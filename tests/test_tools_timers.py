from __future__ import annotations

import threading
import time

import pytest

from thanos import services
from thanos.tools import timers as tm


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    mgr = tm.TimerManager()
    monkeypatch.setattr(tm, "timers", mgr)
    got: list[str] = []
    done = threading.Event()

    def notify(text):
        got.append(text)
        done.set()

    services.set_notifier(notify)
    yield got, done, mgr
    mgr.cancel("all")
    services.set_notifier(None)


def test_timer_fires_notify(fresh) -> None:
    got, done, _ = fresh
    r = tm.set_timer(seconds=0.05, label="pasta")
    assert r.ok and r.message == "Timer set for 0 seconds for pasta."
    assert done.wait(2) and got == ["Your pasta timer is done."]


def test_timer_validation() -> None:
    assert not tm.set_timer().ok
    assert not tm.set_timer(hours=30).ok
    assert tm.set_timer(minutes=1, seconds=30).message == "Timer set for 1 minute 30 seconds."


def test_list_and_cancel(fresh) -> None:
    tm.set_timer(minutes=10, label="tea")
    tm.set_timer(minutes=20)
    assert "tea timer with" in tm.list_timers().message
    assert tm.cancel_timer("tea").message == "Cancelled 1 item."
    assert tm.cancel_timer("all").message == "Cancelled 1 item."
    assert tm.list_timers().message == "No timers or alarms running."


def test_alarm_bad_time() -> None:
    assert not tm.set_alarm("whenever").ok


def test_alarm_ok(fresh) -> None:
    r = tm.set_alarm("11:59 pm")
    assert r.ok and "11:59 PM" in r.message


def test_add_reminder_builds_script(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(tm, "run_applescript", lambda s, timeout=15: seen.append(s) or "")
    r = tm.add_reminder("call mom", "tomorrow at 6 pm")
    assert r.ok and "call mom" in r.message
    assert 'name:"call mom", due date:d' in seen[0] and "set time of d to 64800" in seen[0]
    tm.add_reminder("buy milk")
    assert "due date" not in seen[1]
