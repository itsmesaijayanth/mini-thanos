from datetime import date, datetime, timedelta

import pytest

from thanos.tools._timeparse import TimeParseError, parse_day, parse_duration, parse_when, say_time

NOW = datetime(2026, 11, 5, 14, 0)  # Thursday 2 PM


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("in 20 minutes", NOW + timedelta(minutes=20)),
        ("1 hour and 5 minutes", NOW + timedelta(hours=1, minutes=5)),
        ("90 seconds", NOW + timedelta(seconds=90)),
        ("7:30 pm", datetime(2026, 11, 5, 19, 30)),
        ("7:30 am", datetime(2026, 11, 6, 7, 30)),  # already passed today -> tomorrow
        ("18:00", datetime(2026, 11, 5, 18, 0)),
        ("tomorrow at 9", datetime(2026, 11, 6, 9, 0)),
        ("tomorrow", datetime(2026, 11, 6, 9, 0)),
        ("friday 10 am", datetime(2026, 11, 6, 10, 0)),
        ("monday at 3 pm", datetime(2026, 11, 9, 15, 0)),
        ("noon", datetime(2026, 11, 6, 12, 0)),
        ("6 o'clock in the evening", datetime(2026, 11, 5, 18, 0)),
        ("2026-11-20 at 4:15 pm", datetime(2026, 11, 20, 16, 15)),
    ],
)
def test_parse_when(text, expected) -> None:
    assert parse_when(text, NOW) == expected


def test_parse_when_garbage() -> None:
    with pytest.raises(TimeParseError):
        parse_when("whenever", NOW)


def test_parse_day_and_duration() -> None:
    today = date(2026, 11, 5)
    assert parse_day("today", today) == today
    assert parse_day("thursday", today) == date(2026, 11, 12)  # next week's
    assert parse_day("7 dec", today) == date(2026, 12, 7)
    assert parse_day("hello", today) is None
    assert parse_duration("5 min") == timedelta(minutes=5)
    assert parse_duration("tomorrow") is None


def test_say_time() -> None:
    assert say_time(datetime(2026, 11, 5, 19, 30), NOW) == "7:30 PM"
    assert say_time(datetime(2026, 11, 6, 9, 0), NOW) == "tomorrow at 9:00 AM"
