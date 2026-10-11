"""CalendarPort — the user's calendar: read a window, add an event. The concrete adapter (Google Calendar) lives in tpa/."""

from datetime import datetime
from typing import Protocol, runtime_checkable

from domain.entities.calendar_event import CalendarEvent


@runtime_checkable
class CalendarPort(Protocol):
    async def list_events(self, start: datetime, end: datetime, limit: int = 20) -> list[CalendarEvent]:
        """Events that start in [start, end), earliest first. Raises ToolUnavailableError when it cannot answer."""
        ...

    async def create_event(self, title: str, start: datetime, end: datetime) -> CalendarEvent:
        """Add an event (no attendees, so nobody is invited) and return it as stored.
        Raises ToolUnavailableError when it cannot, with status 401/403 when the sign-in lacks calendar write access."""
        ...
