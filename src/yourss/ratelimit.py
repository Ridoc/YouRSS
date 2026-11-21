"""Small in-process rate limiter for the single-worker deployment."""

from __future__ import annotations

import math
from collections.abc import Callable
from time import monotonic


class RateLimiter:
    def __init__(self, limit: int, window: float, clock: Callable[[], float] = monotonic) -> None:
        self.limit = limit
        self.window = window
        self.clock = clock
        self._events: dict[str, list[float]] = {}

    def _recent(self, key: str, now: float) -> list[float]:
        cutoff = now - self.window
        events = [stamp for stamp in self._events.get(key, []) if stamp > cutoff]
        if events:
            self._events[key] = events
        else:
            self._events.pop(key, None)
        return events

    def allow(self, key: str, now: float | None = None) -> bool:
        stamp = self.clock() if now is None else now
        events = self._recent(key, stamp)
        if len(events) >= self.limit:
            return False
        events.append(stamp)
        self._events[key] = events
        return True

    def retry_after(self, key: str, now: float | None = None) -> int:
        stamp = self.clock() if now is None else now
        events = self._recent(key, stamp)
        if len(events) < self.limit:
            return 0
        return max(1, math.ceil(events[0] + self.window - stamp))

    def reset(self) -> None:
        self._events.clear()


login_ip_limiter = RateLimiter(5, 60)
login_email_limiter = RateLimiter(3, 3600)
verify_ip_limiter = RateLimiter(20, 60)
auth_general_limiter = RateLimiter(60, 60)


def reset_all() -> None:
    for limiter in (login_ip_limiter, login_email_limiter, verify_ip_limiter, auth_general_limiter):
        limiter.reset()
