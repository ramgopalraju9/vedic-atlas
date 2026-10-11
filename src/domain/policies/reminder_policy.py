"""ReminderPolicy — pure rules for reminders: which ones an item needs, which are stale, and what is said aloud.

No clock, no I/O, no model: `now` is passed in and every sentence is a template. Item titles are other people's text
(calendar invitations), so they pass through `clean_text` before they are spoken or printed.

Rules in brief
  * A heads-up for each configured lead time, and one at the start. All-day items get none (unless configured).
  * A heads-up whose time has already passed (the item was created at short notice, or the app was down) is still said
    once, as the NEAREST missed one, with the real time left; never when it is closer than `min_lead_sec` to the start,
    because the start reminder follows at once.
  * An item that has begun has no heads-up left; its start reminder is still planned for `late_grace_sec`.
  * A reminder already in the ledger (`fired`) is never planned again.
"""

import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Iterable, Sequence

from domain.entities.reminder import KIND_LEAD, KIND_START, SOURCE_TASK, Reminder, ReminderItem
from domain.policies.mail_policy import clean_text

MAX_LEADS = 5
MAX_LEAD_MINUTES = 24 * 60
_TITLE_CHARS = 60


@dataclass(frozen=True)
class ReminderRules:
    enabled: bool = True
    lead_minutes: tuple[int, ...] = (15,)
    at_start: bool = True
    skip_all_day: bool = True
    late_grace_sec: int = 600    # a start reminder is still planned this long after the start (a restart, a laptop that was asleep)
    min_lead_sec: int = 60       # a missed heads-up closer than this to the start is left to the start reminder
    expire_sec: int = 1800       # a start reminder deferred longer than this after the start is dropped


def validate_leads(values: Iterable[int]) -> tuple[int, ...]:
    """Distinct lead times, longest first. ValueError on nonsense."""
    leads = sorted({int(v) for v in values}, reverse=True)
    if len(leads) > MAX_LEADS:
        raise ValueError(f"at most {MAX_LEADS} lead times")
    if any(m < 1 or m > MAX_LEAD_MINUTES for m in leads):
        raise ValueError(f"lead times must be between 1 and {MAX_LEAD_MINUTES} minutes")
    return tuple(leads)


def reminder_key(item: ReminderItem, kind: str, lead_minutes: int = 0) -> str:
    suffix = f"lead{lead_minutes}" if kind == KIND_LEAD else "start"
    return f"{item.source}:{item.item_id}:{item.start.isoformat()}:{suffix}"


def plan(items: Sequence[ReminderItem], now: datetime, rules: ReminderRules, fired: set[str]) -> list[Reminder]:
    """Every reminder still to say for `items`, earliest first. A missed heads-up is returned with `due_at == now`."""
    planned: list[Reminder] = []
    if not rules.enabled:
        return planned
    leads = sorted({m for m in rules.lead_minutes if m > 0}, reverse=True)
    for item in items:
        if item.all_day and rules.skip_all_day:
            continue
        remaining = (item.start - now).total_seconds()
        missed: list[Reminder] = []
        for m in leads:
            r = Reminder(reminder_key(item, KIND_LEAD, m), item, KIND_LEAD, m, item.start - timedelta(minutes=m))
            if r.key in fired or remaining <= 0:
                continue
            (planned if r.due_at > now else missed).append(r)
        if missed and remaining >= rules.min_lead_sec:
            planned.append(replace(min(missed, key=lambda r: r.lead_minutes), due_at=now))
        if rules.at_start:
            r = Reminder(reminder_key(item, KIND_START), item, KIND_START, 0, item.start)
            if r.key not in fired and -remaining <= rules.late_grace_sec:
                planned.append(r)
    return sorted(planned, key=lambda r: (r.due_at, r.key))


def due(pending: Iterable[Reminder], now: datetime) -> list[Reminder]:
    return [r for r in pending if r.due_at <= now]


def usable(batch: Iterable[Reminder], now: datetime, rules: ReminderRules) -> tuple[list[Reminder], list[Reminder]]:
    """(to say, to drop) from reminders that waited for the user to finish talking.

    A heads-up for an item that has begun is dropped (its start reminder says it better), as is a start reminder
    that waited longer than `expire_sec`."""
    items = list(batch)
    with_start = {(r.item.source, r.item.item_id, r.item.start) for r in items if r.kind == KIND_START}
    keep: list[Reminder] = []
    drop: list[Reminder] = []
    for r in items:
        began = r.item.start <= now
        if r.kind == KIND_LEAD and (began or (r.item.source, r.item.item_id, r.item.start) in with_start):
            drop.append(r)
        elif r.kind == KIND_START and (now - r.item.start).total_seconds() > rules.expire_sec:
            drop.append(r)
        else:
            keep.append(r)
    return sorted(keep, key=lambda r: (r.item.start, r.key)), drop


def minutes_phrase(seconds: float) -> str:
    mins = max(1, math.ceil(seconds / 60))
    if mins == 1:
        return "a minute"
    if mins < 60:
        return f"{mins} minutes"
    hours, rest = divmod(mins, 60)
    base = f"{hours} hour{'' if hours == 1 else 's'}"
    return base if rest == 0 else f"{base} {rest} minutes"


def _phrase(r: Reminder, now: datetime) -> str:
    title = clean_text(r.item.title, _TITLE_CHARS) or "Something"
    task = r.item.source == SOURCE_TASK
    if r.kind == KIND_LEAD:
        left = minutes_phrase((r.item.start - now).total_seconds())
        return f"{title} is due in {left}" if task else f"{title} starts in {left}"
    age = (now - r.item.start).total_seconds()
    if age >= 90:
        ago = minutes_phrase(age)
        return f"{title} was due {ago} ago" if task else f"{title} started {ago} ago"
    return f"{title} is due now" if task else f"{title} is starting now"


def spoken_reminders(reminders: Sequence[Reminder], now: datetime) -> str:
    """One sentence for everything due together, e.g. "Reminder: Design review starts in 15 minutes; Call mom is due now."."""
    if not reminders:
        return ""
    return "Reminder: " + "; ".join(_phrase(r, now) for r in reminders) + "."
