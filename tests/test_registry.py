from __future__ import annotations

from typing import Literal

import pytest

from thanos.tools import ToolRegistry, ToolResult, tool


@pytest.fixture
def reg() -> ToolRegistry:
    r = ToolRegistry()

    @tool(registry=r, category="media")
    def set_volume(level: int, mode: Literal["absolute", "relative"] = "absolute") -> ToolResult:
        """Change the system volume.

        Args:
            level: Volume from 0 to 100, e.g. 40.
            mode: Whether level is absolute or a change.
        """
        return ToolResult.success(f"volume {mode} {level}")

    @tool(registry=r, dangerous=True)
    def delete_file(path: str) -> ToolResult:
        """Delete a file.

        Args:
            path: File to delete.
        """
        return ToolResult.success(f"deleted {path}")

    @tool(registry=r)
    def boom() -> str:
        """Always crashes."""
        raise RuntimeError("kaboom")

    @tool(registry=r, name="maybe")
    def _maybe(note: str | None = None) -> str:
        """Optional arg tool."""
        return f"note={note}"

    return r


def test_schema_generation(reg: ToolRegistry) -> None:
    s = reg.get("set_volume").schema()
    fn = s["function"]
    assert s["type"] == "function"
    assert fn["name"] == "set_volume"
    assert fn["description"] == "Change the system volume."
    params = fn["parameters"]
    assert params["type"] == "object"
    assert params["required"] == ["level"]
    assert params["properties"]["level"] == {
        "type": "integer",
        "description": "Volume from 0 to 100, e.g. 40.",
    }
    assert params["properties"]["mode"]["enum"] == ["absolute", "relative"]
    assert "title" not in str(params)


def test_optional_collapses_anyof(reg: ToolRegistry) -> None:
    props = reg.get("maybe").schema()["function"]["parameters"]["properties"]
    assert props["note"]["type"] == "string"
    assert "anyOf" not in props["note"]


def test_execute_valid_with_coercion(reg: ToolRegistry) -> None:
    r = reg.execute("set_volume", {"level": "40"})
    assert r.ok and r.message == "volume absolute 40"


def test_execute_accepts_json_string_and_ignores_extras(reg: ToolRegistry) -> None:
    r = reg.execute("set_volume", '{"level": 10, "mode": "relative", "junk": 1}')
    assert r.ok and r.message == "volume relative 10"


def test_execute_invalid_args_returns_helpful_error(reg: ToolRegistry) -> None:
    r = reg.execute("set_volume", {"mode": "loud"})
    assert not r.ok
    assert "level" in r.message and "Field required" in r.message
    assert "Expected parameters" in r.message


def test_unknown_tool(reg: ToolRegistry) -> None:
    r = reg.execute("nope", {})
    assert not r.ok and "set_volume" in r.message


def test_tool_exception_is_caught(reg: ToolRegistry) -> None:
    r = reg.execute("boom", {})
    assert not r.ok and "kaboom" in r.message


def test_non_toolresult_return_is_wrapped(reg: ToolRegistry) -> None:
    r = reg.execute("maybe", {})
    assert r.ok and r.message == "note=None"


def test_dangerous_requires_confirmation(reg: ToolRegistry) -> None:
    assert not reg.execute("delete_file", {"path": "x"}).ok
    assert not reg.execute("delete_file", {"path": "x"}, confirm=lambda _: False).ok
    assert reg.execute("delete_file", {"path": "x"}, confirm=lambda _: True).ok


def test_duplicate_name_rejected(reg: ToolRegistry) -> None:
    with pytest.raises(ValueError):

        @tool(registry=reg)
        def boom() -> str:  # noqa: F811
            """dup"""
            return ""


def test_missing_type_hint_rejected() -> None:
    with pytest.raises(TypeError):

        @tool(registry=ToolRegistry())
        def bad(x):  # type: ignore[no-untyped-def]
            """no hints"""


def test_category_filter(reg: ToolRegistry) -> None:
    names = [s["function"]["name"] for s in reg.schemas(["media"])]
    assert names == ["set_volume"]


def test_discover_real_tools() -> None:
    from thanos.tools import registry

    registry.discover()
    assert {"play_youtube", "open_youtube_search", "get_current_time"} <= set(registry.names())
