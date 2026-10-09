"""ReminderItem / Reminder — what can be reminded about, and one reminder due at a moment. Pure data.

A `ReminderItem` is a thing with a start time (a calendar event, a task with a due time). A `Reminder` is one thing to
say about it: a heads-up some minutes before (`lead`) or at the start. `key` identifies the reminder across restarts, so
the ledger can remember that it was already said.
"""

from dataclasses import dataclass
from datetime import datetime

KIND_LEAD = "lead"
KIND_START = "start"

SOURCE_CALENDAR = "calendar"
SOURCE_TASK = "task"


@dataclass(frozen=True)
class ReminderItem:
    source: str            # SOURCE_CALENDAR | SOURCE_TASK
    item_id: str
    title: str
    start: datetime        # timezone-aware local time the item starts / is due
    all_day: bool = False


@dataclass(frozen=True)
class Reminder:
    key: str
    item: ReminderItem
    kind: str              # KIND_LEAD | KIND_START
    lead_minutes: int      # minutes before the start (0 for KIND_START)
    due_at: datetime       # when to say it
