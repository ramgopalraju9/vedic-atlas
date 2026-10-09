"""CalendarPolicy — pure rules for reading the calendar: which window a request means, how events are read out.

The model only picks small integers (`date_offset` 0..6, `days` 1..7); the date arithmetic and the range checks are
here, because relative dates are a known weak spot of a 4B model. Event titles are other people's text, so they are
flattened with `clean_text` before they are spoken.
"""

import re
from datetime import datetime, time, timedelta
from typing import Sequence

from domain.entities.calendar_event import CalendarEvent
from domain.policies.mail_policy import clean_text

MAX_DAYS_AHEAD = 6
MAX_SPAN_DAYS = 7
MAX_SPOKEN = 5
_TITLE_CHARS = 50


def validate_window(date_offset, days) -> tuple[int, int]:
    """(offset, span) or ValueError with a sentence fit to be spoken."""
    if isinstance(date_offset, bool) or not isinstance(date_offset, int) or not 0 <= date_offset <= MAX_DAYS_AHEAD:
        raise ValueError(f"I can only look 0 to {MAX_DAYS_AHEAD} days ahead")
    if days is None:
        days = 1
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_SPAN_DAYS:
        raise ValueError(f"I can read between 1 and {MAX_SPAN_DAYS} days at a time")
    return date_offset, days


def window(now: datetime, date_offset: int, days: int) -> tuple[datetime, datetime]:
    """Local-midnight bounds [start, end) of the days asked for."""
    start = (now + timedelta(days=date_offset)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=days)


def day_label(now: datetime, date_offset: int, days: int) -> str:
    start = now + timedelta(days=date_offset)
    if days > 1:
        return "in the next %d days" % days if date_offset == 0 else f"for {days} days from {start.strftime('%A')}"
    return {0: "today", 1: "tomorrow"}.get(date_offset, f"on {start.strftime('%A')}")


def clock(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    suffix = "AM" if dt.hour < 12 else "PM"
    return f"{hour} {suffix}" if dt.minute == 0 else f"{hour}:{dt.minute:02d} {suffix}"


def spoken_agenda(events: Sequence[CalendarEvent], label: str, *, multi_day: bool = False) -> str:
    if not events:
        return f"Nothing on your calendar {label}."
    shown = list(events)[:MAX_SPOKEN]
    parts = []
    for e in shown:
        title = clean_text(e.title, _TITLE_CHARS) or "Busy"
        when = "all day" if e.all_day else f"at {clock(e.start)}"
        day = f"{e.start.strftime('%A')} " if multi_day else ""
        parts.append(f"{day}{title} {when}" if not e.all_day else f"{day}{title}, all day")
    more = len(events) - len(shown)
    tail = f", and {more} more" if more > 0 else ""
    n = len(events)
    return f"You have {n} event{'' if n == 1 else 's'} {label}: " + ", ".join(parts) + tail + "."


# ---- writing to the calendar ---------------------------------------------------------------------------------------------

DEFAULT_DURATION_MIN = 60
MAX_DURATION_MIN = 480
_TITLE_MAX = 100
# "17:00", "17:00:00", "5:30 pm", "5pm", "12 am": the model is asked for 24-hour HH:MM but does not always comply.
_TIME = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?(?::\d{2})?\s*(?:(a|p)\.?\s?m\.?)?\s*$", re.IGNORECASE)

# A "task" that is really a calendar entry ("block calendar for Manoj"). The control model sent these to the to-do list when there
# was no calendar tool; the tasks tool refuses them so the user hears the truth instead.
# Narrow on purpose: "schedule dentist appointment" or "book a meeting room" are real to-dos. Only a title that names the
# calendar, or starts with "block" / "event" ("block 5 pm", "event with Manoj"), is a calendar entry.
_CALENDAR_TASK = re.compile(r"\bcalend[ae]r\b|^(block|event)\b", re.IGNORECASE)


def looks_like_calendar_entry(title: str | None) -> bool:
    return bool(_CALENDAR_TASK.search(title or ""))


def _parse_clock(value) -> time | None:
    match = _TIME.match(str(value or ""))
    if not match:
        return None
    hour, minute, meridiem = int(match.group(1)), int(match.group(2) or 0), (match.group(3) or "").lower()
    if minute > 59:
        return None
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem == "p" else 0)
    elif match.group(2) is None:
        return None   # a bare "17" or "5" is not a time
    if hour > 23:
        return None
    return time(hour, minute)


def validate_new_event(title, date_offset, at, duration) -> tuple[str, int, time, int]:
    """(title, offset, start time, minutes) or ValueError with a sentence fit to be spoken.

    The model supplies a day offset and a 24-hour "HH:MM"; every range check and the date itself are code."""
    name = clean_text(title, _TITLE_MAX)
    if not name:
        raise ValueError("What should the event be called?")
    if date_offset is None:
        date_offset = 0
    if isinstance(date_offset, bool) or not isinstance(date_offset, int) or not 0 <= date_offset <= MAX_DAYS_AHEAD:
        raise ValueError(f"I can only add events up to {MAX_DAYS_AHEAD} days ahead")
    clock_time = _parse_clock(at)
    if clock_time is None:
        raise ValueError("What time should I set it for?")
    if duration is None:
        duration = DEFAULT_DURATION_MIN
    if isinstance(duration, bool) or not isinstance(duration, int) or not 5 <= duration <= MAX_DURATION_MIN:
        raise ValueError(f"An event can last between 5 minutes and {MAX_DURATION_MIN // 60} hours")
    return name, date_offset, clock_time, duration


def event_times(now: datetime, date_offset: int, at: time, minutes: int) -> tuple[datetime, datetime]:
    """Local start and end. ValueError (spoken) when the start is already past."""
    day = (now + timedelta(days=date_offset)).replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    if day <= now:
        raise ValueError("That time has already passed")
    return day, day + timedelta(minutes=minutes)


def same_event(existing: CalendarEvent, title: str, start: datetime) -> bool:
    return existing.start == start and clean_text(existing.title, _TITLE_MAX).casefold() == title.casefold()


def spoken_created(event: CalendarEvent, label: str, *, already: bool = False) -> str:
    title = clean_text(event.title, _TITLE_MAX)
    when = f"{label} at {clock(event.start)}"
    return f"{title} is already on your calendar {when}." if already else f"Added {title} to your calendar {when}."


_NUMBER_WORDS = "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
_MERIDIEM = r"(?:a\.?\s?m\.?|p\.?\s?m\.?)(?![a-z])"
_STATES_TIME = re.compile(
    rf"\b(?:\d{{1,2}}[:.]\d{{2}}|\d{{1,2}}\s?{_MERIDIEM}|(?:{_NUMBER_WORDS})\s?(?:{_MERIDIEM}|o'?clock)|"
    rf"at\s+(?:\d{{1,2}}|{_NUMBER_WORDS})\b|\d{{1,2}}\s+(?:in the (?:morning|afternoon|evening)|tonight)\b|"
    rf"(?:{_NUMBER_WORDS})\s+(?:thirty|fifteen|forty[- ]five|oh \w+)\b|noon|midnight|o'?clock|half past|quarter (?:past|to))",
    re.IGNORECASE,
)


def message_states_time(text: str | None) -> bool:
    """Did the user's own words give a clock time ("5 pm", "17:30", "at six", "noon")?  A small model fills a required
    time with a plausible one ("15:00", the current minute) when none was said, so the skill trusts a time only when
    the message contains one; a duration like "30 minutes" or "that time" does not count."""
    return bool(_STATES_TIME.search(text or ""))


# ---- the user's words must support the write ------------------------------------------------------------------------------
# The 4B model sometimes answers a request it has no tool for ("move my meeting to 6 pm", "delete the 5 pm meeting") with
# calendar_create, even copying the title from the prompt's example. A write happens only when the user's own words ask to
# ADD something and the title is made of words the user said.

_ADD_VERBS = re.compile(r"\b(add|put|schedule|block|create|book|set up|set|make|new|plan|reserve|fix)\b", re.IGNORECASE)
_CHANGE_VERBS = re.compile(r"\b(cancel|delete|remove|move|reschedule|postpone|push|clear|drop|shift|change)\b", re.IGNORECASE)
_WORD = re.compile(r"\w+", re.UNICODE)


def asks_to_change_not_add(message: str | None) -> bool:
    """"cancel my 5 pm meeting", "move it to 6": a change verb and no verb that adds. There is no tool for it yet."""
    text = message or ""
    return bool(_CHANGE_VERBS.search(text)) and not _ADD_VERBS.search(text)


def title_is_from_the_message(title: str, message: str | None) -> bool:
    """At least one word of the title (3+ letters) appears, by its first four letters, in what the user said. A title with no
    such word to check (all short words) passes."""
    stems = {w[:4] for w in _WORD.findall((title or "").lower()) if len(w) >= 3}
    if not stems:
        return True
    return bool(stems & {w[:4] for w in _WORD.findall((message or "").lower())})
