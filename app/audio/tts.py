"""Edge-TTS wrapper with prebuffering for low-latency VC playback.

The producer (LLM stream) calls ``synthesize_to_file`` once per
sentence; the result is a 48 kHz stereo PCM WAV ready for
PyTgCalls.  By chunking at the sentence level we let the first
audio reach the VC while the model is still generating the rest.
"""
from __future__ import annotations

import asyncio
import os
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
        # Trim to avoid edge-tts choking on very long inputs
        text = text.strip()[:settings.TTS_MAX_CHARS]

        mp3_path = os.path.join(out_dir, f"vidadi_{uuid.uuid4().hex}.mp3")
        wav_path = mp3_path.replace(".mp3", ".wav")
        try:
            comm = edge_tts.Communicate(
                text, self.voice, rate=self.rate, pitch=self.pitch,
            )
            await comm.save(mp3_path)
            # Convert to PCM 48kHz stereo s16le — PyTgCalls expects this
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
            return wav_path if os.path.exists(wav_path) else None
        except Exception as e:  # noqa: BLE001
            log.exception("TTS failed: {}", e)
            try:
                if os.path.exists(mp3_path):
                    os.remove(mp3_path)
            except OSError:
                pass
            return None


_tts: TTS | None = None


def get_tts() -> TTS:
    global _tts
    if _tts is None:
        _tts = TTS()
    return _tts
