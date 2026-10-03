"""SqliteTraceRepository — SQLAlchemy implementation of TraceRepositoryPort.

No ORM row crosses this boundary: rows are mapped to the TurnTrace entity.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import delete, select

from domain.entities.turn_trace import TurnTrace
from tpa.persistence.models.turn_trace import TurnTraceRow
from tpa.persistence.session import SessionLocal


def _to_entity(row: TurnTraceRow) -> TurnTrace:
    meta = json.loads(row.meta_json or "{}")
    return TurnTrace(
        id=row.id, request_id=row.request_id, created_at=row.created_at, agent=row.agent,
        user_message=row.user_message, reply=row.reply, decided=row.decided, forced=row.forced,
        narrated=row.narrated, total_ms=row.total_ms, calls=json.loads(row.calls_json or "[]"),
        prompt_tokens=meta.get("prompt_tokens", {}), timings_ms=meta.get("timings_ms", {}),
        notes=meta.get("notes", []),
    )


class SqliteTraceRepository:
    def __init__(self, *, session_factory=None):
        self._session = session_factory or SessionLocal

    def record(self, trace: TurnTrace) -> int:
        with self._session() as s, s.begin():
            row = TurnTraceRow(
                request_id=trace.request_id, created_at=trace.created_at, agent=trace.agent,
                user_message=trace.user_message, reply=trace.reply, decided=trace.decided,
                forced=trace.forced, narrated=trace.narrated, total_ms=trace.total_ms,
                calls_json=json.dumps(trace.calls, default=str, ensure_ascii=False),
                meta_json=json.dumps(
                    {"prompt_tokens": trace.prompt_tokens, "timings_ms": trace.timings_ms, "notes": trace.notes},
                    ensure_ascii=False,
                ),
            )
            s.add(row)
            s.flush()
            return row.id

    def recent(self, limit: int = 20) -> list[TurnTrace]:
        with self._session() as s:
            rows = s.scalars(
                select(TurnTraceRow).order_by(TurnTraceRow.created_at.desc(), TurnTraceRow.id.desc()).limit(limit)
            )
            return [_to_entity(r) for r in rows]

    def purge_before(self, cutoff: datetime) -> int:
        with self._session() as s, s.begin():
            res = s.execute(delete(TurnTraceRow).where(TurnTraceRow.created_at < cutoff))
            return int(res.rowcount or 0)
