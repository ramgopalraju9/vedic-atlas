"""ClockPort — the current time, as an injectable seam.

New. No donor equivalent — the donor called `datetime.now()` directly
throughout (conversation_repo.py, agent_memory_repo.py, etc). Session-
boundary and retention policies (domain/policies/) need "now" as an input
to stay pure functions; this port is how the service layer supplies it
without those policies importing `datetime` and reaching for wall-clock
time themselves.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class ClockPort(Protocol):
    """Injectable source of the current time."""

    def now(self) -> datetime:
        ...