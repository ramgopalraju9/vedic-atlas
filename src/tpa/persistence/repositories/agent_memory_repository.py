"""AgentMemoryRepository — SQLAlchemy implementation of MemoryRepositoryPort.

Donor: veda/db/agent_memory_repo.py, read in full. Ported closely:
  - `record` still resolves `session_id` via the injected
    `ConversationRepository` (donor design: memory rows and conversation
    turns share the same session boundary — kept, it's a real, sound
    decision, not incidental coupling).
  - `RETENTION_DAYS = 10` pruning-on-every-write behaviour kept verbatim
    (this is also the constant `domain.policies.retention_policy` uses
    for its pure `is_expired_agent_memory` check — the two now agree by
    construction rather than by coincidence).
  - `last_specialist` / `last_project` (donor-specific supervisor
    helpers) intentionally NOT ported here — see the Batch 6 ledger note
    on why `last_action(agent_name="supervisor")` replaces the former,
    and why the latter had no confirmed caller anywhere in this
    migration.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select

from domain.entities.memory_record import MemoryRecord
from domain.policies.retention_policy import AGENT_MEMORY_RETENTION_DAYS
from tpa.persistence.mappers import memory_row_to_entity
from tpa.persistence.models.agent_memory import AgentMemoryRow
from tpa.persistence.repositories.conversation_repository import ConversationRepository
from tpa.persistence.session import SessionLocal


class AgentMemoryRepository:
    """SQLAlchemy-backed implementation of MemoryRepositoryPort."""

    def __init__(self, *, session_factory=None, conversation_repo: ConversationRepository | None = None):
        self._session = session_factory or SessionLocal
        self._conv = conversation_repo or ConversationRepository(session_factory=self._session)

    def record(self, agent_name: str, action: str, context: dict[str, Any] | None = None, user_message: str = "") -> int:
        now = datetime.now()
        session_id = self._conv.current_session_id(now=now)
        ctx_json = json.dumps(context or {}, default=str)
        with self._session() as s, s.begin():
            row = AgentMemoryRow(
                session_id=session_id, agent_name=agent_name, action=action,
                context_json=ctx_json, user_message=user_message, created_at=now,
            )
            s.add(row)
            s.flush()
            row_id = row.id
            cutoff = now - timedelta(days=AGENT_MEMORY_RETENTION_DAYS)
            s.execute(delete(AgentMemoryRow).where(AgentMemoryRow.created_at < cutoff))
            return row_id

    def prune(self, *, now: datetime | None = None) -> int:
        now = now or datetime.now()
        cutoff = now - timedelta(days=AGENT_MEMORY_RETENTION_DAYS)
        with self._session() as s, s.begin():
            result = s.execute(delete(AgentMemoryRow).where(AgentMemoryRow.created_at < cutoff))
            return result.rowcount

    def last_action(self, agent_name: str | None = None) -> MemoryRecord | None:
        with self._session() as s:
            stmt = select(AgentMemoryRow)
            if agent_name:
                stmt = stmt.where(AgentMemoryRow.agent_name == agent_name)
            stmt = stmt.order_by(AgentMemoryRow.created_at.desc(), AgentMemoryRow.id.desc()).limit(1)
            row = s.scalar(stmt)
        return memory_row_to_entity(row) if row else None

    def recent(self, limit: int = 5) -> list[MemoryRecord]:
        with self._session() as s:
            rows = list(s.scalars(select(AgentMemoryRow).order_by(AgentMemoryRow.created_at.desc(), AgentMemoryRow.id.desc()).limit(limit)))
        rows.reverse()
        return [memory_row_to_entity(r) for r in rows]

    def recent_cross_agent(self, exclude: set[str] | None = None, limit: int = 5) -> list[MemoryRecord]:
        with self._session() as s:
            stmt = select(AgentMemoryRow)
            if exclude:
                stmt = stmt.where(AgentMemoryRow.agent_name.notin_(exclude))
            stmt = stmt.order_by(AgentMemoryRow.created_at.desc(), AgentMemoryRow.id.desc()).limit(limit)
            rows = list(s.scalars(stmt))
        rows.reverse()
        return [memory_row_to_entity(r) for r in rows]