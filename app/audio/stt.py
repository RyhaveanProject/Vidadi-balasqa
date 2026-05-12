"""faster-whisper based STT — int8, VAD-friendly, async wrapper."""
from __future__ import annotations

import asyncio
from typing import Optional

import numpy as np
from faster_whisper import WhisperModel

from app.config.settings import settings
from app.core.logger import log


class STT:
    def __init__(self) -> None:
        log.info("Loading faster-whisper model={} compute={}",
                 settings.WHISPER_MODEL, settings.WHISPER_COMPUTE_TYPE)
        self.model = WhisperModel(
            settings.WHISPER_MODEL,
            device="cpu",
            compute_type=settings.WHISPER_COMPUTE_TYPE,
            download_root="/app/.cache/whisper",
        )
        self.language = settings.WHISPER_LANGUAGE
        log.info("Whisper ready.")

    async def transcribe_pcm(self, pcm16: np.ndarray, sample_rate: int = 16000) -> str:
        """pcm16: int16 mono numpy array."""
        if pcm16.size < sample_rate // 4:  # <250ms — skip
            return ""

        loop = asyncio.get_running_loop()
        audio = pcm16.astype(np.float32) / 32768.0

        def _run() -> str:
            segments, _ = self.model.transcribe(
                audio,
                language=self.language,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300},
                beam_size=1,
                best_of=1,
                temperature=0.0,
                condition_on_previous_text=False,
            )
            return " ".join(s.text.strip() for s in segments).strip()

        try:
            return await loop.run_in_executor(None, _run)
        except Exception as e:
            log.exception("STT failed: {}", e)
            return ""


_stt: Optional[STT] = None


def get_stt() -> STT:
    global _stt
    if _stt is None:
        _stt = STT()
    return _stt
