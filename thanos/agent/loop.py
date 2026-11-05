"""The agent: user text -> (LLM <-> tools)* -> final reply."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from thanos.agent.parsing import extract_text_tool_calls
from thanos.agent.prompts import build_system_prompt
from thanos.config import Settings
from thanos.llm.base import LLMClient, Message, ToolCall
from thanos.tools.base import ToolResult, truncate_middle
from thanos.tools.registry import ConfirmFn, ToolRegistry

if TYPE_CHECKING:
    from thanos.agent.router import ToolRouter

log = logging.getLogger(__name__)


@dataclass
class ToolEvent:
    name: str
    arguments: dict[str, Any] | str
    result: ToolResult


@dataclass
class AgentReply:
    text: str
    tool_events: list[ToolEvent] = field(default_factory=list)


EventHook = Callable[[ToolEvent], None]

FALLBACK_REPLY_CHARS = 200

DUPLICATE_CALL_MSG = (
    "Not run: you already called {name} with these exact arguments; the result is above. "
    "Use it and do the NEXT step of the task, or call a tool with different arguments."
)


class Agent:
    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        settings: Settings,
        *,
        confirm: ConfirmFn | None = None,
        on_tool_event: EventHook | None = None,
        system_prompt: str | None = None,
        router: ToolRouter | None = None,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.settings = settings
        self.confirm = confirm
        self.on_tool_event = on_tool_event
        self.system_prompt = system_prompt or build_system_prompt(settings)
        self.history: list[Message] = []  # only user/assistant text turns, kept short
        self.router = router
        self.last_routed: list[str] = []  # tool names offered on the last request (debug/evals)
        self._recent_tools: list[str] = []  # tools used last turn, kept for follow-ups

    def reset(self) -> None:
        self.history.clear()
        self._recent_tools = []

    def warm_cache(self) -> None:
        """Process the system prompt + tool list once so Ollama caches it; later requests
        with the same prefix only pay for the new words (big latency win with many tools)."""
        if self.router is not None:
            return  # the tool list changes per request, nothing stable to cache
        tools = self.registry.schemas()
        self.llm.chat([{"role": "system", "content": self.system_prompt}, {"role": "user", "content": "hi"}], tools)

    def select_tools(self, user_text: str, categories: list[str] | None = None) -> list[dict[str, Any]]:
        if categories is None and self.router is not None:
            self.last_routed = self.router.select(user_text, recent=self._recent_tools)
            log.debug("routed tools: %s", self.last_routed)
            return self.registry.schemas_for(self.last_routed)
        tools = self.registry.schemas(categories)
        self.last_routed = [t["function"]["name"] for t in tools]
        return tools

    def run(self, user_text: str, categories: list[str] | None = None) -> AgentReply:
        tools = self.select_tools(user_text, categories)
        messages: list[Message] = [
            {"role": "system", "content": self.system_prompt},
            *self.history,
            {"role": "user", "content": user_text},
        ]
        events: list[ToolEvent] = []
        final: str | None = None
        hit_limit = False
        seen: set[str] = set()  # (tool, args) already run this request: small models loop

        for step in range(self.settings.max_steps):
            resp = self.llm.chat(messages, tools)
            calls = resp.tool_calls or extract_text_tool_calls(resp.content, self.registry.names())
            log.debug("step %d: content=%r calls=%s", step, resp.content, calls)

            if not calls:
                final = resp.content.strip()
                break

            messages.append(_assistant_message(resp.content if resp.tool_calls else "", calls))
            step_events = [self._execute(call, seen) for call in calls]
            events.extend(step_events)
            for ev in step_events:
                content = truncate_middle(
                    ev.result.to_model_text(), self.settings.max_tool_output_chars
                )
                messages.append({"role": "tool", "tool_name": ev.name, "content": content})

            if all(ev.result.ok and self._is_direct(ev.name) for ev in step_events):
                final = " ".join(ev.result.message for ev in step_events)
                break
        else:
            hit_limit = True

        if not final:
            final = _fallback_reply(events, hit_limit)

        self._remember(user_text, final)
        self._recent_tools = list(dict.fromkeys(e.name for e in events if self.registry.get(e.name)))
        return AgentReply(text=final, tool_events=events)

    # --- internals ------------------------------------------------------------
    def _execute(self, call: ToolCall, seen: set[str]) -> ToolEvent:
        key = f"{call.name}:{json.dumps(_as_dict(call.arguments), sort_keys=True)}"
        if key in seen:
            result = ToolResult.fail(DUPLICATE_CALL_MSG.format(name=call.name))
        else:
            seen.add(key)
            result = self.registry.execute(call.name, call.arguments, confirm=self.confirm)
        ev = ToolEvent(name=call.name, arguments=call.arguments, result=result)
        if self.on_tool_event:
            self.on_tool_event(ev)
        return ev

    def _is_direct(self, name: str) -> bool:
        t = self.registry.get(name)
        return bool(t and t.direct)

    def _remember(self, user_text: str, reply: str) -> None:
        self.history += [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": reply},
        ]
        keep = self.settings.history_turns * 2
        if keep <= 0:
            self.history.clear()
        elif len(self.history) > keep:
            self.history = self.history[-keep:]


def _assistant_message(content: str, calls: list[ToolCall]) -> Message:
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {"function": {"name": c.name, "arguments": _as_dict(c.arguments)}} for c in calls
        ],
    }


def _as_dict(args: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(args, dict):
        return args
    try:
        parsed = json.loads(args)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _fallback_reply(events: list[ToolEvent], hit_limit: bool = False) -> str:
    if not events:
        return "Sorry, I couldn't work out how to do that."
    if hit_limit:
        done = ", ".join(dict.fromkeys(e.name for e in events if e.result.ok)) or "nothing"
        return f"I ran out of steps before finishing. Completed: {done}."
    last = events[-1].result
    text = " ".join(last.message.split())
    if len(text) > FALLBACK_REPLY_CHARS:  # never speak a whole web page / shell dump
        text = text[:FALLBACK_REPLY_CHARS] + "…"
    return f"Done. {text}" if last.ok else f"Sorry, that failed: {text}"
