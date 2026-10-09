"""ConversationRepositoryPort — persistence for turns and summaries.

Donor: veda/db/conversation_repo.py's ConversationRepository. The
session-boundary concept (`current_session_id`) and the CRUD methods
were directly evidenced by the donor read during Batch 2/3 grounding.
The summariser-support methods (`sessions_with_unsummarized`,
`un_summarized_by_session`, `mark_summarized`, `delete_summarized_before`)
were added after reading veda/brain/summarizer.py in full during Batch 5
grounding — that file calls these exact names on its repo, confirming the
real donor contract this port must satisfy.

The 30-minute session-gap RULE itself is not here — that's a pure
decision function in domain/policies/session_boundary_policy.py; this
port only persists what the policy decides.
Correction (2026-09-22, Batch 7): `recent_turns` was originally designed
here as session-scoped (`recent_turns(session_id, limit)`). Reading
veda/db/conversation_repo.py in full revealed the real donor method has
NO session filter — it returns the most recent un-summarized turns
GLOBALLY, which is what ConversationManager.turns actually surfaces to
agents. Corrected here and in service/conversation/conversation_manager.py
(already landed in Batch 5) to match.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable

from domain.entities.conversation import ConversationSummary, Turn


@runtime_checkable
class ConversationRepositoryPort(Protocol):
    """Reads and writes conversation turns and summaries."""

    def current_session_id(self) -> str:
        """Session id the next turn should join, per the session-boundary policy."""
        ...

    def add_turn(self, turn: Turn) -> int:
        """Insert a turn. Returns its row id."""
        ...

    def recent_turns(
        self, limit: int = 30, only_unsummarized: bool = True, session_id: str | None = None,
    ) -> list[Turn]:
        """Most recent turns, chronological order. Global when `session_id` is None, otherwise that session only."""
        ...

    def add_summary(self, summary: ConversationSummary) -> int:
        """Insert a compacted summary. Returns its row id."""
        ...

    def recent_summaries(self, limit: int = 3) -> list[ConversationSummary]:
        """Most recent compacted summaries, newest first — the older-session tier of recall."""
        ...

    def un_summarized_by_session(self, session_id: str) -> list[Turn]:
        """All not-yet-summarized turns for one session — the summariser's input."""
        ...

    def sessions_with_unsummarized(self) -> list[tuple[str, int, datetime, datetime]]:
        """Every session with pending turns: (session_id, count, from_ts, to_ts)."""
        ...

    def summary_created_at(self, summary_id: int) -> datetime | None:
        """When a summary is dated (the conversation's own day), or None if it does not exist."""
        ...

    def turn_created_at(self, turn_id: int) -> datetime | None:
        """When a turn was said, or None if it no longer exists."""
        ...

    def exchanges(self) -> list[tuple[int, str, str, str, datetime]]:
        """Every question-answer pair, oldest first: (answer turn id, session id, question, answer, answered at)."""
        ...

    def sessions_without_summary(self) -> list[tuple[str, int, datetime, datetime]]:
        """Sessions that have turns but no summary row at all: (session_id, count, from_ts, to_ts)."""
        ...

    def turns_by_session(self, session_id: str) -> list[Turn]:
        """Every turn of one session, summarised or not, oldest first."""
        ...

    def mark_summarized(self, turn_ids: list[int]) -> None:
        """Flag turns as folded into a summary, so they're not picked up again."""
        ...

    def delete_summarized_before(self, cutoff: datetime) -> int:
        """Cold-tier GC: delete already-summarized turns older than cutoff. Returns count deleted."""
        ...