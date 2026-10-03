"""TraceRepositoryPort — persistence for tool-turn traces."""

from datetime import datetime
from typing import Protocol, runtime_checkable

from domain.entities.turn_trace import TurnTrace


@runtime_checkable
class TraceRepositoryPort(Protocol):
    def record(self, trace: TurnTrace) -> int:
        """Persist one trace. Returns its row id."""
        ...

    def recent(self, limit: int = 20) -> list[TurnTrace]:
        """Newest first."""
        ...

    def purge_before(self, cutoff: datetime) -> int:
        """Delete traces older than `cutoff`. Returns the number removed."""
        ...
