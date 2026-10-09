"""GmailClient — implements MailPort over the Gmail REST API.

Scopes it needs (granted once by scripts/google_auth.py): gmail.readonly (search, read) and gmail.compose (create and
send drafts). It never deletes, trashes or modifies a message. Only the model-chosen search string and the draft the
user asked for leave the device, all to gmail.googleapis.com via the allow-listed HTTP client.
"""

from __future__ import annotations

import asyncio
import base64
from email.message import EmailMessage

from domain.entities.mail_message import MailDraft, MailMessage
from exceptions.exception import ToolUnavailableError
from tpa.online.google.gmail_parser import to_message
from tpa.online.google.google_auth import GoogleAuth
from tpa.online.http_client import AllowListedHttpClient

HOST = "gmail.googleapis.com"
_BASE = f"https://{HOST}/gmail/v1/users/me"
_CATEGORY = "mail"
_META_HEADERS = ["From", "To", "Subject"]


class GmailClient:
    allowed_hosts = (HOST,)

    def __init__(self, http_client: AllowListedHttpClient, auth: GoogleAuth):
        self._http = http_client
        self._auth = auth

    async def search(self, query: str, limit: int = 5) -> list[MailMessage]:
        headers = await self._auth.headers()
        listing = await self._http.get(
            f"{_BASE}/messages", category=_CATEGORY, headers=headers,
            params={"q": query, "maxResults": max(1, min(limit, 10))},
        )
        ids = [m["id"] for m in listing.get("messages") or [] if m.get("id")]
        items = await asyncio.gather(*(
            self._http.get(
                f"{_BASE}/messages/{mid}", category=_CATEGORY, headers=headers,
                params={"format": "metadata", "metadataHeaders": _META_HEADERS},
            )
            for mid in ids
        ))
        return [to_message(item) for item in items]

    async def get(self, message_id: str) -> MailMessage:
        item = await self._http.get(
            f"{_BASE}/messages/{message_id}", category=_CATEGORY, headers=await self._auth.headers(),
            params={"format": "full"},
        )
        return to_message(item, with_body=True)

    async def create_draft(self, to: str, subject: str, body: str) -> MailDraft:
        msg = EmailMessage()
        msg["To"] = to           # EmailMessage refuses a header value containing a newline: no header injection
        msg["Subject"] = subject
        msg.set_content(body)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        created = await self._http.post_json(
            f"{_BASE}/drafts", category=_CATEGORY, headers=await self._auth.headers(), json={"message": {"raw": raw}},
        )
        draft_id = str(created.get("id") or "")
        if not draft_id:
            raise ToolUnavailableError("GmailClient", _CATEGORY, "Gmail did not return a draft id")
        return MailDraft(id=draft_id, to=to, subject=subject, body=body)

    async def send_draft(self, draft_id: str) -> None:
        await self._http.post_json(
            f"{_BASE}/drafts/send", category=_CATEGORY, headers=await self._auth.headers(), json={"id": draft_id},
        )
