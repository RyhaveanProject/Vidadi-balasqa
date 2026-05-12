"""Realtime audio pipeline for an active voice chat.

NOTE on PyTgCalls inbound audio:
PyTgCalls (free) does not expose a stable Python API for raw inbound PCM
in every version. This pipeline ships with a clean adapter layer that
accepts PCM frames pushed by any callback hook PyTgCalls exposes
(e.g. `on_raw_update` / `RawStream`). If your PyTgCalls build lacks
inbound capture, the pipeline still runs cleanly — speech recognition
will simply not fire until frames are pushed via `feed_frame()`.

The output path (TTS → playback) works in all versions and is the main
realtime feature used by `.ses` mode.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.audio.vad import VADSegmenter
from app.audio.stt import get_stt
from app.ai.brain import get_brain
from app.memory import user_memory, speaker_db
from app.core.logger import log

if TYPE_CHECKING:
    from app.audio.vc_manager import VCManager


class AudioPipeline:
    def __init__(self, chat_id: int, vc: "VCManager") -> None:
        self.chat_id = chat_id
        self.vc = vc
        self.vad = VADSegmenter()
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._consumer())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass

    def feed_frame(self, pcm16_bytes: bytes) -> None:
        """Called by PyTgCalls inbound hook. Non-blocking."""
        if not self._running:
            return
        try:
            self._queue.put_nowait(pcm16_bytes)
        except asyncio.QueueFull:
            pass  # drop oldest

    async def _consumer(self) -> None:
        try:
            while self._running:
                chunk = await self._queue.get()
                for utterance in self.vad.feed(chunk):
                    asyncio.create_task(self._handle_utterance(utterance))
        except asyncio.CancelledError:
            return
        except Exception as e:
            log.exception("AudioPipeline consumer crashed: {}", e)

    async def _handle_utterance(self, pcm16) -> None:
        try:
            stt = get_stt()
            text = await stt.transcribe_pcm(pcm16)
            if not text:
                return
            log.info("[VC {}] heard: {}", self.chat_id, text)

            # Speaker recognition (best effort)
            speaker_name = "kimsə"
            ident = await speaker_db.identify(pcm16)
            if ident:
                _, name, _ = ident
                speaker_name = name

            history = await user_memory.context_for(self.chat_id, limit=10)
            brain = get_brain()
            reply = await brain.reply(text, speaker_name, history, in_voice_chat=True)
            if not reply:
                return
            log.info("[VC {}] reply: {}", self.chat_id, reply)
            await self.vc.speak(self.chat_id, reply)
        except Exception as e:
            log.exception("utterance handler error: {}", e)
