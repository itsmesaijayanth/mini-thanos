"""Single source of settings: config.yaml < env vars (THANOS_*) < explicit kwargs."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="THANOS_",
        env_file=PROJECT_ROOT / ".env",
        yaml_file=PROJECT_ROOT / "config.yaml",
        extra="ignore",
    )

    # LLM
    model: str = "qwen3:8b"
    ollama_host: str = "http://localhost:11434"
    temperature: float = 0.0
    think: bool = False
    keep_alive: str = "30m"
    max_steps: int = 10
    history_turns: int = 6
    num_ctx: int = 16384
    max_reply_tokens: int = 400  # hard cap per LLM turn; stops runaway long answers
    max_tool_output_chars: int = 6000

    # Tools
    workspace_dir: Path = Path("~/Thanos")
    shell_max_timeout: int = 120

    # Speech-to-text / recording
    stt_model: str = "mlx-community/whisper-small-mlx"
    stt_language: str | None = "en"
    sample_rate: int = 16000
    silence_seconds: float = 1.2
    max_record_seconds: float = 15.0
    silence_threshold: float = 0.01

    # Text-to-speech
    tts: bool = True
    tts_engine: Literal["kokoro", "say"] = "kokoro"
    tts_voice: str = "af_heart"
    tts_speed: float = 1.0
    tts_say_voice: str | None = None
    tts_max_chars: int = 300

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
        )

    @property
    def workspace(self) -> Path:
        """Workspace folder (expanded, created on first use)."""
        path = self.workspace_dir.expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
