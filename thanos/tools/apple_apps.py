"""Apple Notes, Calendar and the clipboard."""

from __future__ import annotations

import html
import subprocess
from datetime import datetime, timedelta

from thanos.config import get_settings
from thanos.tools._osa import OSAError, applescript_date, q, run_applescript
from thanos.tools._timeparse import TimeParseError, parse_day, parse_when, say_time
from thanos.tools.base import ToolResult, truncate_middle
from thanos.tools.registry import tool

# --- Notes --------------------------------------------------------------------


@tool(
    category="notes",
    direct=True,
    examples=["make a note called groceries with eggs and bread", "take a note: call the bank",
              "save this in notes"],
)
def create_note(title: str, body: str = "") -> ToolResult:
    """Create a new note in Apple Notes.

    Args:
        title: Note title, e.g. "Groceries".
        body: Note text, e.g. "eggs, bread, milk".
    """
    lines = [f"<div><b>{html.escape(title)}</b></div>"]
    lines += [f"<div>{html.escape(line) or '<br>'}</div>" for line in body.splitlines()]
    script = f'tell application "Notes" to make new note with properties {{body:{q("".join(lines))}}}'
    try:
        run_applescript(script)
    except OSAError as e:
        return ToolResult.fail(str(e))
    return ToolResult.success(f"Note '{title}' created.")


@tool(category="notes", direct=True, examples=["find my notes about the trip", "do I have a note on passwords"])
def find_notes(query: str) -> ToolResult:
    """List Apple Notes whose title matches (titles only). To read a note's text use read_note.

    Args:
        query: Words in the title, e.g. "trip".
    """
    script = f'''tell application "Notes"
set ns to (every note whose name contains {q(query)})
set out to ""
repeat with n in ns
set out to out & (name of n) & linefeed
end repeat
return out
end tell'''
    try:
        out = run_applescript(script, timeout=30)
    except OSAError as e:
        return ToolResult.fail(str(e))
    titles = [t for t in out.splitlines() if t.strip()][:10]
    if not titles:
        return ToolResult.success(f"No notes found with '{query}' in the title.")
    return ToolResult.success(f"Found {len(titles)} note(s): " + ", ".join(titles) + ".")


@tool(category="notes", examples=["read my groceries note", "what's in my note about the trip"])
def read_note(title: str) -> ToolResult:
    """Read the full text of an Apple Note: "read my X note", "what's in my X note".
    (find_notes only lists titles.)

    Args:
        title: Words from the note title, e.g. "groceries".
    """
    script = f'''tell application "Notes"
set ns to (every note whose name contains {q(title)})
if (count of ns) = 0 then return "NOTFOUND"
return plaintext of item 1 of ns
end tell'''
    try:
        out = run_applescript(script, timeout=30)
    except OSAError as e:
        return ToolResult.fail(str(e))
    if out == "NOTFOUND":
        return ToolResult.fail(f"No note with '{title}' in the title.")
    return ToolResult.success(truncate_middle(out, get_settings().max_tool_output_chars))


# --- Calendar -----------------------------------------------------------------


def _events_script(start: datetime, end: datetime) -> str:
    return (
        applescript_date("d0", start)
        + applescript_date("d1", end)
        + '''set out to ""
tell application "Calendar"
repeat with c in calendars
set evs to (every event of c whose start date ≥ d0 and start date < d1)
repeat with e in evs
set out to out & (summary of e) & "|" & ((start date of e) as «class isot» as string) & "|" & (allday event of e) & linefeed
end repeat
end repeat
end tell
return out'''
    )


def parse_events(out: str) -> list[tuple[str, datetime, bool]]:
    events = []
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) != 3:
            continue
        title, iso, allday = parts
        try:
            events.append((title.strip(), datetime.fromisoformat(iso.strip()), allday.strip() == "true"))
        except ValueError:
            continue
    return sorted(events, key=lambda e: e[1])


@tool(
    category="calendar",
    direct=True,
    examples=["what's on my calendar today", "do I have meetings tomorrow", "my schedule for friday"],
)
def calendar_events(day: str = "today") -> ToolResult:
    """List Calendar events for a day.

    Args:
        day: "today", "tomorrow", a weekday like "friday", or a date like "2026-11-07".
    """
    d = parse_day(day)
    if d is None:
        return ToolResult.fail(f"Couldn't understand the day '{day}'.")
    start = datetime.combine(d, datetime.min.time())
    try:
        out = run_applescript(_events_script(start, start + timedelta(days=1)), timeout=60)
    except OSAError as e:
        return ToolResult.fail(str(e))
    events = parse_events(out)
    label = day.strip() if day.strip().lower() in ("today", "tomorrow") else f"on {d:%A %d %B}"
    if not events:
        return ToolResult.success(f"Nothing on your calendar {label}.")
    items = [title if allday else f"{title} at {e.hour % 12 or 12}:{e:%M %p}" for title, e, allday in events]
    return ToolResult.success(f"{len(items)} event(s) {label}: " + "; ".join(items) + ".")


@tool(
    category="calendar",
    direct=True,
    examples=["add a meeting with Ravi tomorrow at 3 pm", "schedule dentist on friday at 10 am",
              "put lunch with Priya on my calendar at 1 pm"],
)
def add_calendar_event(title: str, start: str, duration_minutes: int = 60) -> ToolResult:
    """Add an event (meeting, call, appointment) at a specific time to the Calendar app.
    Use for "schedule"/"book"/"add a meeting". For to-dos use add_reminder.

    Args:
        title: Event name, e.g. "Meeting with Ravi".
        start: When it starts, as said, e.g. "tomorrow at 3 pm", "friday 10 am".
        duration_minutes: Length in minutes. Default 60.
    """
    try:
        when = parse_when(start)
    except TimeParseError as e:
        return ToolResult.fail(f"{e}. Ask when it starts.")
    end = when + timedelta(minutes=max(5, duration_minutes))
    script = (
        applescript_date("d0", when)
        + applescript_date("d1", end)
        + f'''tell application "Calendar"
set c to first calendar whose writable is true
make new event at end of events of c with properties {{summary:{q(title)}, start date:d0, end date:d1}}
end tell'''
    )
    try:
        run_applescript(script, timeout=30)
    except OSAError as e:
        return ToolResult.fail(str(e))
    return ToolResult.success(f"Added {title}, {say_time(when)}.")


# --- Clipboard ----------------------------------------------------------------


@tool(category="clipboard", examples=["what's in my clipboard", "read what I copied", "summarize what I copied"])
def read_clipboard() -> ToolResult:
    """Get the text currently copied to the clipboard."""
    text = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout
    if not text.strip():
        return ToolResult.success("The clipboard is empty.")
    return ToolResult.success(truncate_middle(text, get_settings().max_tool_output_chars))


@tool(category="clipboard", direct=True, examples=["copy this to clipboard", "copy my email address"])
def copy_to_clipboard(text: str) -> ToolResult:
    """Copy text to the clipboard so the user can paste it.

    Args:
        text: The exact text to copy.
    """
    subprocess.run(["pbcopy"], input=text, text=True, check=False)
    return ToolResult.success("Copied to clipboard.")
