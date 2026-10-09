"""SqliteReminderLedger — SQLAlchemy implementation of ReminderLedgerPort."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from sqlalchemy import delete as sa_delete, select

from tpa.persistence.models.reminder_fired import ReminderFiredRow
from tpa.persistence.session import SessionLocal


def _naive(moment: datetime) -> datetime:
    """The column is a naive local DateTime (like every other table here)."""
    return moment.astimezone().replace(tzinfo=None) if moment.tzinfo else moment


class SqliteReminderLedger:
    def __init__(self, *, session_factory=None):
        self._session = session_factory or SessionLocal

    def fired_keys(self, since: datetime) -> set[str]:
        with self._session() as s:
            return set(s.scalars(select(ReminderFiredRow.key).where(ReminderFiredRow.fired_at >= _naive(since))))

    def mark_fired(self, keys: Iterable[str], at: datetime) -> None:
        stamp = _naive(at)
        with self._session() as s, s.begin():
            for key in set(keys):
                row = s.get(ReminderFiredRow, key)
                if row is None:
                    s.add(ReminderFiredRow(key=key, fired_at=stamp))
                else:
                    row.fired_at = stamp

    def purge_before(self, cutoff: datetime) -> int:
        with self._session() as s, s.begin():
            return s.execute(sa_delete(ReminderFiredRow).where(ReminderFiredRow.fired_at < _naive(cutoff))).rowcount or 0
