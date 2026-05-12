"""WebRTC-VAD based speech segmenter.

Receives 16kHz mono int16 PCM frames (20ms = 320 samples) and yields
complete utterances when a silence gap follows speech.
"""
from __future__ import annotations

import collections
from typing import Iterator, Optional

import numpy as np
import webrtcvad

from app.config.settings import settings


FRAME_MS = 20
SAMPLE_RATE = 16000
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 320
BYTES_PER_FRAME = FRAME_SAMPLES * 2


class VADSegmenter:
    def __init__(self) -> None:
        self.vad = webrtcvad.Vad(settings.VAD_AGGRESSIVENESS)
        self._buf = bytearray()
        self._speech: list[bytes] = []
        self._silence_ms = 0
        self._speech_ms = 0
        self._min_speech = settings.MIN_SPEECH_MS
        self._silence_end = settings.SILENCE_END_MS

    def feed(self, pcm16_bytes: bytes) -> Iterator[np.ndarray]:
        """Feed raw 16kHz mono PCM bytes; yield numpy int16 utterances."""
        self._buf.extend(pcm16_bytes)
        while len(self._buf) >= BYTES_PER_FRAME:
            frame = bytes(self._buf[:BYTES_PER_FRAME])
            del self._buf[:BYTES_PER_FRAME]
            is_speech = False
            try:
                is_speech = self.vad.is_speech(frame, SAMPLE_RATE)
            except Exception:
                is_speech = False

            if is_speech:
                self._speech.append(frame)
                self._speech_ms += FRAME_MS
                self._silence_ms = 0
            else:
                if self._speech:
                    self._silence_ms += FRAME_MS
                    self._speech.append(frame)  # tail
                    if self._silence_ms >= self._silence_end:
                        if self._speech_ms >= self._min_speech:
                            raw = b"".join(self._speech)
                            yield np.frombuffer(raw, dtype=np.int16)
                        self._speech.clear()
                        self._speech_ms = 0
                        self._silence_ms = 0

    def flush(self) -> Optional[np.ndarray]:
        if self._speech and self._speech_ms >= self._min_speech:
            raw = b"".join(self._speech)
            self._speech.clear()
            self._speech_ms = 0
            self._silence_ms = 0
            return np.frombuffer(raw, dtype=np.int16)
        return None
