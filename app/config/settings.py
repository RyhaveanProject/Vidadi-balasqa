"""Centralised settings loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _get_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None or val.strip() == "":
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    val = os.getenv(name)
    if val is None or val.strip() == "":
        return default
    try:
        return float(val)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # Telegram
    API_ID: int = _get_int("API_ID", 0)
    API_HASH: str = os.getenv("API_HASH", "")
    SESSION_STRING: str = os.getenv("SESSION_STRING", "")
    OWNER_ID: int = _get_int("OWNER_ID", 0)

    # LLM
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "gemini").lower()
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gemini-2.5-flash")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    EMERGENT_LLM_KEY: str = os.getenv("EMERGENT_LLM_KEY", "")

    # Whisper
    WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "base")
    WHISPER_COMPUTE_TYPE: str = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
    WHISPER_LANGUAGE: str = os.getenv("WHISPER_LANGUAGE", "az")
    WHISPER_CPU_THREADS: int = _get_int("WHISPER_CPU_THREADS", 2)

    # TTS
    TTS_VOICE: str = os.getenv("TTS_VOICE", "az-AZ-BabekNeural")
    TTS_RATE: str = os.getenv("TTS_RATE", "+5%")
    TTS_PITCH: str = os.getenv("TTS_PITCH", "+0Hz")
    TTS_MAX_CHARS: int = _get_int("TTS_MAX_CHARS", 600)

    # Personality
    BOT_NAME: str = os.getenv("BOT_NAME", "Vidadi")

    # Storage
    DB_PATH: str = os.getenv("DB_PATH", "/app/data/vidadi.db")

    # Behaviour
    CHAT_REPLY_PROBABILITY: float = _get_float("CHAT_REPLY_PROBABILITY", 0.18)
    IDLE_CHAT_INTERVAL_SEC: int = _get_int("IDLE_CHAT_INTERVAL_SEC", 900)

    # Realtime VAD tuning (lower silence_end = faster reply, but more interruptions)
    VAD_AGGRESSIVENESS: int = _get_int("VAD_AGGRESSIVENESS", 2)
    MIN_SPEECH_MS: int = _get_int("MIN_SPEECH_MS", 300)
    SILENCE_END_MS: int = _get_int("SILENCE_END_MS", 500)

    # Full-duplex
    INTERRUPT_ENABLED: bool = os.getenv("INTERRUPT_ENABLED", "true").lower() in ("1", "true", "yes")

    def validate(self) -> None:
        missing = []
        if not self.API_ID:
            missing.append("API_ID")
        if not self.API_HASH:
            missing.append("API_HASH")
        if not self.SESSION_STRING:
            missing.append("SESSION_STRING")
        if not self.OWNER_ID:
            missing.append("OWNER_ID")
        if self.LLM_PROVIDER == "gemini" and not self.GEMINI_API_KEY:
            missing.append("GEMINI_API_KEY (or set LLM_PROVIDER=emergent)")
        if self.LLM_PROVIDER == "emergent" and not self.EMERGENT_LLM_KEY:
            missing.append("EMERGENT_LLM_KEY")
        if missing:
            raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")


settings = Settings()
