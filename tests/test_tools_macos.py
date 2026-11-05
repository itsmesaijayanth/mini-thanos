from __future__ import annotations

import subprocess

import pytest

from thanos.tools import _osa, macos
from thanos.tools._osa import OSAError, explain_osa_error, q


@pytest.fixture
def osa(monkeypatch):
    scripts: list[str] = []
    replies: dict[str, str] = {}

    def fake(script, timeout=15):
        scripts.append(script)
        for key, val in replies.items():
            if key in script:
                if isinstance(val, Exception):
                    raise val
                return val
        return ""

    monkeypatch.setattr(macos, "run_applescript", fake)
    fake.scripts, fake.replies = scripts, replies
    return fake


@pytest.fixture
def cmds(monkeypatch):
    calls: list[list[str]] = []

    def fake(cmd, timeout=15):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, fake.rc, stdout=fake.stdout, stderr="")

    fake.rc, fake.stdout, fake.calls = 0, "", calls
    monkeypatch.setattr(macos, "run", fake)
    return fake


def test_set_volume_clamps(osa) -> None:
    assert macos.set_volume(150).message == "Volume set to 100 percent."
    assert "set volume output volume 100" in osa.scripts[-1]


def test_change_volume_steps_from_current(osa) -> None:
    osa.replies["get volume settings"] = "45"
    r = macos.change_volume("down")
    assert r.ok and "35" in r.message and "output volume 35" in osa.scripts[-1]


def test_permission_error_is_friendly(osa) -> None:
    osa.replies["set volume"] = OSAError(explain_osa_error("execution error: Not authorized to send Apple events (-1743)"))
    r = macos.set_volume(10)
    assert not r.ok and "Privacy & Security → Automation" in r.message


def test_brightness_uses_key_codes(osa) -> None:
    macos.change_brightness("up", steps=3)
    assert "repeat 3 times" in osa.scripts[-1] and "key code 144" in osa.scripts[-1]


def test_open_app_missing(cmds) -> None:
    cmds.rc = 1
    assert not macos.open_app("Nope").ok
    assert cmds.calls[-1] == ["open", "-a", "Nope"]


def test_quit_app_not_running(osa) -> None:
    osa.replies["is running"] = "false"
    assert macos.quit_app("Slack").message == "Slack isn't running."


def test_battery_parsing() -> None:
    out = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t76%; discharging; 4:12 remaining present: true"
    assert macos.parse_battery(out).message == "Battery is at 76 percent, about 4 hours 12 minutes left."
    out = " -InternalBattery-0 (id=1)\t40%; charging; 0:45 remaining present: true"
    assert macos.parse_battery(out).message == "Battery is at 40 percent, charging, about 45 minutes to full."
    out = " -InternalBattery-0 (id=1)\t100%; charged; 0:00 remaining present: true"
    assert macos.parse_battery(out).message == "Battery is at 100 percent, fully charged."
    assert not macos.parse_battery("Now drawing from 'AC Power'").ok
    out = " -InternalBattery-0 (id=1)\t5%; discharging; 17:11 remaining present: true"
    assert macos.parse_battery(out).message == "Battery is low, at 5 percent. Plug in soon."


def test_quote_escapes() -> None:
    assert q('say "hi" \\ there') == '"say \\"hi\\" \\\\ there"'


def test_osa_error_messages() -> None:
    assert "Accessibility" in _osa.explain_osa_error("System Events got an error: not allowed assistive access. (-1719)")
    assert _osa.explain_osa_error("execution error: Something broke (-1)") == "Something broke (-1)"
