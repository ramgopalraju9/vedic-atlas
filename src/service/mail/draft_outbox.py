"""DraftOutbox — the ONE draft Veda has just read back to the user and may send if they say so.

This is what makes "send it" safe: `gmail_send` takes no recipient and no text, it can only send the draft stored here,
which was created this session and read out in full. Nothing else can be sent. A draft expires after `ttl_sec`, is
replaced by a newer draft, and is removed the moment a send is attempted (`take`), so a retried or repeated "send it"
can never send twice. Held in memory on purpose: a restart forgets pending drafts (they remain in the user's Gmail drafts).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from domain.entities.mail_message import MailDraft

DEFAULT_TTL_SEC = 600


@dataclass(frozen=True)
class PendingDraft:
    draft: MailDraft
    session_id: str | None
    expires_at: datetime


class DraftOutbox:
    def __init__(self, ttl_sec: int = DEFAULT_TTL_SEC, now: Callable[[], datetime] = datetime.now):
        self._ttl = timedelta(seconds=ttl_sec)
        self._now = now
        self._pending: PendingDraft | None = None

    def put(self, draft: MailDraft, session_id: str | None) -> None:
        self._pending = PendingDraft(draft, session_id, self._now() + self._ttl)

    def peek(self, session_id: str | None) -> MailDraft | None:
        p = self._pending
        if p is None or p.expires_at <= self._now():
            self._pending = None
            return None
        if session_id is not None and p.session_id is not None and p.session_id != session_id:
            return None
        return p.draft

    def take(self, session_id: str | None) -> MailDraft | None:
        """Remove and return the draft a send is about to use (None when there is nothing valid to send)."""
        draft = self.peek(session_id)
        if draft is not None:
            self._pending = None
        return draft
