"""Reminder sources — the calendar and the task list, both seen as "things with a start time".

No model is involved anywhere in the reminder path: these read plain data and hand `ReminderItem`s to the scheduler.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from domain.entities.reminder import SOURCE_CALENDAR, SOURCE_TASK, ReminderItem
from domain.ports.calendar_port import CalendarPort
from service.tasks.task_service import TaskService

_CALENDAR_LIMIT = 50


class CalendarReminderSource:
    name = "calendar"

    def __init__(self, calendar: CalendarPort):
        self._calendar = calendar

    async def upcoming(self, start: datetime, end: datetime) -> list[ReminderItem]:
        events = await self._calendar.list_events(start, end, limit=_CALENDAR_LIMIT)
        return [
            ReminderItem(SOURCE_CALENDAR, e.id, e.title, e.start, e.all_day)
            for e in events if e.id and start <= e.start < end
        ]


class TaskReminderSource:
    """Open tasks that have a due time. A task with no due time has nothing to remind about; a due date with no time of
    day (midnight) is treated as all-day, which the policy skips by default."""

    name = "task"

    def __init__(self, tasks: TaskService):
        self._tasks = tasks

    async def upcoming(self, start: datetime, end: datetime) -> list[ReminderItem]:
        tasks = await asyncio.to_thread(self._tasks.list)
        items: list[ReminderItem] = []
        for t in tasks:
            if t.done or t.due_at is None or t.id is None:
                continue
            due = t.due_at.astimezone()   # a naive stored time is local time
            if not start <= due < end:
                continue
            midnight = due.hour == 0 and due.minute == 0 and due.second == 0
            items.append(ReminderItem(SOURCE_TASK, str(t.id), t.title, due, all_day=midnight))
        return items
