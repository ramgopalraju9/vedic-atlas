"""Ports for reminders: where items come from, where "already said" is remembered, and who can speak a reminder.

Kept in one module because they only make sense together. The calendar and the task list are reached through
`ReminderSource` adapters in the service layer; the SQLite ledger and the voice session implement the other two.
"""

from datetime import datetime
from typing import Iterable, Protocol, runtime_checkable

from domain.entities.reminder import ReminderItem


@runtime_checkable
class ReminderSource(Protocol):
    name: str

    async def upcoming(self, start: datetime, end: datetime) -> list[ReminderItem]:
        """Items that start in [start, end). May raise ToolUnavailableError (not_configured=True when not linked)."""
        ...


@runtime_checkable
class ReminderLedgerPort(Protocol):
    def fired_keys(self, since: datetime) -> set[str]:
        """Keys of the reminders said at or after `since`."""
        ...

    def mark_fired(self, keys: Iterable[str], at: datetime) -> None:
        ...

    def purge_before(self, cutoff: datetime) -> int:
        ...


@runtime_checkable
class AnnouncementSpeakerPort(Protocol):
    """The voice session, seen from the reminder side. It only ever speaks when nothing else is going on."""

    def available(self) -> bool:
        """False when there is no running voice loop (voice disabled or failed to start): reminders are text only."""
        ...

    def is_busy(self) -> bool:
        """True while the assistant is speaking, the user is talking or about to, or a turn is being processed."""
        ...

    async def announce(self, text: str) -> bool:
        """Speak `text` if (and only if) idle at this very moment. False means 'busy, try again later'."""
        ...
