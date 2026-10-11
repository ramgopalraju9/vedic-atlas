"""SqliteSessionContextRepository — SQLAlchemy implementation of SessionContextPort.

No ORM row crosses this boundary: rows are mapped to the SessionContext entity.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import delete

from domain.entities.session_context import SessionContext
from tpa.persistence.models.session_context import SessionContextRow
from tpa.persistence.session import SessionLocal


def _to_entity(row: SessionContextRow) -> SessionContext:
    return SessionContext(
        session_id=row.session_id, speaker_id=row.speaker_id, tool=row.tool,
        slots=json.loads(row.slots_json or "{}"), updated_at=row.updated_at, expires_at=row.expires_at,
        last_result_ids=json.loads(row.last_result_ids_json or "[]"), pending=json.loads(row.pending_json or "{}"),
    )


class SqliteSessionContextRepository:
    def __init__(self, *, session_factory=None):
        self._session = session_factory or SessionLocal

    def get(self, session_id: str, speaker_id: str, now: datetime) -> SessionContext | None:
        with self._session() as s, s.begin():
            row = s.get(SessionContextRow, (session_id, speaker_id))
            if row is None:
                return None
            if row.expires_at <= now:  # lazy expiry
                s.delete(row)
                return None
            return _to_entity(row)

    def upsert(self, state: SessionContext) -> None:
        with self._session() as s, s.begin():
            row = s.get(SessionContextRow, (state.session_id, state.speaker_id))
            if row is None:
                row = SessionContextRow(session_id=state.session_id, speaker_id=state.speaker_id)
                s.add(row)
            row.tool = state.tool
            row.slots_json = json.dumps(state.slots, default=str, ensure_ascii=False)
            row.last_result_ids_json = json.dumps(state.last_result_ids, ensure_ascii=False)
            row.pending_json = json.dumps(state.pending, default=str, ensure_ascii=False)
            row.updated_at = state.updated_at
            row.expires_at = state.expires_at

    def clear(self, session_id: str, speaker_id: str) -> None:
        with self._session() as s, s.begin():
            s.execute(delete(SessionContextRow).where(
                SessionContextRow.session_id == session_id, SessionContextRow.speaker_id == speaker_id))

    def purge_expired(self, now: datetime) -> int:
        with self._session() as s, s.begin():
            res = s.execute(delete(SessionContextRow).where(SessionContextRow.expires_at <= now))
            return int(res.rowcount or 0)
