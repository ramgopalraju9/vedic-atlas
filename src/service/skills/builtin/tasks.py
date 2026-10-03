"""TasksSkill — agent-callable task management (Feature D + Feature C).

Exposes add/list/complete/delete so the responder's tool loop can act on the
user's tasks. Runs through SkillRunner like any skill (guardrails apply).

Every observation returned here is what the model is allowed to tell the
user, so each one is phrased as a plain fact about what the DB now holds.
`complete`/`delete` accept a task_id OR a free-text `title` ("milk packets")
that is resolved to a unique open task; ambiguous or missing matches are
reported as errors instead of guessing.
"""

from __future__ import annotations

from datetime import datetime

from core.logging_config import logger
from domain.entities.agent_context import AgentContext
from domain.entities.skill_result import SkillResult
from service.skills.base_skill import BaseSkill
from service.tasks.task_service import TaskService

VALID_ACTIONS = ("add", "list", "complete", "delete")
_ALLOWED_PARAMS = frozenset({"action", "title", "task_id", "notes", "due_at", "include_done"})
_MAX_TITLE = 200

_EXAMPLES = """tasks tool examples (user phrase -> your reply):
User: I need to bring vegies
{"tool": "tasks", "args": {"action": "add", "title": "bring vegies"}}
User: remind me to call the bank tomorrow
{"tool": "tasks", "args": {"action": "add", "title": "call the bank", "due_at": "<tomorrow 09:00 ISO>"}}
User: yeah I bought the milk packets
{"tool": "tasks", "args": {"action": "complete", "title": "milk packets"}}
User: I finished the code review
{"tool": "tasks", "args": {"action": "complete", "title": "code review"}}
User: what are my tasks? / what do I need to do?
{"tool": "tasks", "args": {"action": "list"}}
User: which tasks have I completed? / what did I finish?
{"tool": "tasks", "args": {"action": "list", "include_done": true}}
User: forget the bank thing
{"tool": "tasks", "args": {"action": "delete", "title": "bank"}}
User: how are you today?
{"final": "Doing well, thanks. How's your day going?"}
After the tool answers (OBSERVATION), reply with {"final": "..."} saying only what the OBSERVATION says, in one short spoken sentence. If it starts with ERROR, say it did not work and why."""


class TasksSkill(BaseSkill):
    """Manage the user's to-do tasks: add, list, complete, or delete."""

    def __init__(self, service: TaskService, permission_level: str = "notify", enabled: bool = True):
        super().__init__(
            name="tasks",
            description=(
                "The user's to-do list. Use it whenever they mention something they need to do, "
                "ask what is on their list, or say they finished/bought/did something on it."
            ),
            permission_level=permission_level,
            enabled=enabled,
        )
        self._service = service

    def get_parameters_description(self) -> str:
        return (
            "Parameters: action (str, required: add|list|complete|delete), "
            "title (str: the new task for add; a few words naming the task for complete|delete), "
            "task_id (int, optional alternative to title for complete|delete), "
            "notes (str, optional), due_at (ISO datetime, optional)"
        )

    def get_usage_examples(self) -> str:
        return _EXAMPLES

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
            "additionalProperties": False,
        }

    def _fail(self, error: str) -> SkillResult:
        return SkillResult(skill_name=self.name, success=False, error=error)

    def _ok(self, output: str) -> SkillResult:
        return SkillResult(skill_name=self.name, success=True, output=output)

    async def execute(self, ctx: AgentContext, **params) -> SkillResult:
        unknown = set(params) - _ALLOWED_PARAMS
        if unknown:
            return self._fail(f"Unknown parameter(s): {', '.join(sorted(unknown))}")
        action = str(params.get("action") or "").lower()
        if action not in VALID_ACTIONS:
            return self._fail(f"Invalid action '{action}'. Use add, list, complete or delete.")
        try:
            if action == "add":
                return self._add(params)
            if action == "list":
                return self._list(params)
            return self._complete_or_delete(action, params)
        except Exception as e:
            logger.exception("[tasks] %s failed", action)
            return self._fail(str(e))

    # ---- actions --------------------------------------------------------

    def _add(self, params: dict) -> SkillResult:
        title = str(params.get("title") or "").strip()
        if not title:
            return self._fail("title required for add")
        if len(title) > _MAX_TITLE:
            return self._fail(f"title too long (max {_MAX_TITLE} characters)")
        task, created = self._service.add_unique(
            title, notes=str(params.get("notes") or ""), due_at=self._parse_due(params.get("due_at"))
        )
        if not created:
            return self._ok(f"Already on the list as #{task.id}: {task.title}. Nothing added.")
        return self._ok(f"Added task #{task.id}: {task.title}")

    def _list(self, params: dict) -> SkillResult:
        include_done = bool(params.get("include_done"))
        tasks = self._service.list(include_done=include_done)
        if not tasks:
            return self._ok("The task list is empty. No pending tasks.")
        lines = [
            f"#{t.id} {'[done]' if t.done else '[open]'} {t.title}"
            + (f" (due {t.due_at.strftime('%d %b %H:%M')})" if t.due_at else "")
            for t in tasks
        ]
        return self._ok(f"{len(tasks)} task(s):\n" + "\n".join(lines))

    def _complete_or_delete(self, action: str, params: dict) -> SkillResult:
        task = self._resolve(params)
        if isinstance(task, SkillResult):
            return task
        if action == "complete":
            if task.done:
                return self._ok(f"Task #{task.id} ({task.title}) was already done.")
            if not self._service.complete(task.id):
                return self._fail(f"No task #{task.id}")
            return self._ok(f"Completed task #{task.id}: {task.title}")
        if not self._service.delete(task.id):
            return self._fail(f"No task #{task.id}")
        return self._ok(f"Deleted task #{task.id}: {task.title}")

    def _resolve(self, params: dict):
        """-> Task, or a failed SkillResult explaining why it couldn't be resolved."""
        tid = self._coerce_id(params.get("task_id"))
        if tid is not None:
            task = self._service.get(tid)
            return task if task is not None else self._fail(f"No task #{tid}")
        query = str(params.get("title") or "").strip()
        if not query:
            return self._fail("title or task_id required")
        matches = self._service.find_open(query)
        if not matches:
            return self._fail(f"No open task matches '{query}'. Nothing was changed.")
        if len(matches) > 1:
            options = "; ".join(f"#{t.id} {t.title}" for t in matches)
            return self._fail(f"More than one task matches '{query}': {options}. Ask the user which one.")
        logger.info(f"[tasks] resolved '{query}' -> #{matches[0].id} {matches[0].title!r}")
        return matches[0]

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
