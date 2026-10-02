"""TasksSkill — agent-callable task management (Feature D + Feature C).

New. Exposes add/list/complete/delete so the responder's tool loop can act
on the user's tasks. Runs through SkillRunner like any skill (guardrails apply).
"""

from __future__ import annotations

from datetime import datetime

from domain.entities.agent_context import AgentContext
from domain.entities.skill_result import SkillResult
from service.skills.base_skill import BaseSkill
from service.tasks.task_service import TaskService

VALID_ACTIONS = ("add", "list", "complete", "delete")


class TasksSkill(BaseSkill):
    """Manage the user's to-do tasks: add, list, complete, or delete."""

    def __init__(self, service: TaskService, permission_level: str = "notify", enabled: bool = True):
        super().__init__(
            name="tasks",
            description="Manage the user's to-do tasks: add, list, complete, or delete tasks.",
            permission_level=permission_level,
            enabled=enabled,
        )
        self._service = service

    def get_parameters_description(self) -> str:
        return (
            "Parameters: action (str, required: add|list|complete|delete), "
            "title (str, for add), task_id (int, for complete|delete), "
            "notes (str, optional), due_at (ISO datetime, optional)"
        )

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(VALID_ACTIONS)},
                "title": {"type": "string"},
                "task_id": {"type": "integer"},
                "notes": {"type": "string"},
                "due_at": {"type": "string"},
            },
            "required": ["action"],
        }

    async def execute(self, ctx: AgentContext, **params) -> SkillResult:
        action = (params.get("action") or "").lower()
        if action not in VALID_ACTIONS:
            return SkillResult(skill_name=self.name, success=False, error=f"Invalid action '{action}'")
        try:
            if action == "add":
                title = (params.get("title") or "").strip()
                if not title:
                    return SkillResult(skill_name=self.name, success=False, error="title required for add")
                task = self._service.add(
                    title, notes=params.get("notes") or "", due_at=self._parse_due(params.get("due_at"))
                )
                return SkillResult(skill_name=self.name, success=True, output=f"Added task #{task.id}: {task.title}")
            if action == "list":
                tasks = self._service.list(include_done=bool(params.get("include_done")))
                if not tasks:
                    return SkillResult(skill_name=self.name, success=True, output="No pending tasks.")
                lines = [
                    f"#{t.id} {'[x]' if t.done else '[ ]'} {t.title}"
                    + (f" (due {t.due_at.date()})" if t.due_at else "")
                    for t in tasks
                ]
                return SkillResult(skill_name=self.name, success=True, output="\n".join(lines))
            tid = self._coerce_id(params.get("task_id"))
            if tid is None:
                return SkillResult(skill_name=self.name, success=False, error="task_id required")
            if action == "complete":
                ok = self._service.complete(tid)
                return SkillResult(
                    skill_name=self.name, success=ok,
                    output=f"Completed task #{tid}" if ok else None,
                    error=None if ok else f"No task #{tid}",
                )
            ok = self._service.delete(tid)
            return SkillResult(
                skill_name=self.name, success=ok,
                output=f"Deleted task #{tid}" if ok else None,
                error=None if ok else f"No task #{tid}",
            )
        except Exception as e:
            return SkillResult(skill_name=self.name, success=False, error=str(e))

    @staticmethod
    def _coerce_id(value) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_due(value) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None