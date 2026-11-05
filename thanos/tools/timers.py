"""Timers and alarms (alert while Thanos runs) and Reminders-app reminders (persist)."""

from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from thanos import services
from thanos.tools._osa import OSAError, applescript_date, q, run_applescript
from thanos.tools._timeparse import TimeParseError, parse_when, say_time
from thanos.tools.base import ToolResult
from thanos.tools.registry import tool


@dataclass
class _Timer:
    id: int
    kind: str  # "timer" | "alarm"
    label: str
    fire_at: datetime
    handle: threading.Timer


class TimerManager:
    def __init__(self) -> None:
        self._timers: dict[int, _Timer] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def add(self, kind: str, label: str, fire_at: datetime, message: str) -> _Timer:
        tid = next(self._ids)
        delay = max(0.0, (fire_at - datetime.now()).total_seconds())
        handle = threading.Timer(delay, self._fire, args=(tid, message))
        handle.daemon = True
        t = _Timer(tid, kind, label, fire_at, handle)
        with self._lock:
            self._timers[tid] = t
        handle.start()
        return t

    def _fire(self, tid: int, message: str) -> None:
        with self._lock:
            self._timers.pop(tid, None)
        services.notify(message)

    def active(self) -> list[_Timer]:
        with self._lock:
            return sorted(self._timers.values(), key=lambda t: t.fire_at)

    def cancel(self, which: str) -> list[_Timer]:
        which = which.strip().lower()
        with self._lock:
            targets = [
                t
                for t in self._timers.values()
                if which in ("all", "", "everything")
                or which == str(t.id)
                or (t.label and which in t.label.lower())
                or which == t.kind
            ]
            for t in targets:
                t.handle.cancel()
                self._timers.pop(t.id, None)
        return targets


timers = TimerManager()


def _say_duration(td: timedelta) -> str:
    secs = int(round(td.total_seconds()))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    parts = [f"{h} hour{'s' * (h != 1)}" if h else "", f"{m} minute{'s' * (m != 1)}" if m else "",
             f"{s} second{'s' * (s != 1)}" if s else ""]
    return " ".join(p for p in parts if p) or "0 seconds"


@tool(
    category="time",
    direct=True,
    examples=["set a timer for 10 minutes", "timer 30 seconds", "remind me in 5 minutes to check the oven",
              "start a 1 hour timer for laundry"],
)
def set_timer(minutes: int = 0, seconds: int = 0, hours: int = 0, label: str = "") -> ToolResult:
    """Start a countdown timer; Thanos speaks and shows a notification when it ends.

    Args:
        minutes: Minutes, e.g. 10.
        seconds: Extra seconds, e.g. 30.
        hours: Hours, e.g. 1.
        label: Optional name, e.g. "pasta".
    """
    td = timedelta(hours=hours, minutes=minutes, seconds=seconds)
    if td.total_seconds() <= 0:
        return ToolResult.fail("Timer length must be more than zero; ask how long.")
    if td > timedelta(hours=24):
        return ToolResult.fail("Timers can be at most 24 hours; use add_reminder for longer.")
    length = _say_duration(td)
    done = f"Your {label} timer is done." if label else f"Your {length} timer is done."
    timers.add("timer", label, datetime.now() + td, done)
    return ToolResult.success(f"Timer set for {length}" + (f" for {label}." if label else "."))


@tool(
    category="time",
    direct=True,
    examples=["wake me up at 7 am", "set an alarm for 6:30", "alarm at 5 pm tomorrow"],
)
def set_alarm(time: str, label: str = "") -> ToolResult:
    """Set an alarm for a clock time; Thanos speaks and shows a notification then.

    Args:
        time: When, as said, e.g. "7:30 am", "18:00", "tomorrow at 6".
        label: Optional name, e.g. "gym".
    """
    try:
        when = parse_when(time)
    except TimeParseError as e:
        return ToolResult.fail(f"{e}. Ask for a time like '7:30 am'.")
    msg = f"Alarm: {label}." if label else f"It's {say_time(when, when)}. Your alarm."
    timers.add("alarm", label, when, msg)
    return ToolResult.success(f"Alarm set for {say_time(when)}.")


@tool(category="time", direct=True, examples=["what timers do I have", "how long is left on my timer"])
def list_timers() -> ToolResult:
    """List running timers and alarms with the time left."""
    active = timers.active()
    if not active:
        return ToolResult.success("No timers or alarms running.")
    now = datetime.now()
    parts = []
    for t in active:
        name = f"{t.label} {t.kind}" if t.label else t.kind
        if t.kind == "timer":
            parts.append(f"{name} with {_say_duration(t.fire_at - now)} left")
        else:
            parts.append(f"{name} at {say_time(t.fire_at, now)}")
    return ToolResult.success("You have " + "; ".join(parts) + ".")


@tool(category="time", direct=True, examples=["cancel my timer", "stop the alarm", "cancel all timers"])
def cancel_timer(which: str = "all") -> ToolResult:
    """Cancel timers/alarms.

    Args:
        which: "all", "timer", "alarm", or the label, e.g. "pasta". Default "all".
    """
    gone = timers.cancel(which)
    if not gone:
        return ToolResult.success("There was nothing to cancel.")
    return ToolResult.success(f"Cancelled {len(gone)} {'item' if len(gone) == 1 else 'items'}.")


@tool(
    category="time",
    direct=True,
    examples=["remind me to call mom tomorrow at 6 pm", "add a reminder to pay rent on friday",
              "add buy milk to my reminders"],
)
def add_reminder(text: str, due: str = "") -> ToolResult:
    """Add a to-do reminder to the Apple Reminders app (syncs to iPhone): "remind me to ...".
    Not for meetings/calls with people at a time (use add_calendar_event).

    Args:
        text: What to remember, e.g. "call mom".
        due: When, optional, e.g. "tomorrow at 6 pm", "friday", "in 2 hours". Empty = no date.
    """
    props = f"name:{q(text)}"
    script = ""
    when = None
    if due.strip():
        try:
            when = parse_when(due)
        except TimeParseError as e:
            return ToolResult.fail(f"{e}. Ask when, e.g. 'tomorrow at 6 pm'.")
        script += applescript_date("d", when)
        props += ", due date:d, remind me date:d"
    script += f'tell application "Reminders" to make new reminder with properties {{{props}}}'
    try:
        run_applescript(script)
    except OSAError as e:
        return ToolResult.fail(str(e))
    return ToolResult.success(
        f"Reminder added: {text}" + (f", {say_time(when)}." if when else ".")
    )
