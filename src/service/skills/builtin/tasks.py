"""TasksSkill — agent-callable task management (Feature D + Feature C).

Exposes add/list/complete/delete so the responder's tool loop can act on the
user's tasks. Runs through SkillRunner like any skill (guardrails apply).

Every observation returned here is what the model is allowed to tell the
user, so each one is phrased as a plain fact about what the DB now holds.
`complete`/`delete` accept a task_id OR a free-text `title` ("milk packets")
that is resolved to a unique open task; ambiguous or missing matches are
reported as errors instead of guessing.

Name, description, argument schema and examples come from the tool's manifest
(config/tools/tasks.yaml); this class only holds behaviour. Each result also
carries a `spoken` sentence in metadata, which is the user-facing reply when
the manifest's reply_mode is "template" (no second model call, nothing the
model can embellish).
"""

from __future__ import annotations

from datetime import datetime

from core.logging_config import logger
from domain.entities.agent_context import AgentContext
from domain.entities.skill_result import SkillResult
from domain.entities.tool_manifest import ToolManifest
from domain.policies.calendar_policy import looks_like_calendar_entry
from service.skills.manifest_skill import ManifestSkill
from service.tasks.task_service import TaskService

VALID_ACTIONS = ("add", "list", "complete", "delete")
_MAX_TITLE = 200
_MAX_SPOKEN_TITLES = 8


class TasksSkill(ManifestSkill):
    """Manage the user's to-do tasks: add, list, complete, or delete."""

    what = "update your tasks"

    def __init__(
        self,
        service: TaskService,
        manifest: ToolManifest,
        permission_level: str | None = None,
        enabled: bool = True,
    ):
        super().__init__(manifest, permission_level=permission_level, enabled=enabled)
        self._service = service

    async def run(self, ctx: AgentContext, **params) -> SkillResult:
        action = str(params.get("action") or "").lower()
        if action not in VALID_ACTIONS:
            return self._fail(f"Invalid action '{action}'. Use add, list, complete or delete.")
        if action == "add":
            return self._add(params)
        if action == "list":
            return self._list(params)
        return self._complete_or_delete(action, params)

    # ---- actions --------------------------------------------------------

    def _add(self, params: dict) -> SkillResult:
        title = str(params.get("title") or "").strip()
        if not title:
            return self._fail("title required for add", "What should I add?")
        if looks_like_calendar_entry(title):
            # "block calendar for Manoj" is an event, not a to-do; saying so beats filing it where it will never alert anyone.
            return self._fail(
                "title describes a calendar event, not a task",
                "That sounds like a calendar event rather than a task. Tell me the day and time and I'll add it to your calendar.",
            )
        if len(title) > _MAX_TITLE:
            return self._fail(f"title too long (max {_MAX_TITLE} characters)", "That task title is too long.")
        task, created = self._service.add_unique(
            title, notes=str(params.get("notes") or ""), due_at=self._parse_due(params.get("due_at"))
        )
        if not created:
            return self._ok(
                f"Already on the list as #{task.id}: {task.title}. Nothing added.",
                f"{task.title} is already on your list.",
            )
        return self._ok(f"Added task #{task.id}: {task.title}", f"Added {task.title} to your list.")

    def _list(self, params: dict) -> SkillResult:
        include_done = bool(params.get("include_done"))
        tasks = self._service.list(include_done=include_done)
        if not tasks:
            return self._ok(
                "The task list is empty. No pending tasks.",
                "Nothing here yet." if include_done else "Your list is empty.",
            )
        lines = [
            f"#{t.id} {'[done]' if t.done else '[open]'} {t.title}"
            + (f" (due {t.due_at.strftime('%d %b %H:%M')})" if t.due_at else "")
            for t in tasks
        ]
        return self._ok(f"{len(tasks)} task(s):\n" + "\n".join(lines), self._spoken_list(tasks, include_done))

    def _complete_or_delete(self, action: str, params: dict) -> SkillResult:
        task = self._resolve(params)
        if isinstance(task, SkillResult):
            return task
        if action == "complete":
            if task.done:
                return self._ok(
                    f"Task #{task.id} ({task.title}) was already done.", f"{task.title} was already done."
                )
            if not self._service.complete(task.id):
                return self._fail(f"No task #{task.id}")
            return self._ok(f"Completed task #{task.id}: {task.title}", f"Marked {task.title} as done.")
        if not self._service.delete(task.id):
            return self._fail(f"No task #{task.id}")
        return self._ok(f"Deleted task #{task.id}: {task.title}", f"Removed {task.title} from your list.")

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
            return self._fail(
                f"No open task matches '{query}'. Nothing was changed.",
                f"I couldn't find a task like {query}, so I didn't change anything.",
            )
        if len(matches) > 1:
            options = "; ".join(f"#{t.id} {t.title}" for t in matches)
            names = " or ".join(t.title for t in matches[:3])
            return self._fail(
                f"More than one task matches '{query}': {options}. Ask the user which one.",
                f"Which one do you mean: {names}?",
            )
        logger.info(f"[tasks] resolved '{query}' -> #{matches[0].id} {matches[0].title!r}")
        return matches[0]

    @staticmethod
    def _spoken_list(tasks, include_done: bool) -> str:
        def join(titles: list[str]) -> str:
            shown = titles[:_MAX_SPOKEN_TITLES]
            text = ", ".join(shown[:-1]) + (", and " if len(shown) > 1 else "") + shown[-1]
            more = len(titles) - len(shown)
            return text + (f", and {more} more" if more > 0 else "")

        if not include_done:
            noun = "task" if len(tasks) == 1 else "tasks"
            return f"You have {len(tasks)} {noun}: {join([t.title for t in tasks])}."
        done = [t.title for t in tasks if t.done]
        pending = [t.title for t in tasks if not t.done]
        parts = []
        if done:
            parts.append(f"You've finished {join(done)}.")
        if pending:
            parts.append(f"Still open: {join(pending)}.")
        return " ".join(parts)

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
