"""TtlCache — tiny in-memory cache with per-entry expiry.

Weather changes slowly, rates daily: caching saves latency and rate limits and
keeps repeated questions cheap. The clock is injectable so expiry is testable.
"""

from __future__ import annotations

import time
from typing import Any, Callable


class TtlCache:
    def __init__(self, max_entries: int = 256, clock: Callable[[], float] = time.monotonic):
        self._max = max_entries
        self._clock = clock
        self._items: dict[Any, tuple[float, Any]] = {}

    def get(self, key: Any) -> Any | None:
        entry = self._items.get(key)
        if entry is None:
            return None
        expires, value = entry
        if self._clock() >= expires:
            del self._items[key]
            return None
        return value

    def put(self, key: Any, value: Any, ttl_sec: float) -> None:
        if ttl_sec <= 0:
            return
        if len(self._items) >= self._max:
            self._items.pop(next(iter(self._items)))  # oldest-inserted first
        self._items[key] = (self._clock() + ttl_sec, value)
