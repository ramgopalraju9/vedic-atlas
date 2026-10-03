"""SqliteTaskRepository — SQLAlchemy implementation of TaskRepositoryPort.

New (Feature D). No ORM row crosses this boundary — rows are mapped to the
domain Task entity before returning.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete as sa_delete, select, update

from domain.entities.task import Task
from tpa.persistence.models.task import TaskRow
from tpa.persistence.session import SessionLocal


def _to_entity(row: TaskRow) -> Task:
    return Task(
        id=row.id, title=row.title, done=row.done, notes=row.notes,
        due_at=row.due_at, created_at=row.created_at, completed_at=row.completed_at,
    )


class SqliteTaskRepository:
    """SQLAlchemy-backed implementation of TaskRepositoryPort."""

    def __init__(self, *, session_factory=None):
        self._session = session_factory or SessionLocal

    def add(self, task: Task) -> int:
        if not (task.title or "").strip():
            raise ValueError("task title required")
        with self._session() as s, s.begin():
            row = TaskRow(
                title=task.title.strip(), done=task.done, notes=task.notes,
                due_at=task.due_at, created_at=task.created_at,
            )
            s.add(row)
            s.flush()
            return row.id

    def list(self, include_done: bool = False) -> list[Task]:
        with self._session() as s:
            stmt = select(TaskRow)
            if not include_done:
                stmt = stmt.where(TaskRow.done == False)  # noqa: E712
            stmt = stmt.order_by(TaskRow.done.asc(), TaskRow.created_at.asc())
            return [_to_entity(r) for r in s.scalars(stmt)]

    def get(self, task_id: int) -> Task | None:
        with self._session() as s:
            row = s.get(TaskRow, task_id)
            return _to_entity(row) if row is not None else None

    def set_done(self, task_id: int, done: bool = True) -> bool:
        with self._session() as s, s.begin():
            res = s.execute(
                update(TaskRow).where(TaskRow.id == task_id).values(
                    done=done, completed_at=datetime.now() if done else None
                )
            )
            return res.rowcount > 0

    def delete(self, task_id: int) -> bool:
        with self._session() as s, s.begin():
            res = s.execute(sa_delete(TaskRow).where(TaskRow.id == task_id))
            return res.rowcount > 0

    def purge_completed_before(self, cutoff: datetime) -> int:
        with self._session() as s, s.begin():
            res = s.execute(
                sa_delete(TaskRow).where(TaskRow.done == True, TaskRow.completed_at < cutoff)  # noqa: E712
            )
            return int(res.rowcount or 0)
