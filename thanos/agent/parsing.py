"""Fallback: recover tool calls that a small model wrote as text instead of structured calls.

Some local models occasionally answer with `{"name": "play_youtube", "arguments": {...}}`
(sometimes wrapped in ```json fences or <tool_call> tags) in the message content.
We only accept names that are real registered tools.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from thanos.llm.base import ToolCall

_DECODER = json.JSONDecoder()


def extract_text_tool_calls(content: str, known_tools: Iterable[str]) -> list[ToolCall]:
    if not content or "{" not in content:
        return []
    known = set(known_tools)
    calls: list[ToolCall] = []
    i = content.find("{")
    while i != -1:
        try:
            obj, end = _DECODER.raw_decode(content, i)
        except json.JSONDecodeError:
            i = content.find("{", i + 1)
            continue
        for item in obj if isinstance(obj, list) else [obj]:
            call = _to_call(item, known)
            if call:
                calls.append(call)
        i = content.find("{", end)
    return calls


def _to_call(obj: Any, known: set[str]) -> ToolCall | None:
    if not isinstance(obj, dict):
        return None
    if isinstance(obj.get("function"), dict):  # OpenAI-style {"function": {...}}
        obj = obj["function"]
    name = obj.get("name") or obj.get("tool")
    if name not in known:
        return None
    args = obj.get("arguments", obj.get("parameters", {}))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return None
    return ToolCall(name=name, arguments=args if isinstance(args, dict) else {})
