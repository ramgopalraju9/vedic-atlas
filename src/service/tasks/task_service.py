"""TaskService — use-case facade over a TaskRepositoryPort.

New (Feature D). Thin orchestration so routes, the CLI, and the tasks skill
all go through one place.
"""

from __future__ import annotations

from datetime import datetime

from domain.entities.task import Task
from domain.ports.task_repository_port import TaskRepositoryPort


class TaskService:
    """Add, list, complete, and delete user tasks."""

    def __init__(self, repo: TaskRepositoryPort):
        self._repo = repo

    def add(self, title: str, *, notes: str = "", due_at: datetime | None = None) -> Task:
        task = Task(id=None, title=title, notes=notes, due_at=due_at)
        task.id = self._repo.add(task)
        return task

    def list(self, *, include_done: bool = False) -> list[Task]:
        return self._repo.list(include_done=include_done)

    def complete(self, task_id: int) -> bool:
        return self._repo.set_done(task_id, True)

    def delete(self, task_id: int) -> bool:
        return self._repo.delete(task_id)