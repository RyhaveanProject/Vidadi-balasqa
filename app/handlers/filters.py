"""Owner-only filter + simple per-user rate limiter."""
from __future__ import annotations

import time
from collections import defaultdict

from pyrogram import filters
from pyrogram.types import Message

from app.config.settings import settings


def owner_only():
    async def _f(_, __, m: Message) -> bool:
        return bool(m.from_user and m.from_user.id == settings.OWNER_ID)
    return filters.create(_f)


class RateLimiter:
    def __init__(self, per_seconds: float = 4.0) -> None:
        self._last: dict[int, float] = defaultdict(float)
        self.per = per_seconds

    def allow(self, user_id: int) -> bool:
        now = time.time()
        if now - self._last[user_id] < self.per:
            return False
        self._last[user_id] = now
        return True
