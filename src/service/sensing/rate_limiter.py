"""Debouncer + RateLimiter — keep ambient narration from flooding the user.

Donor: veda/bus/rate_limit.py, read in full and ported verbatim. No
behavioural changes — both classes were already framework-free.
"""

from collections import deque
from datetime import datetime


class Debouncer:
    """Suppress repeated events with the same dedupe_key inside a window.

    An empty key bypasses debouncing (nothing is suppressed).
    """

    def __init__(self, window_sec: float = 30.0):
        self.window_sec = window_sec
        self._last_seen: dict[str, datetime] = {}

    def should_emit(self, key: str) -> bool:
        if not key:
            return True
        now = datetime.now()
        last = self._last_seen.get(key)
        if last is not None and (now - last).total_seconds() < self.window_sec:
            return False
        self._last_seen[key] = now
        return True

    def forget(self, key: str) -> None:
        self._last_seen.pop(key, None)


class RateLimiter:
    """Cap total ambient narrations per window — a global ceiling."""

    def __init__(self, max_events: int = 6, window_sec: float = 60.0):
        self.max_events = max_events
        self.window_sec = window_sec
        self._stamps: deque[datetime] = deque()

    def allow(self) -> bool:
        now = datetime.now()
        while self._stamps and (now - self._stamps[0]).total_seconds() > self.window_sec:
            self._stamps.popleft()
        if len(self._stamps) >= self.max_events:
            return False
        self._stamps.append(now)
        return True