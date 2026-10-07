"""ConversationManager — bounded conversation history, facade over a repository.

Donor: veda/brain/conversation.py, read in full and adapted:
  - DROPPED the one-time JSON-to-SQLite migration (`_migrate_json_if_needed`,
    `_rename_legacy`, the `CONVERSATION_FILE` import). That logic exists
    only to upgrade an existing VEDA install that still had
    `data/conversation.json` from before the SQLite migration happened.
    This is a fresh product with no such legacy file to import.
  - Depends on ConversationRepositoryPort (Batch 3) rather than
    constructing a concrete `ConversationRepository` itself — the donor's
    `init_db()` call and direct repo construction move to server.py's
    composition step.
  - `add_turn` / `turns` / `get_context_summary` kept with identical
    external shape so agents built against this class don't need edits
    later, matching the donor's own stated design goal.
"""

from __future__ import annotations

from domain.entities.conversation import Turn
from domain.ports.conversation_repository_port import ConversationRepositoryPort
from core.logging_config import logger


class ConversationManager:
    """Stores and retrieves conversation turns via a ConversationRepositoryPort."""

    def __init__(self, repo: ConversationRepositoryPort, max_history: int = 30, summaries_limit: int = 3):
        self.repo = repo
        self.max_history = max_history
        self.summaries_limit = summaries_limit

    def current_session_id(self) -> str:
        """The session a turn happening now belongs to (30-minute activity gap, resolved by the repo)."""
        return self.repo.current_session_id()

    def add_turn(self, role: str, content: str, session_id: str | None = None) -> None:
        """Insert a new turn. `session_id` is the one the caller already resolved for this turn; when omitted
        it resolves via the repo (so a turn straddling the 30-minute boundary can't land in two sessions)."""
        try:
            from datetime import datetime

            self.repo.add_turn(
                Turn(
                    id=None,
                    session_id=session_id or self.repo.current_session_id(),
                    role=role,
                    content=content,
                    created_at=datetime.now(),
                )
            )
        except Exception as e:
            # Don't let a persistence hiccup kill the response path.
            logger.warning(f"[conversation] add_turn failed: {e}")

    @property
    def turns(self) -> list[Turn]:
        """Recent, un-summarised turns of the CURRENT session only. An earlier session reaches the model through its
        summary; a new session (30-minute gap) correctly starts with an empty history."""
        return self.turns_in(self.repo.current_session_id())

    def turns_in(self, session_id: str) -> list[Turn]:
        limit = max(self.max_history * 2, 1)
        return self.repo.recent_turns(limit=limit, only_unsummarized=True, session_id=session_id)

    def get_summaries_block(self, limit: int | None = None) -> str:
        """Older-session summaries (compacted by ConversationSummariser), oldest first."""
        limit = self.summaries_limit if limit is None else limit
        if limit <= 0:
            return ""
        try:
            gists = self.repo.recent_summaries(limit=limit)
        except Exception as e:
            logger.warning(f"[conversation] recent_summaries failed: {e}")
            return ""
        if not gists:
            return ""
        lines = ["EARLIER SESSIONS (compressed):"]
        for g in reversed(gists):  # chronological
            lines.append(f"- {g.created_at.date()}: {g.content}")
        return "\n".join(lines)

    def get_context_summary(self) -> str:
        """Tiered recall: older-session summaries (compacted by
        ConversationSummariser) plus recent raw turns — matches the donor's
        `brain/conversation.py::get_context_summary` shape exactly."""
        summaries = self.get_summaries_block()
        recent = self.turns
        if not summaries and not recent:
            return ""
        parts: list[str] = []
        if summaries:
            parts.append(summaries)
        if recent:
            parts.append("RECENT CONVERSATION:")
            for t in recent:
                prefix = "User" if t.role == "user" else "Veda"
                parts.append(f"{prefix}: {t.content}")
        return "\n".join(parts)