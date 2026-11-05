"""Tool registry: `@tool` decorator, JSON-schema generation, validation, dispatch.

A tool is a plain, type-hinted Python function. The decorator turns its signature and
docstring into a pydantic model (for validation) and an Ollama/OpenAI tool schema (for the
LLM). Modules inside `thanos.tools` are auto-discovered, so adding a tool never requires
editing this file.
"""

from __future__ import annotations

import importlib
import inspect
import json
import logging
import pkgutil
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, get_type_hints

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from thanos.tools.base import ToolResult

log = logging.getLogger(__name__)

ConfirmFn = Callable[[str], bool]

_INTERNAL_MODULES = {"base", "registry"}


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    func: Callable[..., Any]
    args_model: type[BaseModel]
    category: str = "general"
    dangerous: bool = False
    direct: bool = False  # on success, reply with the tool's message (skip the extra LLM turn)
    examples: tuple[str, ...] = ()  # sample user phrases; used by the router, never sent to the LLM

    def routing_text(self) -> str:
        """What the router embeds: name, description and example phrases."""
        parts = [self.name.replace("_", " "), self.description, *self.examples]
        return "\n".join(p for p in parts if p)

    def schema(self) -> dict[str, Any]:
        params = _clean_schema(self.args_model.model_json_schema())
        params.setdefault("properties", {})
        params.setdefault("required", [])
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": params,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    # --- registration -------------------------------------------------------
    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool

    def discover(self, package: str = "thanos.tools") -> None:
        """Import every module in `package` so their @tool decorators run."""
        pkg = importlib.import_module(package)
        for mod in pkgutil.iter_modules(pkg.__path__):
            if mod.name.startswith("_") or mod.name in _INTERNAL_MODULES:
                continue
            importlib.import_module(f"{package}.{mod.name}")

    # --- lookup -------------------------------------------------------------
    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def tools(self) -> list[Tool]:
        return list(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    def schemas_for(self, names: Iterable[str]) -> list[dict[str, Any]]:
        """Schemas for the named tools (unknown names are skipped), in the given order."""
        return [t.schema() for n in names if (t := self._tools.get(n))]

    def schemas(self, categories: Iterable[str] | None = None) -> list[dict[str, Any]]:
        """Tool schemas for the LLM, optionally limited to some categories."""
        cats = set(categories) if categories is not None else None
        return [
            t.schema()
            for t in self._tools.values()
            if cats is None or t.category in cats
        ]

    # --- execution ----------------------------------------------------------
    def execute(
        self,
        name: str,
        raw_args: dict[str, Any] | str | None,
        confirm: ConfirmFn | None = None,
    ) -> ToolResult:
        """Validate args and run a tool. Never raises: errors become ToolResult.fail."""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.fail(
                f"Unknown tool '{name}'. Available tools: {', '.join(self.names())}."
            )

        try:
            args = _parse_args(raw_args)
            validated = tool.args_model.model_validate(args)
        except (ValidationError, ValueError) as e:
            return ToolResult.fail(
                f"Invalid arguments for '{name}': {_format_error(e)} "
                f"Expected parameters: {json.dumps(tool.schema()['function']['parameters'])}"
            )

        kwargs = validated.model_dump()
        if tool.dangerous:
            prompt = f"Allow {name}({_fmt_kwargs(kwargs)})?"
            if confirm is None or not confirm(prompt):
                return ToolResult.fail("The user declined to run this action.")

        try:
            out = tool.func(**kwargs)
        except Exception as e:  # tools must never crash the agent
            log.exception("Tool %s crashed", name)
            return ToolResult.fail(f"{type(e).__name__}: {e}")

        if isinstance(out, ToolResult):
            return out
        return ToolResult.success(str(out) if out is not None else "Done.")


registry = ToolRegistry()


def tool(
    func: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    category: str = "general",
    dangerous: bool = False,
    direct: bool = False,
    examples: Iterable[str] = (),
    registry: ToolRegistry = registry,
) -> Any:
    """Register a function as an LLM-callable tool.

    category:  group name, used to send only relevant tools to the model.
    dangerous: ask the user before running (shell, delete, write...).
    examples:  sample user phrases ("set a timer for 5 minutes") that help the router
               pick this tool; they are never sent to the LLM.
    direct:    on success the tool's message is the final reply (no extra LLM call).
               Use for fire-and-forget actions; leave False when the model must read
               the result (e.g. summarise a PDF).

    Usage:
        @tool(category="media")
        def play_youtube(query: str) -> ToolResult:
            '''Play a video on YouTube.

            Args:
                query: What to search for, e.g. "believer imagine dragons".
            '''
    """

    def wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
        description, arg_docs = _parse_docstring(inspect.getdoc(fn) or "")
        t = Tool(
            name=name or fn.__name__,
            description=description or fn.__name__.replace("_", " "),
            func=fn,
            args_model=_build_args_model(fn, arg_docs),
            category=category,
            dangerous=dangerous,
            direct=direct,
            examples=tuple(examples),
        )
        registry.register(t)
        fn.__tool__ = t  # type: ignore[attr-defined]
        return fn

    return wrap(func) if func is not None else wrap


# --- helpers ----------------------------------------------------------------

_ARGS_HEADER = re.compile(r"^\s*(Args|Arguments|Parameters):\s*$", re.IGNORECASE)
_ARG_LINE = re.compile(r"^\s*(\w+)\s*(?:\([^)]*\))?\s*:\s*(.*)$")


def _parse_docstring(doc: str) -> tuple[str, dict[str, str]]:
    """Split a Google-style docstring into (description, {arg: description})."""
    desc_lines: list[str] = []
    arg_docs: dict[str, str] = {}
    current: str | None = None
    in_args = False
    for line in doc.splitlines():
        if _ARGS_HEADER.match(line):
            in_args = True
            continue
        if not in_args:
            desc_lines.append(line)
            continue
        m = _ARG_LINE.match(line)
        if m:
            current = m.group(1)
            arg_docs[current] = m.group(2).strip()
        elif current and line.strip():
            arg_docs[current] += " " + line.strip()
    return " ".join(" ".join(desc_lines).split()), arg_docs


def _build_args_model(fn: Callable[..., Any], arg_docs: dict[str, str]) -> type[BaseModel]:
    hints = get_type_hints(fn)
    fields: dict[str, Any] = {}
    for pname, param in inspect.signature(fn).parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            raise TypeError(f"Tool {fn.__name__}: *args/**kwargs are not supported")
        if pname not in hints:
            raise TypeError(f"Tool {fn.__name__}: parameter '{pname}' needs a type hint")
        default = ... if param.default is inspect.Parameter.empty else param.default
        fields[pname] = (hints[pname], Field(default, description=arg_docs.get(pname)))
    return create_model(
        f"{fn.__name__}_args",
        __config__=ConfigDict(extra="ignore", str_strip_whitespace=True),
        **fields,
    )


def _clean_schema(node: Any) -> Any:
    """Make pydantic's JSON schema smaller and friendlier for small models:
    drop titles and collapse Optional (anyOf [X, null]) into X."""
    if isinstance(node, list):
        return [_clean_schema(n) for n in node]
    if not isinstance(node, dict):
        return node
    node = {k: v for k, v in node.items() if k != "title"}
    any_of = node.get("anyOf")
    if isinstance(any_of, list):
        non_null = [s for s in any_of if s.get("type") != "null"]
        if len(non_null) == 1:
            rest = {k: v for k, v in node.items() if k != "anyOf"}
            node = {**non_null[0], **rest}
    return {k: _clean_schema(v) for k, v in node.items()}


def _parse_args(raw: dict[str, Any] | str | None) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, str):
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("arguments must be a JSON object")
        return parsed
    return dict(raw)


def _format_error(e: Exception) -> str:
    if isinstance(e, ValidationError):
        return "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'arguments'}: {err['msg']}"
            for err in e.errors()
        ) + "."
    return str(e)


def _fmt_kwargs(kwargs: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in kwargs.items())
