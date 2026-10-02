"""ConversationRepository – SQLAlchemy implementation of ConversationRepositoryPort.

Donor: veda/db/conversation_repo.py, read in full (this batch, completing
grounding that started in Batch 3/5). Ported closely:
  - `current_session_id` – the real 30-minute gap logic. Note: the pure
    gap DECISION is domain.policies.session_boundary_policy.is_same_session
    (Batch 4); this method still needs to query "when was the last turn"
    (I/O), so it calls that pure function rather than re-implementing the
    comparison inline – the donor inlined the comparison, this repo
    delegates the decision to keep the rule in exactly one place.
  - `add_turn` takes a `Turn` entity directly (per the corrected Port
    signature) rather than resolving session_id itself – the donor's
    version took `(role, content)` and resolved the session internally;
    that resolution now happens one layer up, in
    service/conversation/conversation_manager.py.
  - `recent_turns` has NO session filter – matches the real donor
    behaviour (see the Batch 7 correction note in the ledger), not the
    session-scoped version I incorrectly designed in Batch 3.
  - All ORM rows are converted via tpa/persistence/mappers.py before
    returning – no `Mapped` type crosses this module's boundary.
"""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from sqlalchemy import delete, func, select, update

from domain.entities.conversation import ConversationSummary, Turn
from domain.policies.session_boundary_policy import DEFAULT_SESSION_GAP_MIN, is_same_session
from tpa.persistence.mappers import summary_row_to_entity, turn_row_to_entity
from tpa.persistence.models.conversation import ConversationSummaryRow, ConversationTurnRow
from tpa.persistence.session import SessionLocal


class ConversationRepository:
    """SQLAlchemy-backed implementation of ConversationRepositoryPort."""

    def __init__(self, session_gap_min: int = DEFAULT_SESSION_GAP_MIN, *, session_factory=None):
        self.session_gap_min = session_gap_min
        self._session = session_factory or SessionLocal

    @staticmethod
    def _mint_session_id() -> str:
        import uuid

        return uuid.uuid4().hex[:8]

    def current_session_id(self, *, now: datetime | None = None) -> str:
        """Lazy: does NOT insert a placeholder row. Empty DB -> freshly minted id."""
        now = now or datetime.now()
        with self._session() as s:
            last = s.scalar(
                select(ConversationTurnRow).order_by(
                    ConversationTurnRow.created_at.desc(), ConversationTurnRow.id.desc()
                )
            )
        if last is None:
            return self._mint_session_id()
        if is_same_session(last.created_at, now, self.session_gap_min):
            return last.session_id
        return self._mint_session_id()

    def add_turn(self, turn: Turn) -> int:
        if not turn.role or not turn.content:
            raise ValueError("role and content are required")
        with self._session() as s, s.begin():
            row = ConversationTurnRow(
                session_id=turn.session_id,
                role=turn.role,
                content=turn.content,
                created_at=turn.created_at,
                summarized=turn.summarized,
            )
            s.add(row)
            s.flush()
            return row.id

    def recent_turns(self, limit: int = 10, only_unsummarized: bool = True) -> list[Turn]:
        """Most recent turns GLOBALLY (no session filter) – matches the donor."""
        with self._session() as s:
            stmt = select(ConversationTurnRow)
            if only_unsummarized:
                stmt = stmt.where(ConversationTurnRow.summarized == False)  # noqa: E712
            stmt = stmt.order_by(
                ConversationTurnRow.created_at.desc(), ConversationTurnRow.id.desc()
            ).limit(limit)
            rows = list(s.scalars(stmt))
        rows.reverse()  # chronological
        return [turn_row_to_entity(r) for r in rows]

    def un_summarized_by_session(self, session_id: str) -> list[Turn]:
        with self._session() as s:
            stmt = (
                select(ConversationTurnRow)
                .where(ConversationTurnRow.session_id == session_id, ConversationTurnRow.summarized == False)  # noqa: E712
                .order_by(ConversationTurnRow.created_at.asc(), ConversationTurnRow.id.asc())
            )
            rows = list(s.scalars(stmt))
        return [turn_row_to_entity(r) for r in rows]

    def sessions_with_unsummarized(self) -> list[tuple[str, int, datetime, datetime]]:
        with self._session() as s:
            rows = s.execute(
                select(
                    ConversationTurnRow.session_id,
                    func.count(ConversationTurnRow.id),
                    func.min(ConversationTurnRow.created_at),
                    func.max(ConversationTurnRow.created_at),
                )
                .where(ConversationTurnRow.summarized == False)  # noqa: E712
                .group_by(ConversationTurnRow.session_id)
                .order_by(func.min(ConversationTurnRow.created_at).asc())
            ).all()
        return [(sid, int(cnt), lo, hi) for sid, cnt, lo, hi in rows]

    def mark_summarized(self, turn_ids: Sequence[int]) -> None:
        if not turn_ids:
            return
        with self._session() as s, s.begin():
            s.execute(update(ConversationTurnRow).where(ConversationTurnRow.id.in_(list(turn_ids))).values(summarized=True))

    def add_summary(self, summary: ConversationSummary) -> int:
        if not summary.content:
            raise ValueError("summary content required")
        with self._session() as s, s.begin():
            row = ConversationSummaryRow(
                session_id=summary.session_id,
                from_ts=summary.from_ts,
                to_ts=summary.to_ts,
                content=summary.content,
                turn_count=summary.turn_count,
            )
            s.add(row)
            s.flush()
            return row.id

    def recent_summaries(self, limit: int = 3) -> list[ConversationSummary]:
        with self._session() as s:
            rows = list(s.scalars(select(ConversationSummaryRow).order_by(ConversationSummaryRow.created_at.desc()).limit(limit)))
        return [summary_row_to_entity(r) for r in rows]

    def delete_summarized_before(self, cutoff: datetime) -> int:
        with self._session() as s, s.begin():
            res = s.execute(
                delete(ConversationTurnRow).where(
                    ConversationTurnRow.summarized == True, ConversationTurnRow.created_at < cutoff  # noqa: E712
                )
            )
            return int(res.rowcount or 0)
