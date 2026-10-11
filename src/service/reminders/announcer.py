"""ReminderAnnouncer — says due reminders, but only when nothing else is going on.

The one rule that matters: a reminder never talks over the user or over a reply. It waits until the voice session has
been idle for `idle_settle_sec` (so it does not cut into a pause between two of the user's sentences), then speaks every
reminder that piled up in the meantime as ONE sentence. Muted is not busy: the mic being closed does not stop the speaker.

No model call anywhere: the sentence is a template (domain/policies/reminder_policy.py), the voice is the same TTS the
replies use, played through the voice session's echo-guarded path so the assistant never hears itself.

Text is always published to the feed too (the terminal prints it); with voice disabled or not running that is the only
channel and nothing waits.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Callable, Sequence

from core.logging_config import logger
from domain.entities.reminder import Reminder
from domain.policies.reminder_policy import ReminderRules, spoken_reminders, usable
from domain.ports.reminder_ports import AnnouncementSpeakerPort, ReminderLedgerPort
from service.reminders.feed import ReminderFeed


def local_now() -> datetime:
    return datetime.now().astimezone()


class ReminderAnnouncer:
    def __init__(
        self,
        ledger: ReminderLedgerPort,
        feed: ReminderFeed,
        speaker: AnnouncementSpeakerPort | None,
        rules: ReminderRules,
        *,
        clock: Callable[[], datetime] = local_now,
        idle_settle_sec: float = 1.5,
        poll_sec: float = 0.25,
    ):
        self._ledger = ledger
        self._feed = feed
        self._speaker = speaker
        self._rules = rules
        self._clock = clock
        self._idle_settle_sec = idle_settle_sec
        self._poll_sec = poll_sec
        self._queue: dict[str, Reminder] = {}
        self._inflight: set[str] = set()   # taken from the queue, not yet in the ledger (being spoken right now)
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None

    # ---- lifecycle ------------------------------------------------------------------------------------------------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="ReminderAnnouncer")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    def set_rules(self, rules: ReminderRules) -> None:
        self._rules = rules

    # ---- queue ----------------------------------------------------------------------------------------------------

    def holds(self, key: str) -> bool:
        return key in self._queue or key in self._inflight

    @property
    def queued(self) -> int:
        return len(self._queue)

    def submit(self, reminders: Sequence[Reminder]) -> None:
        for r in reminders:
            self._queue.setdefault(r.key, r)
        if self._queue:
            self._wake.set()

    # ---- delivery -------------------------------------------------------------------------------------------------

    def _can_speak(self) -> bool:
        try:
            return self._speaker is not None and self._speaker.available()
        except Exception:
            return False

    async def _run(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            try:
                await self.drain()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[reminders] delivery failed: {type(e).__name__}")
                await asyncio.sleep(1.0)

    async def wait_idle(self) -> None:
        """Return once the voice session has stayed idle for `idle_settle_sec` (at once when there is no voice)."""
        idle_since: float | None = None
        while self._can_speak():
            if self._speaker.is_busy():
                idle_since = None
            else:
                now = time.monotonic()
                idle_since = now if idle_since is None else idle_since
                if now - idle_since >= self._idle_settle_sec:
                    return
            await asyncio.sleep(self._poll_sec)

    async def drain(self) -> None:
        while self._queue:
            await self.wait_idle()
            now = self._clock()
            batch, dropped = usable(list(self._queue.values()), now, self._rules)
            self._inflight.update(r.key for r in batch)
            self._inflight.update(r.key for r in dropped)
            self._queue.clear()
            if dropped:
                self._ledger.mark_fired([r.key for r in dropped], now)
                self._inflight.difference_update(r.key for r in dropped)
            if not batch:
                continue
            text = spoken_reminders(batch, now)
            if self._can_speak():
                try:
                    spoke = await self._speaker.announce(text)
                except Exception as e:   # a TTS failure must not lose the reminder: the text still goes out
                    logger.error(f"[reminders] could not speak: {type(e).__name__}")
                    spoke = True
                if not spoke:   # busy at this very moment: keep them, they join whatever arrives next
                    self._inflight.difference_update(r.key for r in batch)
                    self.submit(batch)
                    await asyncio.sleep(self._poll_sec)
                    continue
            self._feed.publish(text, now)
            self._ledger.mark_fired([r.key for r in batch], now)
            self._inflight.difference_update(r.key for r in batch)
            logger.info(f"[reminders] announced {len(batch)} reminder(s)")
