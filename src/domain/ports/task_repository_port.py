"""TaskRepositoryPort — persistence for user tasks.

New (Feature D). Keeps the task use cases independent of SQLAlchemy.
"""

from typing import Protocol, runtime_checkable

from domain.entities.task import Task


@runtime_checkable
class TaskRepositoryPort(Protocol):
    """Reads and writes user tasks."""

    def add(self, task: Task) -> int:
        """Insert a task. Returns its row id."""
        ...

    def list(self, include_done: bool = False) -> list[Task]:
        """Tasks, pending first; pass include_done to also return completed ones."""
        ...

    def get(self, task_id: int) -> Task | None:
        """One task by id, or None."""
        ...

    def set_done(self, task_id: int, done: bool = True) -> bool:
        """Mark a task done/undone. Returns False if the id doesn't exist."""
        ...

    def delete(self, task_id: int) -> bool:
        """Delete a task. Returns False if the id doesn't exist."""
        ...