"""WebRTC-VAD based speech segmenter (low-latency, tunable).

Input contract:
    feed(pcm16_bytes) where pcm16_bytes is 16 kHz mono int16 PCM
    bytes — chunks may be any size; the segmenter buffers and slices
    into 20 ms frames internally.

It yields full utterances as numpy int16 arrays when a silence gap
follows speech.

Two flushes are emitted:
    * Complete utterance (silence > SILENCE_END_MS)
    * Force-flush via ``flush()`` on shutdown

``is_voiced_chunk(chunk)`` is a fast probe that returns True if the
given chunk contains enough speech to consider it "user just barged
in" — used by the dialog state machine for full-duplex interrupts
without waiting for the utterance to complete.
"""
from __future__ import annotations

from typing import Iterator, Optional

import numpy as np
import webrtcvad

from app.config.settings import settings


FRAME_MS = 20
SAMPLE_RATE = 16000
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 320
BYTES_PER_FRAME = FRAME_SAMPLES * 2

# A short rolling pre-roll added to each utterance so the very first
# phoneme (often clipped by VAD) makes it into Whisper.
PREROLL_FRAMES = 8  # 160 ms


class VADSegmenter:
    def __init__(self) -> None:
        self.vad = webrtcvad.Vad(settings.VAD_AGGRESSIVENESS)
        self._buf = bytearray()
        self._speech: list[bytes] = []
        self._preroll: list[bytes] = []
        self._silence_ms = 0
        self._speech_ms = 0
        self._min_speech = settings.MIN_SPEECH_MS
        self._silence_end = settings.SILENCE_END_MS

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def feed(self, pcm16_bytes: bytes) -> Iterator[np.ndarray]:
        """Feed raw 16kHz mono PCM bytes; yield numpy int16 utterances."""
        self._buf.extend(pcm16_bytes)
        while len(self._buf) >= BYTES_PER_FRAME:
            frame = bytes(self._buf[:BYTES_PER_FRAME])
            del self._buf[:BYTES_PER_FRAME]
            is_speech = self._safe_is_speech(frame)

            if is_speech:
                if not self._speech:
                    # Starting a new utterance — attach pre-roll
                    self._speech.extend(self._preroll)
                self._speech.append(frame)
                self._speech_ms += FRAME_MS
                self._silence_ms = 0
            else:
                # Update pre-roll ring
                self._preroll.append(frame)
                if len(self._preroll) > PREROLL_FRAMES:
                    self._preroll.pop(0)

                if self._speech:
                    self._silence_ms += FRAME_MS
                    self._speech.append(frame)  # tail / co-articulation
                    if self._silence_ms >= self._silence_end:
                        if self._speech_ms >= self._min_speech:
                            raw = b"".join(self._speech)
                            yield np.frombuffer(raw, dtype=np.int16)
                        self._speech.clear()
                        self._speech_ms = 0
                        self._silence_ms = 0

    def is_voiced_chunk(self, pcm16_bytes: bytes) -> bool:
        """Cheap probe — True if >=2 sub-frames look like speech."""
        if len(pcm16_bytes) < BYTES_PER_FRAME * 2:
            return False
        voiced = 0
        n_frames = len(pcm16_bytes) // BYTES_PER_FRAME
        for i in range(n_frames):
            frame = pcm16_bytes[i * BYTES_PER_FRAME:(i + 1) * BYTES_PER_FRAME]
            if len(frame) != BYTES_PER_FRAME:
                continue
            if self._safe_is_speech(frame):
                voiced += 1
                if voiced >= 2:
                    return True
        return False

    def flush(self) -> Optional[np.ndarray]:
        if self._speech and self._speech_ms >= self._min_speech:
            raw = b"".join(self._speech)
            self._speech.clear()
            self._speech_ms = 0
            self._silence_ms = 0
            return np.frombuffer(raw, dtype=np.int16)
        return None

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _safe_is_speech(self, frame: bytes) -> bool:
        try:
            return self.vad.is_speech(frame, SAMPLE_RATE)
        except Exception:  # noqa: BLE001
            return False
