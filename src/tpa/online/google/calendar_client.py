"""GoogleCalendarClient — implements CalendarPort over the Google Calendar REST API.

Scope needed: calendar.events (read and add events; it cannot delete a calendar or change sharing). Works on the user's
primary calendar only. Added events carry no attendees, so Google sends nobody an invitation.
Reads: recurring events arrive already expanded (`singleEvents`), earliest first.
"""

from __future__ import annotations

from datetime import date, datetime, time

from domain.entities.calendar_event import CalendarEvent
from exceptions.exception import ToolUnavailableError
from tpa.online.google.google_auth import GoogleAuth
from tpa.online.http_client import AllowListedHttpClient

HOST = "www.googleapis.com"
_URL = f"https://{HOST}/calendar/v3/calendars/primary/events"


def _moment(node: dict) -> tuple[datetime | None, bool]:
    """(local datetime, is_all_day) for an event start/end node."""
    if node.get("dateTime"):
        return datetime.fromisoformat(node["dateTime"].replace("Z", "+00:00")).astimezone(), False
    if node.get("date"):
        return datetime.combine(date.fromisoformat(node["date"]), time.min).astimezone(), True
    return None, False


def to_event(item: dict) -> CalendarEvent | None:
    if item.get("status") == "cancelled":
        return None
    start, all_day = _moment(item.get("start") or {})
    if start is None:
        return None
    end, _ = _moment(item.get("end") or {})
    return CalendarEvent(
        id=str(item.get("id") or ""), title=str(item.get("summary") or ""), start=start, end=end,
        all_day=all_day, location=str(item.get("location") or ""),
    )


class GoogleCalendarClient:
    allowed_hosts = (HOST,)

    def __init__(self, http_client: AllowListedHttpClient, auth: GoogleAuth):
        self._http = http_client
        self._auth = auth

    async def list_events(self, start: datetime, end: datetime, limit: int = 20) -> list[CalendarEvent]:
        payload = await self._http.get(
            _URL, category="calendar", headers=await self._auth.headers(),
            params={
                "timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": "true",
                "orderBy": "startTime", "maxResults": max(1, min(limit, 50)),
            },
        )
        events = [e for e in (to_event(i) for i in payload.get("items") or []) if e is not None]
        return [e for e in events if start <= e.start < end]

    async def create_event(self, title: str, start: datetime, end: datetime) -> CalendarEvent:
        created = await self._http.post_json(
            _URL, category="calendar", headers=await self._auth.headers(),
            json={"summary": title, "start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()}},
        )
        event = to_event(created)
        if event is None:
            raise ToolUnavailableError("GoogleCalendarClient", "calendar", "Google Calendar did not return the new event")
        return event
