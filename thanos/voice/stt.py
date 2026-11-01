"""Speech-to-text. `STT` is the interface; MlxWhisperSTT runs Whisper on the Apple GPU."""

from __future__ import annotations

from typing import Protocol

import numpy as np

from thanos.config import Settings


class STT(Protocol):
    def transcribe(self, audio: np.ndarray) -> str: ...


class MlxWhisperSTT:
    def __init__(self, settings: Settings) -> None:
        self.model = settings.stt_model
        self.language = settings.stt_language

    def transcribe(self, audio: np.ndarray) -> str:
        if audio.size == 0:
            return ""
        import mlx_whisper  # lazy: heavy import

        result = mlx_whisper.transcribe(
            audio.astype(np.float32),
            path_or_hf_repo=self.model,
            language=self.language,
            condition_on_previous_text=False,
        )
        return str(result.get("text", "")).strip()

    def warmup(self) -> None:
        """Download (first run) and load the model so the first command is fast."""
        import mlx_whisper

        mlx_whisper.transcribe(
            np.zeros(16000, dtype=np.float32), path_or_hf_repo=self.model, language=self.language
        )


def create_stt(settings: Settings) -> STT:
    return MlxWhisperSTT(settings)
