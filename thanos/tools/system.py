"""Basic system info tools."""

from __future__ import annotations

from datetime import datetime

from thanos.tools.base import ToolResult
from thanos.tools.registry import tool


@tool(category="system", direct=True)
def get_current_time() -> ToolResult:
    """Get the current local date and time. Use when the user asks the time, date, or day."""
    now = datetime.now().astimezone()
    return ToolResult.success(
        f"It is {now.hour % 12 or 12}:{now:%M %p} on {now:%A}, {now.day} {now:%B %Y}.",
        iso=now.isoformat(timespec="seconds"),
    )
