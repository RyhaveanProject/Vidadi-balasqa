"""Speaker recognition — lightweight MFCC-cosine-similarity fingerprint store.

Designed to be free-tier friendly: no pyannote / heavy DNN models.
Stores a small numeric vector per known speaker; matches by cosine distance.
"""
from __future__ import annotations

import time
from typing import Optional, Tuple

import numpy as np
import aiosqlite

from app.config.settings import settings
from app.core.logger import log


def _to_blob(arr: np.ndarray) -> bytes:
    return arr.astype(np.float32).tobytes()


def _from_blob(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    if a.size == 0 or b.size == 0:
        return 0.0
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def fingerprint(pcm16: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
    """Very cheap voice fingerprint: log-mel-ish 32-dim summary stats.

    pcm16: int16 numpy array, mono.
    """
    if pcm16.size < sample_rate // 2:  # need at least 0.5s
        return np.zeros(32, dtype=np.float32)
    x = pcm16.astype(np.float32) / 32768.0
    # frame
    frame = 400  # 25ms @16k
    hop = 160    # 10ms
    if x.size < frame:
        return np.zeros(32, dtype=np.float32)
    n_frames = 1 + (x.size - frame) // hop
    spec = []
    win = np.hanning(frame)
    for i in range(min(n_frames, 200)):
        seg = x[i * hop:i * hop + frame] * win
        mag = np.abs(np.fft.rfft(seg))
        spec.append(mag)
    spec = np.array(spec)
    # 16 mel-ish bands by averaging contiguous bins
    bands = np.array_split(spec, 16, axis=1)
    band_energy = np.array([np.log1p(b.mean()) for b in bands])
    band_std = np.array([np.log1p(b.std()) for b in bands])
    vec = np.concatenate([band_energy, band_std]).astype(np.float32)
    n = np.linalg.norm(vec)
    return vec / n if n > 0 else vec


async def add_sample(user_id: int, name: str, pcm16: np.ndarray, sample_rate: int = 16000) -> None:
    emb = fingerprint(pcm16, sample_rate)
    if np.linalg.norm(emb) == 0:
        return
    duration = pcm16.size / sample_rate
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute(
            "INSERT INTO voice_samples(user_id, name, embedding, duration, ts) VALUES (?,?,?,?,?)",
            (user_id, name, _to_blob(emb), duration, int(time.time())),
        )
        await db.commit()


async def identify(pcm16: np.ndarray, sample_rate: int = 16000, threshold: float = 0.72) -> Optional[Tuple[int, str, float]]:
    emb = fingerprint(pcm16, sample_rate)
    if np.linalg.norm(emb) == 0:
        return None
    best = None
    async with aiosqlite.connect(settings.DB_PATH) as db:
        cur = await db.execute("SELECT user_id, name, embedding FROM voice_samples")
        rows = await cur.fetchall()
    for uid, name, blob in rows:
        score = _cosine(emb, _from_blob(blob))
        if best is None or score > best[2]:
            best = (uid, name, score)
    if best and best[2] >= threshold:
        log.debug("speaker identified: {} ({:.2f})", best[1], best[2])
        return best
    return None
