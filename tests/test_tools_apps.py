from __future__ import annotations

import subprocess
from datetime import datetime

import pytest

from thanos import services
from thanos.tools import apple_apps, media, messaging, websites
from thanos.tools._osa import OSAError
from thanos.tools.messaging import Contact


def fake_osa(monkeypatch, module, reply=""):
    seen: list[str] = []

    def fake(script, timeout=15):
        seen.append(script)
        if isinstance(reply, Exception):
            raise reply
        return reply(script) if callable(reply) else reply

    monkeypatch.setattr(module, "run_applescript", fake)
    return seen


# --- notes / calendar / clipboard ---------------------------------------------

def test_create_note_escapes_html(monkeypatch) -> None:
    seen = fake_osa(monkeypatch, apple_apps)
    assert apple_apps.create_note("Shop <list>", "eggs & milk\nbread").ok
    assert "Shop &lt;list&gt;" in seen[0] and "eggs &amp; milk" in seen[0]


def test_read_note_not_found(monkeypatch) -> None:
    fake_osa(monkeypatch, apple_apps, "NOTFOUND")
    assert not apple_apps.read_note("nothing").ok


def test_calendar_parsing_and_message(monkeypatch) -> None:
    out = "Standup|2026-11-05T09:30:00|false\nHoliday|2026-11-05T00:00:00|true\njunk line\n"
    events = apple_apps.parse_events(out)
    assert [e[0] for e in events] == ["Holiday", "Standup"]
    fake_osa(monkeypatch, apple_apps, out)
    msg = apple_apps.calendar_events("today").message
    assert msg == "2 event(s) today: Holiday; Standup at 9:30 AM."


def test_calendar_empty_and_bad_day(monkeypatch) -> None:
    fake_osa(monkeypatch, apple_apps, "")
    assert apple_apps.calendar_events("tomorrow").message == "Nothing on your calendar tomorrow."
    assert not apple_apps.calendar_events("someday").ok


def test_add_event(monkeypatch) -> None:
    seen = fake_osa(monkeypatch, apple_apps)
    r = apple_apps.add_calendar_event("Dentist", "friday 10 am", 30)
    assert r.ok and "Dentist" in r.message and "summary:\"Dentist\"" in seen[0]


def test_clipboard(monkeypatch) -> None:
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw.get("input")))
        return subprocess.CompletedProcess(cmd, 0, stdout="copied text", stderr="")

    monkeypatch.setattr(apple_apps.subprocess, "run", fake_run)
    assert apple_apps.read_clipboard().message == "copied text"
    apple_apps.copy_to_clipboard("hello")
    assert calls[-1] == (["pbcopy"], "hello")


# --- media ----------------------------------------------------------------------

def test_play_music_not_in_library(monkeypatch) -> None:
    fake_osa(monkeypatch, media, "NOTFOUND")
    r = media.play_music("Kesariya")
    assert not r.ok and "YouTube" in r.message


def test_play_music_ok(monkeypatch) -> None:
    seen = fake_osa(monkeypatch, media, "Kesariya by Arijit Singh")
    assert media.play_music("kesariya").message == "Playing Kesariya by Arijit Singh."
    assert "only songs" in seen[0]
    media.play_music("arijit", kind="artist")
    assert "only artists" in seen[1]


def test_music_control_and_now_playing(monkeypatch) -> None:
    seen = fake_osa(monkeypatch, media, "NOTHING")
    assert media.music_control("next").message == "Next track."
    assert 'to next track' in seen[0]
    assert media.now_playing().message == "Nothing is playing in Music right now."


def test_youtube_control(monkeypatch) -> None:
    monkeypatch.setattr(media, "installed_browsers", lambda: ["Brave Browser", "Safari"])
    fake_osa(monkeypatch, media, lambda s: "NOTAB" if "Brave" in s else "paused")
    assert media.youtube_control("pause").message == "Paused."
    fake_osa(monkeypatch, media, OSAError("Executing JavaScript through AppleScript is turned off"))
    assert "Allow JavaScript from Apple Events" in media.youtube_control("play").message


# --- websites -------------------------------------------------------------------

def test_site_urls() -> None:
    assert websites.site_url("github") == "https://github.com"
    assert websites.site_url("the gmail website") == "https://mail.google.com"
    assert websites.site_url("nytimes.com") == "https://nytimes.com"
    assert websites.site_url("https://x.org/a") == "https://x.org/a"
    assert websites.site_url("hacker news").startswith("https://duckduckgo.com/?q=%21ducky")


def test_maps_and_google(monkeypatch) -> None:
    opened = []
    monkeypatch.setattr(websites, "_open", lambda u: opened.append(u) or True)
    websites.maps_directions("Bangalore airport", "MG Road")
    websites.google_search("best pizza")
    assert opened[0] == ("https://www.google.com/maps/dir/?api=1&destination=Bangalore+airport"
                         "&origin=MG+Road")
    assert opened[1] == "https://www.google.com/search?q=best+pizza"


# --- messaging --------------------------------------------------------------------

@pytest.fixture
def contacts(monkeypatch):
    book = [Contact("Ravi Kumar", "+91 98765 43210", ""), Contact("Ravi Shah", "9876500000", "")]
    monkeypatch.setattr(messaging, "find_contacts", lambda n: [c for c in book if n.lower() in c.name.lower()])
    sent = fake_osa(monkeypatch, messaging)
    yield sent
    services.set_confirmer(None)


def test_imessage_never_sends_without_confirmation(contacts) -> None:
    services.set_confirmer(None)
    r = messaging.send_imessage("Ravi Kumar", "hi")
    assert not r.ok and contacts == []
    services.set_confirmer(lambda p: False)
    assert not messaging.send_imessage("Ravi Kumar", "hi").ok and contacts == []


def test_imessage_sends_after_confirmation(contacts) -> None:
    prompts = []
    services.set_confirmer(lambda p: prompts.append(p) or True)
    assert messaging.send_imessage("ravi kumar", "on my way").ok
    assert prompts == ['Send iMessage to Ravi Kumar (+91 98765 43210): "on my way"?']
    assert 'send "on my way" to participant "+91 98765 43210"' in contacts[0]


def test_ambiguous_and_unknown_contacts(contacts) -> None:
    services.set_confirmer(lambda p: True)
    r = messaging.send_imessage("Ravi", "x")
    assert not r.ok and "Ravi Kumar, Ravi Shah" in r.message
    assert not messaging.send_imessage("Zed", "x").ok


def test_whatsapp_numbers() -> None:
    assert messaging.whatsapp_number("+91 98765 43210", "") == "919876543210"
    assert messaging.whatsapp_number("98765 00000", "IN") == "919876500000"
    assert messaging.whatsapp_number("09876500000", "IN") == "919876500000"
    assert messaging.whatsapp_number("98765 00000", "") is None


def test_whatsapp_opens_prefilled_chat(contacts, monkeypatch) -> None:
    opened = []
    monkeypatch.setattr(messaging.subprocess, "run", lambda cmd, **k: opened.append(cmd))
    monkeypatch.setattr("thanos.tools.info.home_location", lambda: {"country": "IN"})
    services.set_confirmer(lambda p: True)
    r = messaging.send_whatsapp("Ravi Shah", "meeting at 4")
    assert r.ok and opened == [["open", "whatsapp://send?phone=919876500000&text=meeting%20at%204"]]
