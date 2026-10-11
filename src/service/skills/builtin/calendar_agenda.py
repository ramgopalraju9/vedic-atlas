"""calendar_agenda — read-only: what is on the user's calendar for a day or a few days.

The model picks two small integers (`date_offset`, `days`); the window arithmetic, range checks and the spoken sentence are
code (domain/policies/calendar_policy.py), so a wrong guess about "Friday" can only be a wrong offset, never a wrong date
computed by the model. The reply is a template: titles are spoken as the calendar returned them, nothing is paraphrased.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from domain.entities.agent_context import AgentContext
from domain.entities.skill_result import SkillResult
from domain.entities.tool_manifest import ToolManifest
from domain.policies.calendar_policy import day_label, spoken_agenda, validate_window, window
from domain.ports.calendar_port import CalendarPort
from service.skills.manifest_skill import ManifestSkill


class CalendarAgendaSkill(ManifestSkill):
    what = "read your calendar"

    def __init__(
        self, calendar: CalendarPort, manifest: ToolManifest, now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
        **kw,
    ):
        super().__init__(manifest, **kw)
        self._calendar = calendar
        self._now = now

    async def run(self, ctx: AgentContext, date_offset=0, days=1, **_) -> SkillResult:
        try:
            offset, span = validate_window(date_offset, days)
        except ValueError as e:
            return self._fail(str(e), f"{e}.")
        now = self._now()
        start, end = window(now, offset, span)
        events = await self._calendar.list_events(start, end)
        text = spoken_agenda(events, day_label(now, offset, span), multi_day=span > 1)
        return self._ok(text, text)
