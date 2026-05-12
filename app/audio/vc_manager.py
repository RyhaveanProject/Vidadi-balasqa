"""PyTgCalls voice-chat manager.

Handles join/leave of group voice chats, plays TTS responses, and pipes
incoming audio frames into the realtime audio pipeline.
"""
from __future__ import annotations

import asyncio
import os
from typing import Dict, Optional

from pyrogram import Client
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream, AudioQuality, Update

from app.core.logger import log
from app.audio.audio_pipeline import AudioPipeline


class VCManager:
    """Owner-controlled VC presence; one active VC per process."""

    def __init__(self, client: Client) -> None:
        self.client = client
        self.calls = PyTgCalls(client)
        self.active_chats: Dict[int, AudioPipeline] = {}
        self._lock = asyncio.Lock()
        self._tts_queue: asyncio.Queue = asyncio.Queue()
        self._tts_worker_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        await self.calls.start()

        @self.calls.on_update()
        async def _on_update(_, update: Update):
            try:
                from pytgcalls.types import StreamEnded, ChatUpdate
                if isinstance(update, StreamEnded):
                    log.info("VC stream ended chat={}", update.chat_id)
                elif isinstance(update, ChatUpdate):
                    log.info("VC chat update chat={} status={}", update.chat_id, update.status)
            except Exception:
                pass

        self._tts_worker_task = asyncio.create_task(self._tts_worker())
        log.info("PyTgCalls started.")

    async def stop(self) -> None:
        if self._tts_worker_task:
            self._tts_worker_task.cancel()
        for chat_id in list(self.active_chats.keys()):
            try:
                await self.leave(chat_id)
            except Exception:
                pass
        try:
            await self.calls.stop()
        except Exception:
            pass

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
                        video_flags=MediaStream.IGNORE,
                    ),
                )
                self.active_chats[chat_id] = pipeline
                await pipeline.start()
                log.info("Joined VC chat={}", chat_id)
                return True
            except Exception as e:
                log.exception("join VC failed: {}", e)
                return False

    async def leave(self, chat_id: int) -> bool:
        async with self._lock:
            pipeline = self.active_chats.pop(chat_id, None)
            if pipeline:
                await pipeline.stop()
            try:
                await self.calls.leave_call(chat_id)
            except Exception as e:
                log.warning("leave VC error: {}", e)
            log.info("Left VC chat={}", chat_id)
            return True

    async def speak(self, chat_id: int, text: str) -> None:
        """Queue a sentence to be TTS'd and played in the VC."""
        if not text.strip():
            return
        await self._tts_queue.put((chat_id, text))

    async def _tts_worker(self) -> None:
        from app.audio.tts import get_tts
        tts = get_tts()
        while True:
            try:
                chat_id, text = await self._tts_queue.get()
                if chat_id not in self.active_chats:
                    continue
                wav = await tts.synthesize_to_file(text)
                if not wav:
                    continue
                try:
                    await self.calls.play(
                        chat_id,
                        MediaStream(
                            wav,
                            audio_parameters=AudioQuality.HIGH,
                            video_flags=MediaStream.IGNORE,
                        ),
                    )
                    log.info("VC speaking [{}]: {}", chat_id, text[:80])
                    # Estimate duration to avoid overlap; fallback ~3s
                    await asyncio.sleep(max(1.5, len(text) * 0.07))
                except Exception as e:
                    log.exception("VC play failed: {}", e)
                finally:
                    try:
                        os.remove(wav)
                    except OSError:
                        pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.exception("tts_worker loop: {}", e)


async def _ensure_silent_wav() -> str:
    """Create a 1s silent WAV used as the initial 'play' source (PyTgCalls requires media)."""
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
