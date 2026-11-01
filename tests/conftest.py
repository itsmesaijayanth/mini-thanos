from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pytest

from thanos.config import Settings
from thanos.llm.base import LLMResponse, Message, ToolCall


class FakeLLM:
    """Returns scripted responses in order and records what it was sent."""

    def __init__(self, responses: Iterable[LLMResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[list[Message]] = []
        self.tools_sent: list[list[dict[str, Any]]] = []

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        self.calls.append([dict(m) for m in messages])
        self.tools_sent.append(tools)
        if not self.responses:
            return LLMResponse(content="(no more scripted responses)")
        return self.responses.pop(0)


def call(name: str, **args: Any) -> LLMResponse:
    return LLMResponse(tool_calls=[ToolCall(name=name, arguments=args)])


def say(text: str) -> LLMResponse:
    return LLMResponse(content=text)


@pytest.fixture
def settings() -> Settings:
    return Settings(max_steps=4, history_turns=2, tts=False)
