"""ReminderFeed — the last few spoken reminders as text, so a terminal client can print them.

In memory and bounded. A client remembers the last `seq` it saw and asks for what came after.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime

_KEEP = 50


class ReminderFeed:
    def __init__(self, keep: int = _KEEP):
        self._items: deque[dict] = deque(maxlen=keep)
        self._seq = 0

    @property
    def last_seq(self) -> int:
        return self._seq

    def publish(self, text: str, at: datetime) -> int:
        self._seq += 1
        self._items.append({"seq": self._seq, "at": at.isoformat(timespec="seconds"), "text": text})
        return self._seq

    def since(self, after: int) -> list[dict]:
        return [dict(i) for i in self._items if i["seq"] > after]
