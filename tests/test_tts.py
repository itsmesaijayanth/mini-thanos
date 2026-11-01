from __future__ import annotations

import threading
import time

import numpy as np

from thanos.config import Settings
from thanos.voice import tts
from thanos.voice.tts import NullSpeaker, Speaker, clean_for_speech, split_sentences


class FakeEngine:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def synthesize(self, text: str):
        self.texts.append(text)
        return np.zeros(10, dtype=np.float32), 24000


class FakePlayer:
    """Each play() 'lasts' `seconds` unless stop() is called."""

    def __init__(self, seconds: float = 0.0) -> None:
        self.seconds = seconds
        self.played = 0
        self._stopped = threading.Event()

    def play(self, audio, sample_rate) -> None:
        self.played += 1
        self._stopped.wait(self.seconds)

    def stop(self) -> None:
        self._stopped.set()


def test_clean_removes_urls_markdown_emoji() -> None:
    text = "**Playing** `Believer` 🎵 now — https://youtube.com/watch?v=1 #music"
    assert clean_for_speech(text) == "Playing Believer now — music"  # noqa: RUF001


def test_clean_caps_at_sentence_boundary() -> None:
    text = "First sentence here. " + "Second one is longer. " * 30
    out = clean_for_speech(text, max_chars=60)
    assert out.startswith("First sentence here. Second one is longer.")
    assert out.endswith("Details are on screen.")
    assert len(out) <= 60 + len(" Details are on screen.")


def test_clean_single_giant_sentence() -> None:
    out = clean_for_speech("word " * 200, max_chars=50)
    assert out.endswith("Details are on screen.") and len(out) < 80


def test_clean_empty() -> None:
    assert clean_for_speech("") == "" and clean_for_speech("https://x.com") == ""


def test_split_sentences() -> None:
    assert split_sentences("Hi there. How are you? Great!") == ["Hi there.", "How are you?", "Great!"]


def test_speak_is_async_and_speaks_every_sentence() -> None:
    engine, player = FakeEngine(), FakePlayer()
    sp = Speaker(engine, player)
    sp.speak("One. Two. Three.")
    sp.wait()
    assert engine.texts == ["One.", "Two.", "Three."] and player.played == 3


def test_stop_interrupts_quickly() -> None:
    engine, player = FakeEngine(), FakePlayer(seconds=5)
    sp = Speaker(engine, player)
    sp.speak("One. Two. Three. Four.")
    time.sleep(0.1)
    t0 = time.perf_counter()
    sp.stop()
    assert time.perf_counter() - t0 < 1.0
    assert player.played == 1


def test_speak_nothing_for_empty_text() -> None:
    engine, player = FakeEngine(), FakePlayer()
    sp = Speaker(engine, player)
    sp.speak("https://only-a-link.com")
    sp.wait()
    assert engine.texts == [] and player.played == 0


def test_create_speaker_muted() -> None:
    assert isinstance(tts.create_speaker(Settings(tts=False)), NullSpeaker)


def test_create_speaker_falls_back_to_say(monkeypatch) -> None:
    def broken(settings):
        raise RuntimeError("onnx failed")

    monkeypatch.setattr(tts, "KokoroEngine", broken)
    monkeypatch.setattr(tts.shutil, "which", lambda name: "/usr/bin/say")
    sp = tts.create_speaker(Settings(tts=True, tts_engine="kokoro"), player=FakePlayer())
    assert isinstance(sp, Speaker) and sp.name == "say"


def test_clean_speaks_file_names_by_kind() -> None:
    text = (
        "Files: 1000055692.jpg, 20260710T161658_957073.txt, valid_lossrun.pdf, "
        "[Migrated]_BULK_IMPORT_1784012550584.json and ID 98765432101."
    )
    assert clean_for_speech(text) == (
        "Files: an image, a text file, valid lossrun PDF, "
        "Migrated Bulk Import JSON file and ID."
    )


def test_clean_keeps_normal_numbers() -> None:
    assert clean_for_speech("It is 8:06 PM on 1 November 2026, 561 files.") == (
        "It is 8:06 PM on 1 November 2026, 561 files."
    )


def test_clean_drops_list_numbering_and_underscores() -> None:
    text = "Here are the files:\n1. valid_lossrun.pdf\n2. notes.txt\n- photo.jpg"
    assert clean_for_speech(text) == "Here are the files: valid lossrun PDF, notes text file, photo image"
