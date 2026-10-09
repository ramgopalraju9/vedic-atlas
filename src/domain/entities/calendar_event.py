"""CalendarEvent — one entry of the user's calendar. Pure data.

Times are timezone-aware local datetimes. An all-day event has `all_day=True` and its `start` is local midnight.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CalendarEvent:
    id: str
    title: str
    start: datetime
    end: datetime | None = None
    all_day: bool = False
    location: str = ""
