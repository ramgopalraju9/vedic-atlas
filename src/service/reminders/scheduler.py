"""ReminderScheduler — keeps a small timetable of reminders and hands each to the announcer when it is due.

One asyncio task, no model, no threads. Every `poll_sec` it re-reads the next `horizon_hours` of items from its sources
and replans (so a cancelled or moved event simply stops or changes its reminders); every `tick_sec` it checks what is
due. Between ticks it sleeps, so the cost is one small request per poll and a few comparisons per tick.

Failure handling: a source that is down keeps its last known items, the next poll backs off (up to 6x), and nothing is
spoken about it. A source that is not linked yet (Google not signed in) is skipped quietly. Item titles never reach the
logs; only counts do.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Callable, Sequence

from core.logging_config import logger
from domain.entities.reminder import Reminder, ReminderItem
from domain.policies.reminder_policy import ReminderRules, due, plan
from domain.ports.reminder_ports import ReminderLedgerPort, ReminderSource
from exceptions.exception import ToolUnavailableError
from service.reminders.announcer import ReminderAnnouncer, local_now

_LEDGER_LOOKBACK = timedelta(days=2)
_LEDGER_RETENTION = timedelta(days=3)
_MAX_BACKOFF = 6
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)   # "sync as soon as possible"


class ReminderScheduler:
    def __init__(
        self,
        sources: Sequence[ReminderSource],
        ledger: ReminderLedgerPort,
        announcer: ReminderAnnouncer,
        rules: ReminderRules,
        *,
        clock: Callable[[], datetime] = local_now,
        poll_sec: float = 300.0,
        horizon_hours: float = 24.0,
        tick_sec: float = 5.0,
    ):
        self._sources = list(sources)
        self._ledger = ledger
        self._announcer = announcer
        self._rules = rules
        self._clock = clock
        self._poll_sec = poll_sec
        self._horizon = timedelta(hours=horizon_hours)
        self._tick_sec = tick_sec
        self._items: dict[str, list[ReminderItem]] = {}
        self._pending: list[Reminder] = []
        self._next_sync = _EPOCH
        self._failures = 0
        self._unlinked_logged = False
        self._last_sync: datetime | None = None
        self._last_sync_ok = True
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None

    # ---- lifecycle ------------------------------------------------------------------------------------------------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="ReminderScheduler")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    def nudge(self) -> None:
        """Re-read the sources soon (the user just added an event or changed the settings)."""
        self._next_sync = _EPOCH
        self._wake.set()

    def set_rules(self, rules: ReminderRules) -> None:
        self._rules = rules
        self.nudge()

    @property
    def rules(self) -> ReminderRules:
        return self._rules

    def status(self) -> dict:
        nxt = min((r.due_at for r in self._pending), default=None)
        return {
            "running": self._task is not None and not self._task.done(),
            "last_sync_at": self._last_sync.isoformat(timespec="seconds") if self._last_sync else None,
            "last_sync_ok": self._last_sync_ok,
            "pending": len(self._pending),
            "queued_to_say": self._announcer.queued,
            "next_due_at": nxt.isoformat(timespec="seconds") if nxt else None,
        }

    # ---- loop -----------------------------------------------------------------------------------------------------

    async def _run(self) -> None:
        try:
            self._ledger.purge_before(self._clock() - _LEDGER_RETENTION)
        except Exception as e:
            logger.warning(f"[reminders] ledger purge failed: {type(e).__name__}")
        while True:
            try:
                now = self._clock()
                if now >= self._next_sync:
                    await self._sync(now)
                self._dispatch(self._clock())
            except asyncio.CancelledError:
                raise
            except Exception as e:   # the timetable must survive anything
                logger.error(f"[reminders] scheduler error: {type(e).__name__}")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._tick_sec)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    def _dispatch(self, now: datetime) -> None:
        ready = [r for r in due(self._pending, now) if not self._announcer.holds(r.key)]
        if not ready:
            return
        ready_keys = {r.key for r in ready}
        self._pending = [r for r in self._pending if r.key not in ready_keys]
        self._announcer.submit(ready)

    async def _sync(self, now: datetime) -> None:
        ok = True
        lookback = timedelta(seconds=self._rules.late_grace_sec)
        for source in self._sources:
            try:
                self._items[source.name] = await source.upcoming(now - lookback, now + self._horizon)
            except ToolUnavailableError as e:
                if getattr(e, "not_configured", False):
                    if not self._unlinked_logged:
                        logger.info(f"[reminders] {source.name} is not set up; its reminders are off until it is linked")
                        self._unlinked_logged = True
                    self._items[source.name] = []
                else:
                    ok = False
                    logger.warning(f"[reminders] {source.name} unavailable; keeping the last known items")
            except Exception as e:
                ok = False
                logger.warning(f"[reminders] {source.name} read failed ({type(e).__name__}); keeping the last known items")
        self._failures = 0 if ok else self._failures + 1
        self._last_sync, self._last_sync_ok = now, ok
        self._next_sync = now + timedelta(seconds=self._poll_sec * min(2 ** self._failures, _MAX_BACKOFF))
        items = [i for group in self._items.values() for i in group]
        fired = self._ledger.fired_keys(now - _LEDGER_LOOKBACK) if self._rules.enabled else set()
        planned = plan(items, now, self._rules, fired) if self._rules.enabled else []
        self._pending = [r for r in planned if not self._announcer.holds(r.key)]
