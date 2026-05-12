"""Edge-TTS wrapper — produces PCM/WAV files for PyTgCalls."""
from __future__ import annotations

import asyncio
import os
import tempfile
import uuid

import edge_tts

from app.config.settings import settings
from app.core.logger import log


class TTS:
    def __init__(self) -> None:
        self.voice = settings.TTS_VOICE
        self.rate = settings.TTS_RATE
        self.pitch = settings.TTS_PITCH

    async def synthesize_to_file(self, text: str, out_dir: str = "/tmp") -> str | None:
        if not text.strip():
            return None
        mp3_path = os.path.join(out_dir, f"vidadi_{uuid.uuid4().hex}.mp3")
        try:
            comm = edge_tts.Communicate(text, self.voice, rate=self.rate, pitch=self.pitch)
            await comm.save(mp3_path)
            wav_path = mp3_path.replace(".mp3", ".wav")
            # Convert to PCM 48kHz mono — PyTgCalls expects raw audio stream
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", mp3_path,
                "-acodec", "pcm_s16le", "-ar", "48000", "-ac", "2",
                wav_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            try:
                os.remove(mp3_path)
            except OSError:
                pass
            return wav_path
        except Exception as e:
            log.exception("TTS failed: {}", e)
            return None


_tts: TTS | None = None


def get_tts() -> TTS:
    global _tts
    if _tts is None:
        _tts = TTS()
    return _tts
