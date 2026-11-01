"""Provider-neutral LLM interface. The agent depends only on this."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

# Messages use the Ollama/OpenAI chat shape:
#   {"role": "system" | "user" | "assistant" | "tool", "content": str, ...}
Message = dict[str, Any]


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any] | str = field(default_factory=dict)


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMClient(Protocol):
    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse: ...
