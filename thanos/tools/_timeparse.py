"""Deterministic parsing of spoken times/dates ("in 20 minutes", "7:30 pm", "tomorrow at 9")."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600}
_REL = re.compile(
    r"^(?:in\s+)?(\d+(?:\.\d+)?)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)"
    r"(?:\s*(?:and\s+)?(\d+)\s*(minutes?|mins?|m|seconds?|secs?|s))?(?:\s+from now)?$"
)
_TIME = re.compile(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?(?=\s|$)")


class TimeParseError(ValueError):
    pass


def _unit(word: str) -> int:
    return _UNIT_SECONDS[word[0]]


def parse_duration(text: str) -> timedelta | None:
    """'20 minutes', 'in 1 hour and 5 minutes', '90s' -> timedelta, else None."""
    m = _REL.match(text.strip().lower())
    if not m:
        return None
    secs = float(m.group(1)) * _unit(m.group(2))
    if m.group(3):
        secs += int(m.group(3)) * _unit(m.group(4))
    return timedelta(seconds=secs)


def parse_day(text: str, today: date | None = None) -> date | None:
    """'today', 'tomorrow', 'monday', '2026-11-07', '7 nov' -> date (or None if no day words)."""
    today = today or date.today()
    t = text.lower()
    if "day after tomorrow" in t:
        return today + timedelta(days=2)
    if "tomorrow" in t:
        return today + timedelta(days=1)
    if "today" in t or "tonight" in t:
        return today
    for i, name in enumerate(WEEKDAYS):
        if re.search(rf"\b{name}\b", t):
            ahead = (i - today.weekday()) % 7 or 7
            return today + timedelta(days=ahead)
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    for fmt in ("%d %b", "%d %B", "%b %d", "%B %d"):
        for token in re.findall(r"\b\d{1,2} [a-z]+\b|\b[a-z]+ \d{1,2}\b", t):
            try:
                d = datetime.strptime(token, fmt).date().replace(year=today.year)
            except ValueError:
                continue
            return d if d >= today else d.replace(year=today.year + 1)
    return None


def parse_when(text: str, now: datetime | None = None, default_hour: int = 9) -> datetime:
    """A spoken moment -> datetime. Times alone that already passed today mean tomorrow."""
    now = now or datetime.now()
    t = " ".join(text.lower().replace("o'clock", "").split())
    if not t:
        raise TimeParseError("empty time")
    dur = parse_duration(t)
    if dur is not None:
        return now + dur
    day = parse_day(t, now.date())
    hour = minute = None
    if "noon" in t or "midday" in t:
        hour, minute = 12, 0
    elif "midnight" in t:
        hour, minute = 0, 0
    else:
        stripped = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", t)
        for m in _TIME.finditer(stripped):
            h, mm, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(".", "")
            if ap == "pm" and h < 12:
                h += 12
            elif ap == "am" and h == 12:
                h = 0
            elif not ap and h < 12 and ("evening" in t or "night" in t or "afternoon" in t):
                h += 12
            if h <= 23 and mm <= 59 and (ap or m.group(2) or "at" in t or day is None):
                hour, minute = h, mm
                break
    if hour is None:
        if day is None:
            raise TimeParseError(f"couldn't understand the time '{text}'")
        hour, minute = default_hour, 0
    result = datetime.combine(day or now.date(), datetime.min.time()).replace(hour=hour, minute=minute)
    if day is None and result <= now:
        result += timedelta(days=1)
    return result


def say_time(dt: datetime, now: datetime | None = None) -> str:
    """Friendly spoken form: '7:30 PM', 'tomorrow at 9:00 AM', 'Monday 7 Nov at 9:00 AM'."""
    now = now or datetime.now()
    clock = f"{dt.hour % 12 or 12}:{dt:%M %p}"
    days = (dt.date() - now.date()).days
    if days == 0:
        return clock
    if days == 1:
        return f"tomorrow at {clock}"
    return f"{dt:%A} {dt.day} {dt:%b} at {clock}"
