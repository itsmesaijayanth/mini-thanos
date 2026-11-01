"""Result type every tool returns."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    ok: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, message: str, **data: Any) -> ToolResult:
        return cls(ok=True, message=message, data=data)

    @classmethod
    def fail(cls, message: str, **data: Any) -> ToolResult:
        return cls(ok=False, message=message, data=data)

    def to_model_text(self) -> str:
        """Compact text fed back to the LLM as the tool message."""
        payload: dict[str, Any] = {"ok": self.ok, "message": self.message}
        if self.data:
            payload["data"] = self.data
        return json.dumps(payload, ensure_ascii=False, default=str)


def truncate_middle(text: str, limit: int) -> str:
    """Trim long text to `limit` chars, keeping the start and the end (errors often live there)."""
    if limit <= 0 or len(text) <= limit:
        return text
    marker = f"\n…[{len(text) - limit} chars trimmed]…\n"
    keep = max(limit - len(marker), 0)
    head = keep * 2 // 3
    return text[:head] + marker + text[len(text) - (keep - head):]
