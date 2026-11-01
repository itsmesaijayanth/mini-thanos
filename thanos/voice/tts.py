"""Text-to-speech: Kokoro neural voice (local), macOS `say` as fallback.

`Speaker` is what the app uses:
    speak(text)  -> returns immediately; speaks in the background, sentence by sentence
    stop()       -> interrupts (call before recording so Thanos never hears itself)
    wait()       -> block until done (used by --once before exiting)
"""

from __future__ import annotations

import logging
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import wave
from pathlib import Path
from typing import Protocol

import numpy as np

from thanos.config import Settings

log = logging.getLogger(__name__)

KOKORO_DIR = Path("~/.cache/thanos/kokoro").expanduser()
KOKORO_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
KOKORO_FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")
ON_SCREEN_SUFFIX = "Details are on screen."

Audio = tuple[np.ndarray, int]  # (float32 samples, sample rate)


# --- text preparation -------------------------------------------------------

_URL = re.compile(r"https?://\S+|www\.\S+")
_MARKDOWN = re.compile(r"[*#`>|~\[\]]+")
_EMOJI = re.compile(
    "[\U0001f000-\U0001faff\U00002600-\U000027bf\U0001f900-\U0001f9ff️‍]+"
)
# File names are spoken as "<readable name> <kind>": "valid_lossrun.pdf" -> "valid lossrun PDF",
# "1000055692.jpg" -> "an image" (machine ids/timestamps in names are dropped).
FILE_KINDS: dict[str, str] = {
    **dict.fromkeys(("jpg", "jpeg", "png", "gif", "webp", "heic", "bmp", "svg"), "image"),
    **dict.fromkeys(("mp4", "mov", "mkv", "avi", "webm"), "video"),
    **dict.fromkeys(("mp3", "wav", "m4a", "aac", "flac"), "audio file"),
    **dict.fromkeys(("txt", "md", "log"), "text file"),
    **dict.fromkeys(("csv", "xls", "xlsx", "numbers"), "spreadsheet"),
    **dict.fromkeys(("doc", "docx", "pages", "rtf"), "document"),
    **dict.fromkeys(("ppt", "pptx", "key"), "presentation"),
    **dict.fromkeys(("zip", "tar", "gz", "rar", "7z"), "archive"),
    **dict.fromkeys(("dmg", "pkg"), "installer"),
    **dict.fromkeys(("py", "js", "ts", "sh", "html", "css"), "code file"),
    "pdf": "PDF",
    "json": "JSON file",
}
_FILE_NAME = re.compile(
    r"[^\s,;:]+\.(" + "|".join(sorted(FILE_KINDS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
_ID_LIKE = re.compile(r"\b\w*\d{5,}\w*\b")  # 1784012550584, 20260710T161658
_LONG_NUMBER = re.compile(r"(?<![\d.,])\d{7,}(?!\d|[.,]\d)")  # ids/timestamps; keeps 2026, 561
_LIST_MARKER = re.compile(r"^(?:\d{1,2}[.)]|[-•*])\s+")  # "1. ", "2) ", "- " bullets
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def clean_for_speech(text: str, max_chars: int = 300) -> str:
    """Make a reply pleasant to hear: no links/markdown/emoji, and not too long."""
    text = _URL.sub("", text or "")
    text = _FILE_NAME.sub(_speak_file_name, text)
    text = _LONG_NUMBER.sub("", text)
    text = _join_lines(text).replace("_", " ")
    text = _EMOJI.sub("", _MARKDOWN.sub("", text))
    text = re.sub(r"\s+([.,!?;:])", r"\1", " ".join(text.split()))
    if len(text) <= max_chars:
        return text
    kept = ""
    for sentence in split_sentences(text):
        if len(kept) + len(sentence) + 1 > max_chars:
            break
        kept = f"{kept} {sentence}".strip()
    if not kept:  # one giant sentence: cut at a word boundary
        kept = text[:max_chars].rsplit(" ", 1)[0] + "."
    return f"{kept} {ON_SCREEN_SUFFIX}"


def _speak_file_name(m: re.Match[str]) -> str:
    stem, ext = m.group(0).rsplit(".", 1)
    kind = FILE_KINDS[ext.lower()]
    words = _ID_LIKE.sub(" ", re.sub(r"[\[\]()_.\-]+", " ", stem)).split()
    words = [w.capitalize() if w.isupper() and len(w) >= 4 else w for w in words]
    if not any(len(w) > 1 and w.isalpha() for w in words):
        article = "an" if kind[0] in "aeiou" else "a"
        return f"{article} {kind}"
    return f"{' '.join(words)} {kind}"


def _join_lines(text: str) -> str:
    """Turn multi-line text (incl. numbered/bulleted lists) into one speakable line:
    list items are joined with commas, other lines with sentence breaks."""
    out = ""
    prev_item = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        item = bool(_LIST_MARKER.match(line))
        line = _LIST_MARKER.sub("", line)
        if not out:
            out = line
        elif item and prev_item:
            out += ", " + line
        elif out[-1] in ".!?:,;":
            out += " " + line
        else:
            out += ". " + line
        prev_item = item
    return out


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text) if s.strip()]


# --- engines ----------------------------------------------------------------


class TTSEngine(Protocol):
    def synthesize(self, text: str) -> Audio: ...


class KokoroEngine:
    """Kokoro-82M via onnxruntime. Downloads model files (~340 MB) on first use."""

    def __init__(self, settings: Settings) -> None:
        from kokoro_onnx import Kokoro  # lazy: loads onnxruntime

        logging.getLogger("phonemizer").setLevel(logging.ERROR)  # noisy, harmless warnings

        model, voices = (_ensure_download(name) for name in KOKORO_FILES)
        self._kokoro = Kokoro(str(model), str(voices))
        self.voice = settings.tts_voice
        self.speed = settings.tts_speed
        if self.voice not in self._kokoro.get_voices():
            raise ValueError(f"Unknown Kokoro voice '{self.voice}'")

    def synthesize(self, text: str) -> Audio:
        samples, sr = self._kokoro.create(text, voice=self.voice, speed=self.speed, lang="en-us")
        return samples.astype(np.float32), sr


class SayEngine:
    """macOS `say`, rendered to a WAV so playback is interruptible like Kokoro's."""

    SAMPLE_RATE = 22050

    def __init__(self, settings: Settings) -> None:
        self.voice = settings.tts_say_voice

    def synthesize(self, text: str) -> Audio:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "say.wav"
            cmd = ["say", "-o", str(out), f"--data-format=LEI16@{self.SAMPLE_RATE}"]
            if self.voice:
                cmd += ["-v", self.voice]
            subprocess.run([*cmd, text], check=True, capture_output=True)
            with wave.open(str(out)) as w:
                pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
                return pcm.astype(np.float32) / 32768.0, w.getframerate()


def _ensure_download(name: str) -> Path:
    path = KOKORO_DIR / name
    if path.exists() and path.stat().st_size > 0:
        return path
    import httpx

    KOKORO_DIR.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + ".part")
    log.warning("Downloading %s (first run only)…", name)
    with httpx.stream("GET", f"{KOKORO_RELEASE}/{name}", follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        with part.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    part.rename(path)
    return path


# --- playback ---------------------------------------------------------------


class Player(Protocol):
    def play(self, audio: np.ndarray, sample_rate: int) -> None: ...  # blocks until done/stopped
    def stop(self) -> None: ...


class SoundDevicePlayer:
    def play(self, audio: np.ndarray, sample_rate: int) -> None:
        import sounddevice as sd

        sd.play(audio, sample_rate)
        sd.wait()

    def stop(self) -> None:
        import sounddevice as sd

        sd.stop()


# --- speaker ----------------------------------------------------------------


class Speaker:
    def __init__(self, engine: TTSEngine, player: Player, max_chars: int = 300) -> None:
        self.engine = engine
        self.player = player
        self.max_chars = max_chars
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    @property
    def name(self) -> str:
        return type(self.engine).__name__.removesuffix("Engine").lower()

    def speak(self, text: str) -> None:
        self.stop()
        sentences = split_sentences(clean_for_speech(text, self.max_chars))
        if not sentences:
            return
        self._stop = threading.Event()
        q: queue.Queue[Audio | None] = queue.Queue(maxsize=2)
        # Producer synthesizes sentence N+1 while the consumer plays sentence N.
        self._threads = [
            threading.Thread(target=self._produce, args=(sentences, q, self._stop), daemon=True),
            threading.Thread(target=self._consume, args=(q, self._stop), daemon=True),
        ]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        if not any(t.is_alive() for t in self._threads):
            return
        self._stop.set()
        self.player.stop()
        for t in self._threads:
            t.join(timeout=2)

    def wait(self) -> None:
        for t in self._threads:
            t.join()

    def warmup(self) -> None:
        self.engine.synthesize("Ready.")

    def _produce(self, sentences: list[str], q: queue.Queue, stop: threading.Event) -> None:
        try:
            for s in sentences:
                if stop.is_set():
                    break
                _put_unless_stopped(q, self.engine.synthesize(s), stop)
        except Exception:
            log.warning("Speech synthesis failed", exc_info=True)
        finally:
            _put_unless_stopped(q, None, stop)  # end-of-speech marker

    def _consume(self, q: queue.Queue, stop: threading.Event) -> None:
        try:
            while not stop.is_set():
                try:
                    item = q.get(timeout=0.1)
                except queue.Empty:
                    continue
                if item is None:
                    break
                self.player.play(*item)
        except Exception:
            log.warning("Audio playback failed", exc_info=True)


class NullSpeaker:
    """Used when speech is off."""

    name = "off"

    def speak(self, text: str) -> None: ...
    def stop(self) -> None: ...
    def wait(self) -> None: ...
    def warmup(self) -> None: ...


def create_speaker(settings: Settings, player: Player | None = None) -> Speaker | NullSpeaker:
    if not settings.tts:
        return NullSpeaker()
    engine: TTSEngine | None = None
    if settings.tts_engine == "kokoro":
        try:
            engine = KokoroEngine(settings)
        except Exception as e:
            log.warning("Kokoro voice unavailable (%s); falling back to macOS say", e)
    if engine is None and shutil.which("say"):
        engine = SayEngine(settings)
    if engine is None:
        return NullSpeaker()
    return Speaker(engine, player or SoundDevicePlayer(), settings.tts_max_chars)


def _put_unless_stopped(q: queue.Queue, item: object, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            q.put(item, timeout=0.1)
            return
        except queue.Full:
            continue
