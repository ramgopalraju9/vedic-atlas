"""MailPort — the user's mailbox. The concrete adapter (Gmail) lives in tpa/.

Failures are raised as `ToolUnavailableError` (provider down, or the account is not set up), never as a raw SDK error.
There is deliberately no "send this text to this address" method: mail goes out only by sending a draft that already
exists, so what is sent is exactly what was created and read back to the user.
"""

from typing import Protocol, runtime_checkable

from domain.entities.mail_message import MailDraft, MailMessage


@runtime_checkable
class MailPort(Protocol):
    async def search(self, query: str, limit: int = 5) -> list[MailMessage]:
        """Newest first. Headers and snippet only (`body` is empty). `query` uses the provider's search syntax."""
        ...

    async def get(self, message_id: str) -> MailMessage:
        """One message with its plain-text `body`."""
        ...

    async def create_draft(self, to: str, subject: str, body: str) -> MailDraft:
        """Save a draft in the user's mailbox. Sends nothing."""
        ...

    async def send_draft(self, draft_id: str) -> None:
        """Send an existing draft."""
        ...
