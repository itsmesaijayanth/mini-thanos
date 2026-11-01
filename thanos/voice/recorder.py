"""Microphone recording: start on demand, stop automatically after silence."""

from __future__ import annotations

import queue
import time

import numpy as np

from thanos.config import Settings

BLOCK_SECONDS = 0.05
CALIBRATE_SECONDS = 0.3
NO_SPEECH_TIMEOUT = 6.0


def record_utterance(settings: Settings) -> np.ndarray:
    """Record one utterance as mono float32 at settings.sample_rate.

    Stops after `silence_seconds` of quiet following speech, after `max_record_seconds`,
    or after NO_SPEECH_TIMEOUT with no speech (returns an empty array in that case).
    """
    import sounddevice as sd  # lazy: needs PortAudio + mic permission

    sr = settings.sample_rate
    q: queue.Queue[np.ndarray] = queue.Queue()

    def callback(indata, frames, time_info, status) -> None:  # noqa: ARG001
        q.put(indata[:, 0].copy())

    chunks: list[np.ndarray] = []
    heard = False
    silent_for = 0.0
    threshold = settings.silence_threshold
    noise: list[float] = []

    with sd.InputStream(
        samplerate=sr,
        channels=1,
        dtype="float32",
        blocksize=int(sr * BLOCK_SECONDS),
        callback=callback,
    ):
        start = time.monotonic()
        while True:
            try:
                chunk = q.get(timeout=2.0)
            except queue.Empty:
                break
            chunks.append(chunk)
            elapsed = time.monotonic() - start
            level = _rms(chunk)

            # Adapt the threshold to the room's background noise.
            if elapsed < CALIBRATE_SECONDS:
                noise.append(level)
                continue
            if noise:
                threshold = max(settings.silence_threshold, float(np.mean(noise)) * 3)
                noise = []

            if level >= threshold:
                heard = True
                silent_for = 0.0
            else:
                silent_for += len(chunk) / sr

            if heard and silent_for >= settings.silence_seconds:
                break
            if not heard and elapsed >= NO_SPEECH_TIMEOUT:
                break
            if elapsed >= settings.max_record_seconds:
                break

    if not heard or not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks)


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0
