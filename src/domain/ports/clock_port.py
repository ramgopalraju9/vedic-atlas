from datetime import datetime
from typing import Protocol, runtime_checkable

@runtime_checkable
class ClockPort(Protocol):
    """Injectable source of the current time."""

    def now(self) -> datetime:
        ...
