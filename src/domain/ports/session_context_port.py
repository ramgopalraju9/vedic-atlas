"""SessionContextPort — persistence for per-(session, speaker) working state."""

from datetime import datetime
from typing import Protocol, runtime_checkable

from domain.entities.session_context import SessionContext


@runtime_checkable
class SessionContextPort(Protocol):
    def get(self, session_id: str, speaker_id: str, now: datetime) -> SessionContext | None:
        """The live state, or None. A row with `expires_at <= now` counts as absent (and is deleted)."""
        ...

    def upsert(self, state: SessionContext) -> None:
        """Insert or replace the single row for (session_id, speaker_id). Never appends."""
        ...

    def clear(self, session_id: str, speaker_id: str) -> None:
        """Remove the row, if any."""
        ...

    def purge_expired(self, now: datetime) -> int:
        """Delete every expired row. Returns the number removed."""
        ...
