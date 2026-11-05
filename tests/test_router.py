from __future__ import annotations

import pytest

from tests.conftest import FakeLLM, call, say
from thanos.agent import Agent
from thanos.agent.router import ToolRouter
from thanos.config import Settings
from thanos.tools import ToolRegistry, ToolResult, tool

WORDS = ["timer", "weather", "music", "note", "shell"]


class BagEmbedder:
    """Deterministic fake: one dimension per keyword."""

    model = "fake"

    def __init__(self) -> None:
        self.doc_calls = 0

    def _vec(self, text: str) -> list[float]:
        t = text.lower()
        return [float(t.count(w)) + 0.01 for w in WORDS]

    def embed_documents(self, texts):
        self.doc_calls += 1
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


@pytest.fixture
def reg() -> ToolRegistry:
    r = ToolRegistry()
    for name, word in [("set_timer", "timer"), ("get_weather", "weather"), ("play_music", "music"),
                       ("create_note", "note"), ("run_shell", "shell")]:
        def fn() -> ToolResult:
            return ToolResult.success(name)
        fn.__name__ = name
        fn.__doc__ = f"Tool about {word}."
        tool(registry=r, examples=[f"do {word} things"])(fn)
    return r


def _router(reg, tmp_path, embedder=None, **kw) -> ToolRouter:
    s = Settings(router_top_k=1, always_tools=["run_shell"], **kw)
    return ToolRouter(reg, embedder or BagEmbedder(), s, cache_path=tmp_path / "emb.json")


def test_ranks_by_meaning_and_adds_always_tools(reg, tmp_path) -> None:
    r = _router(reg, tmp_path)
    assert r.select("set a timer please") == ["run_shell", "set_timer"]
    assert r.select("weather today") == ["run_shell", "get_weather"]


def test_recent_tools_are_kept_for_follow_ups(reg, tmp_path) -> None:
    r = _router(reg, tmp_path)
    assert r.select("weather", recent=["play_music"]) == ["run_shell", "get_weather", "play_music"]


def test_cache_reused_and_only_changed_tools_reembedded(reg, tmp_path) -> None:
    e1 = BagEmbedder()
    _router(reg, tmp_path, e1).warmup()
    assert e1.doc_calls == 1 and (tmp_path / "emb.json").exists()
    e2 = BagEmbedder()
    _router(reg, tmp_path, e2).warmup()
    assert e2.doc_calls == 0  # all cached


def test_falls_back_to_all_tools_on_error(reg, tmp_path) -> None:
    class Broken(BagEmbedder):
        def embed_query(self, text):
            raise ConnectionError("ollama down")

    r = _router(reg, tmp_path, Broken())
    assert set(r.select("anything")) == set(reg.names())


def test_agent_sends_only_routed_tools_and_remembers_last(reg, tmp_path, settings) -> None:
    r = _router(reg, tmp_path)
    llm = FakeLLM([call("play_music"), say("ok"), call("get_weather"), say("ok")])
    agent = Agent(llm, reg, settings, router=r)
    agent.run("music please")
    assert [t["function"]["name"] for t in llm.tools_sent[0]] == ["run_shell", "play_music"]
    agent.run("weather")  # follow-up still offers the music tool used last turn
    assert [t["function"]["name"] for t in llm.tools_sent[2]] == ["run_shell", "get_weather", "play_music"]
