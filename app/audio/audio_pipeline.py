"""Realtime audio pipeline for an active voice chat.

End-to-end flow per utterance (target latency  < 3 s):

    1.  Inbound capture pushes 16k mono s16le frames into ``feed_frame``.
    2.  VAD segmenter yields complete utterances when a silence gap
        follows speech (configurable via ``MIN_SPEECH_MS`` /
        ``SILENCE_END_MS``).
    3.  If the bot is currently speaking, the very *start* of speech
        triggers an interrupt (full-duplex).
    4.  STT (faster-whisper, int8, beam=1, greedy) transcribes.
    5.  Speaker fingerprint identifies who spoke (best-effort).
    6.  Brain.reply_stream() yields chunks; each chunk is fed to TTS
        prebuffer the moment it arrives → first TTS audio starts
        playing before the LLM has finished generating.
    7.  TTS prebuffer streams sentence-sized WAV files to the
        VCManager.speak() queue — VCManager plays them back-to-back
        until the queue is drained or an interrupt is consumed.
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import numpy as np

from app.audio.dialog_state import DialogState
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
        self.state = DialogState()
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=400)
        self._task: asyncio.Task | None = None
        self._running = False

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._consumer())
        # Warm whisper in background to remove first-utterance lag
        asyncio.create_task(self._warmup())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except Exception:  # noqa: BLE001
                pass

    async def _warmup(self) -> None:
        try:
            stt = get_stt()
            await stt.transcribe_pcm(np.zeros(16000, dtype=np.int16))
            log.info("[VC {}] STT warmed", self.chat_id)
        except Exception as e:  # noqa: BLE001
            log.debug("warmup error: {}", e)

    # ------------------------------------------------------------------
    # ingress
    # ------------------------------------------------------------------

    def feed_frame(self, pcm16_bytes: bytes) -> None:
        """Called by InboundCapture. 16k mono s16le. Non-blocking."""
        if not self._running:
            return
        try:
            self._queue.put_nowait(pcm16_bytes)
        except asyncio.QueueFull:
            # drop oldest
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(pcm16_bytes)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # consumer loop
    # ------------------------------------------------------------------

    async def _consumer(self) -> None:
        try:
            while self._running:
                chunk = await self._queue.get()
                # Probe VAD on incoming frame for early interrupt detection
                # (before full utterance ends, so the bot stops talking fast).
                if self.state.bot_is_speaking() and self.vad.is_voiced_chunk(chunk):
                    self.state.request_interrupt()
                    await self.vc.interrupt(self.chat_id)
                # Standard segmenter pass
                for utterance in self.vad.feed(chunk):
                    self.state.mark_user_speaking()
                    asyncio.create_task(self._handle_utterance(utterance))
        except asyncio.CancelledError:
            return
        except Exception as e:  # noqa: BLE001
            log.exception("AudioPipeline consumer crashed: {}", e)

    # ------------------------------------------------------------------
    # per-utterance handler
    # ------------------------------------------------------------------

    async def _handle_utterance(self, pcm16: np.ndarray) -> None:
        t0 = time.time()
        try:
            stt = get_stt()
            text = await stt.transcribe_pcm(pcm16)
            t_stt = time.time() - t0
            if not text:
                return
            log.info(
                "[VC {}] heard ({:.2f}s STT): {}",
                self.chat_id, t_stt, text,
            )

            # Speaker recognition (best-effort, in thread)
            speaker_name = "kimsə"
            speaker_id: int | None = None
            try:
                ident = await speaker_db.identify(pcm16)
                if ident:
                    speaker_id, speaker_name, _ = ident
            except Exception:  # noqa: BLE001
                pass

            # Memory: log heard utterance + load history
            try:
                await user_memory.remember_message(
                    self.chat_id, speaker_id or 0, speaker_name, text,
                )
            except Exception:  # noqa: BLE001
                pass
            history = await user_memory.context_for(self.chat_id, limit=10)

            # Mark thinking + start streaming reply
            self.state.mark_thinking()
            brain = get_brain()

            first_chunk_t = None
            sentence_buf = ""
            async for chunk in brain.reply_stream(
                text, speaker_name, history, in_voice_chat=True,
            ):
                if first_chunk_t is None:
                    first_chunk_t = time.time() - t0
                    log.info(
                        "[VC {}] first LLM chunk in {:.2f}s",
                        self.chat_id, first_chunk_t,
                    )
                sentence_buf += chunk
                # Flush on sentence-ish boundary for early TTS prebuffer
                flushed, sentence_buf = _split_on_boundary(sentence_buf)
                for sent in flushed:
                    if not self._running:
                        return
                    await self.vc.speak(self.chat_id, sent)

            if sentence_buf.strip():
                await self.vc.speak(self.chat_id, sentence_buf.strip())

            total = time.time() - t0
            log.info("[VC {}] reply end-to-end {:.2f}s", self.chat_id, total)
        except Exception as e:  # noqa: BLE001
            log.exception("utterance handler error: {}", e)


# ---------------------------------------------------------------------------
# Sentence-boundary splitter (used for streaming TTS prebuffer)
# ---------------------------------------------------------------------------

_BOUNDARIES = ".!?…\n"
_MIN_FLUSH_CHARS = 18


def _split_on_boundary(buf: str) -> tuple[list[str], str]:
    """Split ``buf`` into completed sentences + remainder.

    Designed for streaming: never flushes super-short fragments so
    edge-tts has enough material to make a natural-sounding clip.
    """
    out: list[str] = []
    cursor = 0
    for i, ch in enumerate(buf):
        if ch in _BOUNDARIES and (i - cursor) >= _MIN_FLUSH_CHARS:
            piece = buf[cursor:i + 1].strip()
            if piece:
                out.append(piece)
            cursor = i + 1
    return out, buf[cursor:]
