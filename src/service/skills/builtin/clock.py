"""current_time — the time or today's date, read from the device clock.

Without it, "what is the time" was the nearest-looking tool request to `device_status` and was answered with CPU usage.
The sentence is built here from the clock; the model supplies at most `time` or `date`.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Callable

from domain.entities.agent_context import AgentContext
from domain.entities.skill_result import SkillResult
from domain.entities.tool_manifest import ToolManifest
from domain.policies.calendar_policy import clock
from service.skills.manifest_skill import ManifestSkill


# "what time is it in Tokyo": the clock is this device's, so say so instead of passing it off as Tokyo's.
_ELSEWHERE = re.compile(r"\b(?:in|for)\s+(?!the\b|a\b|an\b|my\b|this\b|about\b|\d)[a-z]{3,}", re.IGNORECASE)


class CurrentTimeSkill(ManifestSkill):
    what = "read the clock"

    def __init__(self, manifest: ToolManifest, now: Callable[[], datetime] = lambda: datetime.now().astimezone(), **kw):
        super().__init__(manifest, **kw)
        self._now = now

    async def run(self, ctx: AgentContext, what: str = "time", **_) -> SkillResult:
        now = self._now()
        elsewhere = _ELSEWHERE.search(ctx.user_message or "") is not None
        if what == "date":
            text = f"Today is {now.strftime('%A')} {now.day} {now.strftime('%B %Y')}."
        elif elsewhere:
            text = f"It's {clock(now)} here. I can't check the time anywhere else."
        else:
            text = f"It's {clock(now)}."
        return self._ok(text, text)
