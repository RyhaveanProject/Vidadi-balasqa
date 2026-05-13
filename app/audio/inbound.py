"""Inbound audio capture adapter for PyTgCalls.

PyTgCalls inbound recording APIs differ between versions. This module
auto-detects which capture strategy works at runtime and falls back
cleanly if none are available.

Strategies (tried in order):
1. Native ``@calls.on_raw_audio`` / ``on_stream_audio`` callback
   (PyTgCalls 2.2+ with ntgcalls record bridge).
2. ``RecordStream`` API — start a record sink to a FIFO pipe, then
   read PCM bytes off the FIFO and push them to the pipeline.
3. FFmpeg loopback fallback — pulls audio from a system loopback
   device (PulseAudio ``default.monitor`` / ALSA ``pulse_monitor``).
4. Pure no-op fallback (pipeline still functional via ``feed_frame``
   for manual / test injection).

Frame format pushed to the pipeline:
* PCM 16-bit little-endian
* 16 kHz mono (resampled from PyTgCalls 48 kHz stereo)

This keeps the rest of the pipeline (VAD, faster-whisper) on a
single, predictable input contract regardless of which capture
backend ends up being used.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import struct
from typing import TYPE_CHECKING, Optional

import numpy as np

from app.core.logger import log

if TYPE_CHECKING:
    from pytgcalls import PyTgCalls
    from app.audio.audio_pipeline import AudioPipeline


# ---------------------------------------------------------------------------
# Resampling helpers (48k stereo s16le  ->  16k mono s16le)
# ---------------------------------------------------------------------------

def _stereo48k_to_mono16k(pcm: bytes) -> bytes:
    """Downmix stereo→mono and resample 48000→16000 using simple decimation.

    Quality is acceptable for speech recognition; keeps zero extra deps.
    """
    if not pcm:
        return b""
    # Ensure even number of int16 samples and even number of stereo pairs
    n_samples = len(pcm) // 2
    if n_samples < 2:
        return b""
    arr = np.frombuffer(pcm, dtype=np.int16)
    # Stereo -> mono
    if arr.size % 2 != 0:
        arr = arr[:-1]
    arr = arr.reshape(-1, 2).mean(axis=1).astype(np.int16)
    # 48k -> 16k  (decimate by 3 with simple averaging anti-alias)
    if arr.size < 3:
        return b""
    trim = arr.size - (arr.size % 3)
    arr = arr[:trim].reshape(-1, 3).mean(axis=1).astype(np.int16)
    return arr.tobytes()


# ---------------------------------------------------------------------------
# Capture adapter
# ---------------------------------------------------------------------------

class InboundCapture:
    """Auto-detecting inbound audio capture for a single voice chat."""

    def __init__(
        self,
        calls: "PyTgCalls",
        chat_id: int,
        pipeline: "AudioPipeline",
    ) -> None:
        self.calls = calls
        self.chat_id = chat_id
        self.pipeline = pipeline
        self.strategy: str = "none"
        self._tasks: list[asyncio.Task] = []
        self._fifo_path: Optional[str] = None
        self._ffmpeg_proc: Optional[asyncio.subprocess.Process] = None
        self._closed = False

    # ------------------------------------------------------------------
    # start / stop
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if await self._try_native_callback():
            self.strategy = "native_callback"
        elif await self._try_record_stream():
            self.strategy = "record_stream_fifo"
        elif await self._try_ffmpeg_loopback():
            self.strategy = "ffmpeg_loopback"
        else:
            self.strategy = "noop"
            log.warning(
                "[VC {}] No inbound audio strategy available — "
                "pipeline accepts manual feed_frame() calls only.",
                self.chat_id,
            )
        log.info("[VC {}] inbound capture strategy = {}", self.chat_id, self.strategy)

    async def stop(self) -> None:
        self._closed = True
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()
        if self._ffmpeg_proc and self._ffmpeg_proc.returncode is None:
            try:
                self._ffmpeg_proc.kill()
            except ProcessLookupError:
                pass
        if self._fifo_path and os.path.exists(self._fifo_path):
            try:
                os.unlink(self._fifo_path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Strategy 1 — native PyTgCalls callback (preferred, lowest latency)
    # ------------------------------------------------------------------

    async def _try_native_callback(self) -> bool:
        """Attempt to register a frame callback on the PyTgCalls instance.

        Different PyTgCalls builds expose this under different names;
        we probe each one and bind whichever exists.
        """
        candidates = (
            "on_raw_audio",        # newer ntgcalls builds
            "on_stream_audio",     # alt
            "on_audio_frame",      # custom mods (tgcaller)
        )
        for name in candidates:
            deco = getattr(self.calls, name, None)
            if not callable(deco):
                continue
            try:
                @deco()
                async def _on_audio(_, update):  # noqa: ANN001
                    if self._closed:
                        return
                    if getattr(update, "chat_id", None) != self.chat_id:
                        return
                    raw = (
                        getattr(update, "frame", None)
                        or getattr(update, "data", None)
                        or getattr(update, "audio", None)
                    )
                    if not raw:
                        return
                    pcm16 = _stereo48k_to_mono16k(bytes(raw))
                    if pcm16:
                        self.pipeline.feed_frame(pcm16)

                log.info("[VC {}] bound native callback '{}'", self.chat_id, name)
                return True
            except Exception as e:  # noqa: BLE001
                log.debug("native '{}' bind failed: {}", name, e)
                continue
        return False

    # ------------------------------------------------------------------
    # Strategy 2 — RecordStream → FIFO
    # ------------------------------------------------------------------

    async def _try_record_stream(self) -> bool:
        try:
            from pytgcalls.types import RecordStream  # type: ignore
        except ImportError:
            return False

        fifo = f"/tmp/vidadi_in_{self.chat_id}.s16le"
        try:
            if os.path.exists(fifo):
                os.unlink(fifo)
            os.mkfifo(fifo)
        except OSError as e:
            log.debug("mkfifo failed: {}", e)
            return False

        self._fifo_path = fifo

        # Try to start record into the FIFO; tolerate any kwarg variation.
        try:
            rec_kwargs: dict = {}
            # Common signatures across versions
            for k in ("audio_path", "path", "file"):
                rec_kwargs[k] = fifo
                try:
                    stream = RecordStream(**{k: fifo})  # type: ignore[arg-type]
                    rec_kwargs = {k: fifo}
                    break
                except TypeError:
                    rec_kwargs.pop(k, None)
                    continue
            else:
                return False

            # start the record sink — method names vary
            method = (
                getattr(self.calls, "record", None)
                or getattr(self.calls, "start_recording", None)
            )
            if method is None:
                return False
            await method(self.chat_id, stream)  # type: ignore[misc]
        except Exception as e:  # noqa: BLE001
            log.debug("RecordStream start failed: {}", e)
            return False

        # Reader task — read FIFO and push frames
        task = asyncio.create_task(self._fifo_reader(fifo))
        self._tasks.append(task)
        return True

    async def _fifo_reader(self, fifo: str) -> None:
        # Open non-blocking; ffmpeg/pytgcalls writes producer side
        try:
            fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as e:
            log.warning("FIFO open failed: {}", e)
            return
        buf = bytearray()
        # ~40ms @ 48k stereo s16le = 48000*2*2*0.04 = 7680 bytes
        target_chunk = 7680
        try:
            while not self._closed:
                try:
                    chunk = os.read(fd, 8192)
                except BlockingIOError:
                    chunk = b""
                except OSError:
                    break
                if chunk:
                    buf.extend(chunk)
                    while len(buf) >= target_chunk:
                        block = bytes(buf[:target_chunk])
                        del buf[:target_chunk]
                        pcm16 = _stereo48k_to_mono16k(block)
                        if pcm16:
                            self.pipeline.feed_frame(pcm16)
                else:
                    await asyncio.sleep(0.02)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Strategy 3 — ffmpeg loopback (safety fallback)
    # ------------------------------------------------------------------

    async def _try_ffmpeg_loopback(self) -> bool:
        """Pull audio off a system loopback device if available.

        This works on bare-metal / docker hosts that expose a
        PulseAudio monitor source.  Useful as a last resort when
        no in-process inbound API is available.
        """
        if shutil.which("ffmpeg") is None:
            return False
        # Detect a likely loopback source
        candidates = [
            ("pulse", os.environ.get("LOOPBACK_SOURCE", "default.monitor")),
        ]
        for fmt, src in candidates:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-loglevel", "error",
                    "-f", fmt, "-i", src,
                    "-ac", "1", "-ar", "16000",
                    "-f", "s16le", "pipe:1",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except FileNotFoundError:
                return False
            except Exception:  # noqa: BLE001
                continue

            self._ffmpeg_proc = proc
            task = asyncio.create_task(self._ffmpeg_reader(proc))
            self._tasks.append(task)
            # Probe: if ffmpeg dies fast, fall through
            await asyncio.sleep(0.4)
            if proc.returncode is not None:
                continue
            return True
        return False

    async def _ffmpeg_reader(self, proc: asyncio.subprocess.Process) -> None:
        # Already at 16k mono s16le — feed straight in
        target = 640  # 20ms @ 16k mono = 320 samples = 640 bytes
        buf = bytearray()
        assert proc.stdout is not None
        try:
            while not self._closed:
                chunk = await proc.stdout.read(2048)
                if not chunk:
                    break
                buf.extend(chunk)
                while len(buf) >= target:
                    self.pipeline.feed_frame(bytes(buf[:target]))
                    del buf[:target]
        except asyncio.CancelledError:
            return
        except Exception as e:  # noqa: BLE001
            log.warning("ffmpeg reader error: {}", e)


# ---------------------------------------------------------------------------
# Public manual injection helper (also used by the WAV fallback buffer)
# ---------------------------------------------------------------------------

def wav_buffer_to_frames(wav_bytes: bytes) -> bytes:
    """Decode a tiny WAV blob to 16k mono s16le PCM bytes.

    Used by the safety fallback that lets you stuff a recorded
    `.wav` (e.g. a forwarded voice message) into the live pipeline
    if real-time capture is unavailable in your build.
    """
    import io
    import wave
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        sr = w.getframerate()
        nch = w.getnchannels()
        frames = w.readframes(w.getnframes())
    arr = np.frombuffer(frames, dtype=np.int16)
    if nch == 2:
        arr = arr.reshape(-1, 2).mean(axis=1).astype(np.int16)
    if sr != 16000:
        # Linear decimation/interpolation
        ratio = 16000 / sr
        new_len = int(arr.size * ratio)
        if new_len <= 0:
            return b""
        idx = (np.arange(new_len) / ratio).astype(np.int64)
        idx = np.clip(idx, 0, arr.size - 1)
        arr = arr[idx]
    return arr.tobytes()
