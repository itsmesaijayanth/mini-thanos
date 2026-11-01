"""Ollama implementation of LLMClient."""

from __future__ import annotations

import logging
import re
from typing import Any

import ollama

from thanos.config import Settings
from thanos.llm.base import LLMResponse, Message, ToolCall

log = logging.getLogger(__name__)

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


class OllamaClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.model = settings.model
        self._client = ollama.Client(host=settings.ollama_host)
        self._send_think = True  # disabled automatically for models without thinking support

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "tools": tools or None,
            "options": {
                "temperature": self.settings.temperature,
                "num_ctx": self.settings.num_ctx,
                "num_predict": self.settings.max_reply_tokens,
            },
            "keep_alive": self.settings.keep_alive,
        }
        if self._send_think:
            kwargs["think"] = self.settings.think
        try:
            resp = self._client.chat(**kwargs)
        except ollama.ResponseError as e:
            if "think" in kwargs and "think" in str(e).lower():
                log.info("Model %s does not support `think`; retrying without it", self.model)
                self._send_think = False
                kwargs.pop("think")
                resp = self._client.chat(**kwargs)
            else:
                raise

        msg = resp.message
        calls = [
            ToolCall(name=tc.function.name, arguments=dict(tc.function.arguments or {}))
            for tc in (msg.tool_calls or [])
        ]
        content = _THINK_BLOCK.sub("", msg.content or "").strip()
        return LLMResponse(content=content, tool_calls=calls)

    def warmup(self) -> None:
        """Load the model into memory so the first real request is fast."""
        self._client.generate(model=self.model, prompt="", keep_alive=self.settings.keep_alive)

    def ensure_model(self) -> None:
        """Raise a clear error if Ollama is down or the model is not pulled."""
        try:
            available = {m.model for m in self._client.list().models}
        except Exception as e:  # connection refused etc.
            raise RuntimeError(
                f"Cannot reach Ollama at {self.settings.ollama_host}. "
                "Start it with: brew services start ollama"
            ) from e
        wanted = self.model if ":" in self.model else f"{self.model}:latest"
        if wanted not in available:
            raise RuntimeError(f"Model '{self.model}' not found. Run: ollama pull {self.model}")
