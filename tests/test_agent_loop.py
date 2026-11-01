from __future__ import annotations

import pytest

from tests.conftest import FakeLLM, call, say
from thanos.agent import Agent
from thanos.llm.base import LLMResponse
from thanos.tools import ToolRegistry, ToolResult, tool


@pytest.fixture
def reg() -> ToolRegistry:
    r = ToolRegistry()

    @tool(registry=r, direct=True)
    def play(query: str) -> ToolResult:
        """Play something.

        Args:
            query: what to play
        """
        return ToolResult.success(f"Playing {query}.")

    @tool(registry=r)
    def lookup(key: str) -> ToolResult:
        """Look something up (result must be read by the model).

        Args:
            key: key
        """
        return ToolResult.success("value-42")

    return r


def test_plain_answer_without_tools(reg, settings) -> None:
    llm = FakeLLM([say("Hello!")])
    reply = Agent(llm, reg, settings).run("hi")
    assert reply.text == "Hello!" and reply.tool_events == []


def test_direct_tool_skips_second_llm_call(reg, settings) -> None:
    llm = FakeLLM([call("play", query="believer")])
    reply = Agent(llm, reg, settings).run("play believer")
    assert reply.text == "Playing believer."
    assert len(llm.calls) == 1


def test_non_direct_tool_feeds_result_back(reg, settings) -> None:
    llm = FakeLLM([call("lookup", key="x"), say("The value is 42.")])
    reply = Agent(llm, reg, settings).run("look up x")
    assert reply.text == "The value is 42."
    second = llm.calls[1]
    assert second[-2]["role"] == "assistant" and second[-2]["tool_calls"]
    assert second[-1]["role"] == "tool" and "value-42" in second[-1]["content"]


def test_invalid_args_then_self_correction(reg, settings) -> None:
    llm = FakeLLM([call("play"), call("play", query="lofi")])
    reply = Agent(llm, reg, settings).run("play lofi")
    assert reply.text == "Playing lofi."
    assert [e.result.ok for e in reply.tool_events] == [False, True]
    assert "Field required" in llm.calls[1][-1]["content"]


def test_unknown_tool_then_recovery(reg, settings) -> None:
    llm = FakeLLM([call("play_music", query="x"), call("play", query="x")])
    reply = Agent(llm, reg, settings).run("play x")
    assert reply.text == "Playing x."


def test_text_tool_call_fallback(reg, settings) -> None:
    llm = FakeLLM(
        [LLMResponse(content='```json\n{"name": "play", "arguments": {"query": "jazz"}}\n```')]
    )
    reply = Agent(llm, reg, settings).run("play jazz")
    assert reply.text == "Playing jazz."


def test_duplicate_call_not_rerun(reg, settings) -> None:
    llm = FakeLLM([call("lookup", key="x"), call("lookup", key="x"), say("done")])
    reply = Agent(llm, reg, settings).run("look up x")
    assert [e.result.ok for e in reply.tool_events] == [True, False]
    assert "already called lookup" in llm.calls[2][-1]["content"]


def test_max_steps_is_bounded(reg, settings) -> None:
    llm = FakeLLM([call("lookup", key=str(i)) for i in range(10)])
    reply = Agent(llm, reg, settings).run("loop forever")
    assert len(llm.calls) == settings.max_steps
    assert reply.text == "I ran out of steps before finishing. Completed: lookup."


def test_history_is_kept_and_trimmed(reg, settings) -> None:
    llm = FakeLLM([say("a"), say("b"), say("c")])
    agent = Agent(llm, reg, settings)
    for t in ["1", "2", "3"]:
        agent.run(t)
    assert len(agent.history) == settings.history_turns * 2
    assert agent.history[0]["content"] == "2"
    assert [m["content"] for m in llm.calls[2][1:]] == ["1", "a", "2", "b", "3"]


def test_multi_step_chain(settings) -> None:
    r = ToolRegistry()
    log: list[str] = []

    @tool(registry=r)
    def search(q: str) -> ToolResult:
        """Search. Args: q: query"""
        log.append("search")
        return ToolResult.success("1. Page A — https://x/a")

    @tool(registry=r)
    def fetch(url: str) -> ToolResult:
        """Fetch. Args: url: url"""
        log.append("fetch")
        return ToolResult.success("text of A")

    @tool(registry=r)
    def save(path: str, content: str) -> ToolResult:
        """Save. Args: path: p  content: c"""
        log.append(f"save:{path}:{content}")
        return ToolResult.success(f"Saved {path}")

    llm = FakeLLM(
        [
            call("search", q="popular songs"),
            call("fetch", url="https://x/a"),
            call("save", path="a.txt", content="text of A"),
            say("Saved the text of page A."),
        ]
    )
    reply = Agent(llm, r, settings.model_copy(update={"max_steps": 10})).run("save page A")
    assert log == ["search", "fetch", "save:a.txt:text of A"]
    assert reply.text == "Saved the text of page A."


def test_tool_output_is_truncated_for_model(settings) -> None:
    r = ToolRegistry()

    @tool(registry=r)
    def big() -> ToolResult:
        """Return a lot of text."""
        return ToolResult.success("x" * 50_000)

    llm = FakeLLM([call("big"), say("ok")])
    Agent(llm, r, settings.model_copy(update={"max_tool_output_chars": 500})).run("go")
    tool_msg = llm.calls[1][-1]
    assert tool_msg["role"] == "tool" and len(tool_msg["content"]) <= 500
    assert "chars trimmed" in tool_msg["content"]


def test_empty_final_answer_falls_back_to_short_summary(reg, settings) -> None:
    r = ToolRegistry()

    @tool(registry=r)
    def page() -> ToolResult:
        """Return a long page."""
        return ToolResult.success("word " * 2000)

    reply = Agent(FakeLLM([call("page"), say("")]), r, settings).run("go")
    assert reply.text.startswith("Done. word") and len(reply.text) < 220
