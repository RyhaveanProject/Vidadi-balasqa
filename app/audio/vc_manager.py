"""PyTgCalls voice-chat manager.

Responsibilities:
* Join / leave a group voice chat (one VC per process).
* Pipe **inbound** audio frames from PyTgCalls into the per-chat
  AudioPipeline via the auto-detecting InboundCapture adapter.
* Play **outbound** TTS clips through a bounded queue.
* Full-duplex behaviour: any caller can request an interrupt
  (typically the AudioPipeline when it detects user speech while
  the bot is talking).  The interrupt drains the speak queue and
  stops the current playback so the user is heard immediately.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Dict, Optional

from pyrogram import Client
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream, AudioQuality, Update

from app.core.logger import log
from app.audio.audio_pipeline import AudioPipeline
from app.audio.inbound import InboundCapture


class VCManager:
    """Owner-controlled VC presence; one active VC per process."""

    def __init__(self, client: Client) -> None:
        self.client = client
        self.calls = PyTgCalls(client)
        self.active_chats: Dict[int, AudioPipeline] = {}
        self._captures: Dict[int, InboundCapture] = {}
        self._lock = asyncio.Lock()
        # Per-chat outbound TTS queue
        self._tts_queues: Dict[int, asyncio.Queue] = {}
        self._tts_workers: Dict[int, asyncio.Task] = {}
        # Track current playback for interrupt-cancel
        self._current_playback: Dict[int, dict] = {}

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        await self.calls.start()

        @self.calls.on_update()
        async def _on_update(_, update: Update):
            try:
                from pytgcalls.types import StreamEnded, ChatUpdate
                if isinstance(update, StreamEnded):
                    log.info("VC stream ended chat={}", update.chat_id)
                    info = self._current_playback.get(update.chat_id)
                    if info:
                        info["done"].set()
                        pipe = self.active_chats.get(update.chat_id)
                        if pipe:
                            pipe.state.mark_bot_speak_done()
                elif isinstance(update, ChatUpdate):
                    log.info(
                        "VC chat update chat={} status={}",
                        update.chat_id, update.status,
                    )
            except Exception:  # noqa: BLE001
                pass

        log.info("PyTgCalls started.")

    async def stop(self) -> None:
        for chat_id in list(self.active_chats.keys()):
            try:
                await self.leave(chat_id)
            except Exception:  # noqa: BLE001
                pass
        try:
            await self.calls.stop()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # join / leave
    # ------------------------------------------------------------------

    def is_in_vc(self, chat_id: int) -> bool:
        return chat_id in self.active_chats

    async def join(self, chat_id: int) -> bool:
        async with self._lock:
            if chat_id in self.active_chats:
                return True
            pipeline = AudioPipeline(chat_id=chat_id, vc=self)
            silent = await _ensure_silent_wav()
            try:
                await self.calls.play(
                    chat_id,
                    MediaStream(
                        silent,
                        audio_parameters=AudioQuality.HIGH,
                        video_flags=MediaStream.Flags.IGNORE,
                    ),
                )
                self.active_chats[chat_id] = pipeline
                await pipeline.start()

                # Bind inbound capture (auto-detect)
                capture = InboundCapture(self.calls, chat_id, pipeline)
                await capture.start()
                self._captures[chat_id] = capture

                # Spin per-chat TTS worker
                q: asyncio.Queue = asyncio.Queue(maxsize=8)
                self._tts_queues[chat_id] = q
                self._tts_workers[chat_id] = asyncio.create_task(
                    self._tts_worker(chat_id, q)
                )
                log.info("Joined VC chat={} (capture={})", chat_id, capture.strategy)
                return True
            except Exception as e:  # noqa: BLE001
                log.exception("join VC failed: {}", e)
                return False

    async def leave(self, chat_id: int) -> bool:
        async with self._lock:
            pipeline = self.active_chats.pop(chat_id, None)
            cap = self._captures.pop(chat_id, None)
            worker = self._tts_workers.pop(chat_id, None)
            self._tts_queues.pop(chat_id, None)
            if cap:
                await cap.stop()
            if pipeline:
                await pipeline.stop()
            if worker:
                worker.cancel()
            try:
                await self.calls.leave_call(chat_id)
            except Exception as e:  # noqa: BLE001
                log.warning("leave VC error: {}", e)
            log.info("Left VC chat={}", chat_id)
            return True

    # ------------------------------------------------------------------
    # outbound TTS queue
    # ------------------------------------------------------------------

    async def speak(self, chat_id: int, text: str) -> None:
        """Queue a sentence to be TTS'd and played in the VC."""
        if not text.strip():
            return
        q = self._tts_queues.get(chat_id)
        if q is None:
            return
        try:
            q.put_nowait(text)
        except asyncio.QueueFull:
            # Drop the oldest pending to keep the conversation snappy
            try:
                q.get_nowait()
                q.put_nowait(text)
            except Exception:  # noqa: BLE001
                pass

    async def interrupt(self, chat_id: int) -> None:
        """Drop pending replies & stop the current playback (full-duplex)."""
        q = self._tts_queues.get(chat_id)
        if q is not None:
            while not q.empty():
                try:
                    q.get_nowait()
                except Exception:  # noqa: BLE001
                    break
        info = self._current_playback.get(chat_id)
        if info and not info["done"].is_set():
            info["interrupted"] = True
            # Replace current outbound with 100ms silence to cut audio fast
            try:
                silent = await _ensure_silent_wav()
                await self.calls.play(
                    chat_id,
                    MediaStream(
                        silent,
                        audio_parameters=AudioQuality.HIGH,
                        video_flags=MediaStream.Flags.IGNORE,
                    ),
                )
                info["done"].set()
            except Exception as e:  # noqa: BLE001
                log.debug("interrupt play silent failed: {}", e)
        pipe = self.active_chats.get(chat_id)
        if pipe:
            pipe.state.mark_bot_speak_done()
        log.info("[VC {}] interrupted by user", chat_id)

    async def _tts_worker(self, chat_id: int, q: asyncio.Queue) -> None:
        from app.audio.tts import get_tts
        tts = get_tts()
        while True:
            try:
                text = await q.get()
                if chat_id not in self.active_chats:
                    continue
                wav = await tts.synthesize_to_file(text)
                if not wav:
                    continue
                pipe = self.active_chats.get(chat_id)
                done = asyncio.Event()
                self._current_playback[chat_id] = {
                    "done": done,
                    "interrupted": False,
                    "started": time.time(),
                }
                if pipe:
                    pipe.state.mark_bot_speak_start()
                try:
                    await self.calls.play(
                        chat_id,
                        MediaStream(
                            wav,
                            audio_parameters=AudioQuality.HIGH,
                            video_flags=MediaStream.Flags.IGNORE,
                        ),
                    )
                    log.info("VC speaking [{}]: {}", chat_id, text[:80])
                    # Wait until stream end fires OR estimated duration
                    est_sec = max(1.2, len(text) * 0.07)
                    try:
                        await asyncio.wait_for(done.wait(), timeout=est_sec + 2.0)
                    except asyncio.TimeoutError:
                        pass
                except Exception as e:  # noqa: BLE001
                    log.exception("VC play failed: {}", e)
                    done.set()
                finally:
                    if pipe and not self._current_playback.get(chat_id, {}).get("interrupted"):
                        pipe.state.mark_bot_speak_done()
                    self._current_playback.pop(chat_id, None)
                    try:
                        os.remove(wav)
                    except OSError:
                        pass
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                log.exception("tts_worker loop: {}", e)


async def _ensure_silent_wav() -> str:
    """Create a 1s silent WAV used as the initial 'play' source."""
    path = "/tmp/_vidadi_silence.wav"
    if os.path.exists(path):
        return path
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-t", "1", "-acodec", "pcm_s16le", path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    return path
