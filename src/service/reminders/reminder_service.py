"""ReminderService — the one handle on reminders for the app: start/stop, settings, status, the text feed.

Routes, the CLI and the lifespan talk to this; the scheduler, announcer and feed stay behind it. Settings changed at
runtime apply at once but are not written back to config/reminders.yaml (edit that file to change the defaults).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable

from domain.policies.reminder_policy import ReminderRules, validate_leads
from service.reminders.announcer import ReminderAnnouncer
from service.reminders.feed import ReminderFeed
from service.reminders.scheduler import ReminderScheduler


class ReminderService:
    def __init__(self, scheduler: ReminderScheduler, announcer: ReminderAnnouncer, feed: ReminderFeed):
        self._scheduler = scheduler
        self._announcer = announcer
        self._feed = feed

    def start(self) -> None:
        self._announcer.start()
        self._scheduler.start()

    async def stop(self) -> None:
        await self._scheduler.stop()
        await self._announcer.stop()

    def nudge(self) -> None:
        self._scheduler.nudge()

    def config(self) -> dict:
        r = self._scheduler.rules
        return {"enabled": r.enabled, "lead_minutes": list(r.lead_minutes), "at_start": r.at_start, "skip_all_day": r.skip_all_day}

    def status(self) -> dict:
        return {**self._scheduler.status(), "config": self.config()}

    def update(
        self, *, enabled: bool | None = None, lead_minutes: list[int] | None = None, at_start: bool | None = None,
        skip_all_day: bool | None = None,
    ) -> dict:
        """Change settings now. ValueError (with a plain message) on an invalid lead list; nothing changes then."""
        rules = self._scheduler.rules
        changes: dict = {}
        if enabled is not None:
            changes["enabled"] = bool(enabled)
        if lead_minutes is not None:
            changes["lead_minutes"] = validate_leads(lead_minutes)
        if at_start is not None:
            changes["at_start"] = bool(at_start)
        if skip_all_day is not None:
            changes["skip_all_day"] = bool(skip_all_day)
        rules = replace(rules, **changes)
        self._scheduler.set_rules(rules)
        self._announcer.set_rules(rules)
        return self.config()

    def recent(self, after: int | None) -> dict:
        """Reminders said after sequence number `after`. `after=None` only reports the current position (a new
        client starts from now and is not flooded with old reminders)."""
        if after is None:
            return {"items": [], "last": self._feed.last_seq}
        return {"items": self._feed.since(after), "last": self._feed.last_seq}


class ChangeSignal:
    """A tiny late-bound hook: skills call `fire()` after changing the calendar; the reminder service subscribes later."""

    def __init__(self) -> None:
        self._callbacks: list[Callable[[], None]] = []

    def subscribe(self, callback: Callable[[], None]) -> None:
        self._callbacks.append(callback)

    def fire(self) -> None:
        for cb in self._callbacks:
            try:
                cb()
            except Exception:
                pass
