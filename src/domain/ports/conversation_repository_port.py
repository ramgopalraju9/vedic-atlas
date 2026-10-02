from datetime import datetime
from typing import Protocol, runtime_checkable
from domain.entities.conversation import ConversationSummary, Turn

@runtime_checkable
class ConversationRepositoryPort(Protocol):
    """Reads and writes conversation turns and summaries."""

    def current_session_id(self) -> str:
        """Session id the next turn should join."""
        ...

    def add_turn(self, turn: Turn) -> int:
        """Insert a turn. Returns its row id."""
        ...

    def recent_turns(self, limit: int = 30, only_unsummarized: bool = True) -> list[Turn]:
        """Most recent turns globally, chronological order."""
        ...

    def add_summary(self, summary: ConversationSummary) -> int:
        """Insert a compacted summary. Returns its row id."""
        ...

    def recent_summaries(self, limit: int = 3) -> list[ConversationSummary]:
        """Most recent compacted summaries, newest first."""
        ...

    def un_summarized_by_session(self, session_id: str) -> list[Turn]:
        """All not-yet-summarized turns for one session."""
        ...

    def sessions_with_unsummarized(self) -> list[tuple[str, int, datetime, datetime]]:
        """Every session with unsummarized turns."""
        ...

    def mark_summarized(self, turn_ids: list[int]) -> None:
        """Flag turns as folded into a summary."""
        ...

    def delete_summarized_before(self, cutoff: datetime) -> int:
        """Delete summarized turns older than cutoff. Returns count deleted."""
        ...
