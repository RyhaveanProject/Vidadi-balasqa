"""Voice-chat side hooks — speaker enrolment via Telegram voice messages."""
from __future__ import annotations

import os
import tempfile
import asyncio

import numpy as np
from pyrogram import Client, filters
from pyrogram.types import Message

from app.audio.vc_manager import VCManager
from app.memory import speaker_db
from app.core.logger import log


def register(client: Client, vc: VCManager) -> None:

    @client.on_message(filters.voice & ~filters.bot & ~filters.me)
    async def on_voice(_, m: Message):
        """Use any voice message in chat to enrol that user's voice fingerprint."""
        if not m.from_user:
            return
        try:
            tmpdir = tempfile.mkdtemp()
            ogg = await m.download(file_name=os.path.join(tmpdir, "v.ogg"))
            wav = os.path.join(tmpdir, "v.wav")
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", ogg, "-ac", "1", "-ar", "16000", wav,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            if not os.path.exists(wav):
                return
            import wave
            with wave.open(wav, "rb") as wf:
                pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
            name = (m.from_user.first_name or "kimsə").strip()
            await speaker_db.add_sample(m.from_user.id, name, pcm, 16000)
            log.info("enrolled voice for {} ({})", name, m.from_user.id)
        except Exception as e:
            log.warning("voice enrol error: {}", e)
