"""Simple in-memory rate limiter."""
import time
from collections import defaultdict


class TokenBucket:
    def __init__(self, capacity: int = 5, refill_per_sec: float = 0.5) -> None:
        self.capacity = capacity
        self.refill = refill_per_sec
        self._tokens: dict[int, float] = defaultdict(lambda: capacity)
        self._ts: dict[int, float] = defaultdict(time.time)

    def allow(self, key: int) -> bool:
        now = time.time()
        elapsed = now - self._ts[key]
        self._tokens[key] = min(self.capacity, self._tokens[key] + elapsed * self.refill)
        self._ts[key] = now
        if self._tokens[key] >= 1:
            self._tokens[key] -= 1
            return True
        return False
