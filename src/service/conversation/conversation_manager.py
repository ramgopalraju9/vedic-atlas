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

from typing import Callable

from domain.entities.conversation import Turn
from domain.policies.exchange_note_policy import exchange_note
from domain.policies.day_label_policy import day_label
from domain.ports.conversation_repository_port import ConversationRepositoryPort
from core.logging_config import logger


class ConversationManager:
    """Stores and retrieves conversation turns via a ConversationRepositoryPort."""

    def __init__(
        self, repo: ConversationRepositoryPort, max_history: int = 30, summaries_limit: int = 3,
        on_exchange: Callable[[int, str], None] | None = None,
    ):
        self.repo = repo
        # Called with (answer turn id, note) when a question and its answer are both saved; used to index them for recall.
        self._on_exchange = on_exchange
        self._pending_question: dict[str, str] = {}
        self.max_history = max_history
        self.summaries_limit = summaries_limit

    def current_session_id(self) -> str:
        """The session a turn happening now belongs to (30-minute activity gap, resolved by the repo)."""
        return self.repo.current_session_id()

    def add_turn(self, role: str, content: str, session_id: str | None = None) -> int | None:
        """Insert a new turn and return its id (None if saving failed). `session_id` is the one the caller already
        resolved for this turn; when omitted it resolves via the repo (so a turn straddling the 30-minute boundary can't
        land in two sessions). A user turn followed by an assistant turn of the same session is one exchange."""
        try:
            from datetime import datetime

            sid = session_id or self.repo.current_session_id()
            turn_id = self.repo.add_turn(
                Turn(id=None, session_id=sid, role=role, content=content, created_at=datetime.now())
            )
        except Exception as e:
            # Don't let a persistence hiccup kill the response path.
            logger.warning(f"[conversation] add_turn failed: {e}")
            return None
        if role == "user":
            self._pending_question[sid] = content
        elif role == "assistant" and sid in self._pending_question:
            question = self._pending_question.pop(sid)
            if self._on_exchange is not None:
                try:
                    note = exchange_note(question, content)
                    if note:
                        self._on_exchange(turn_id, note)
                except Exception as e:   # indexing is an extra; the turn is already saved
                    logger.warning(f"[conversation] exchange hook failed: {e}")
        return turn_id

    @property
    def turns(self) -> list[Turn]:
        """Recent turns of the CURRENT session only, summarised or not. An earlier session reaches the model through its
        summary; a new session (30-minute gap) correctly starts with an empty history. Turns the summariser has already
        folded into a summary stay visible while their session is still live (they used to vanish mid-conversation)."""
        return self.turns_in(self.repo.current_session_id())

    def turns_in(self, session_id: str) -> list[Turn]:
        limit = max(self.max_history * 2, 1)
        return self.repo.recent_turns(limit=limit, only_unsummarized=False, session_id=session_id)

    def get_summaries_block(self, limit: int | None = None) -> str:
        """Older-session summaries (compacted by ConversationSummariser), oldest first."""
        limit = self.summaries_limit if limit is None else limit
        if limit <= 0:
            return ""
        try:
            current = self.repo.current_session_id()
            # The live session's own summary would repeat the raw turns shown beside it.
            gists = [g for g in self.repo.recent_summaries(limit=limit + 1) if g.session_id != current][:limit]
        except Exception as e:
            logger.warning(f"[conversation] recent_summaries failed: {e}")
            return ""
        if not gists:
            return ""
        lines = ["EARLIER SESSIONS (compressed):"]
        for g in reversed(gists):  # chronological
            lines.append(f"- {g.created_at.date()}: {g.content}")
        return "\n".join(lines)

    def recent_summaries_block(self, limit: int = 3, max_hit_chars: int = 280, max_total_chars: int = 800) -> str:
        """The newest earlier conversations, newest first, each dated and capped. This is what "what are we discussing?" is
        answered from when the live session has little to say; it ignores the profile's `summaries_limit`, because the
        user asked for it. The live session's own summary is left out (its raw turns are in the prompt)."""
        from service.conversation.backfill import cap_summary

        try:
            current = self.repo.current_session_id()
            gists = [g for g in self.repo.recent_summaries(limit=limit + 1) if g.session_id != current][:limit]
        except Exception as e:
            logger.warning(f"[conversation] recent_summaries failed: {e}")
            return ""
        lines: list[str] = []
        used = 0
        for g in gists:   # repo order is newest first
            line = f"- ({day_label(g.created_at)}) {cap_summary(g.content, max_hit_chars)}"
            if lines and used + len(line) > max_total_chars:
                break
            lines.append(line)
            used += len(line)
        return "RECENT CONVERSATIONS (newest first):\n" + "\n".join(lines) if lines else ""

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