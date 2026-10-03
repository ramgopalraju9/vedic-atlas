"""TaskService — use-case facade over a TaskRepositoryPort.

New (Feature D). Thin orchestration so routes, the CLI, and the tasks skill
all go through one place.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from domain.entities.task import Task
from domain.policies.retention_policy import COMPLETED_TASK_RETENTION_DAYS
from domain.policies.task_matching import best_matches, normalize_title
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

    def add_unique(
        self, title: str, *, notes: str = "", due_at: datetime | None = None
    ) -> tuple[Task, bool]:
        """Add unless an open task with the same meaning exists. -> (task, created)."""
        wanted = normalize_title(title)
        for t in self._repo.list(include_done=False):
            if normalize_title(t.title) == wanted:
                return t, False
        return self.add(title, notes=notes, due_at=due_at), True

    def find_open(self, query: str) -> list[Task]:
        """Open tasks that best match a spoken phrase (see domain.policies.task_matching)."""
        open_tasks = {t.id: t for t in self._repo.list(include_done=False)}
        ids = best_matches(query, {i: t.title for i, t in open_tasks.items()})
        return [open_tasks[i] for i in ids]

    def get(self, task_id: int) -> Task | None:
        return self._repo.get(task_id)

    def purge_completed(self, *, now: datetime | None = None) -> int:
        """Drop tasks completed longer ago than the retention window."""
        cutoff = (now or datetime.now()) - timedelta(days=COMPLETED_TASK_RETENTION_DAYS)
        return self._repo.purge_completed_before(cutoff)
