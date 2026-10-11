"""MailMessage / MailDraft — what the mail port returns. Pure data.

`body` is plain text, already stripped of markup and quoted history by the adapter, and may be empty for a search
result (a list needs only the headers and the snippet).
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MailMessage:
    id: str
    sender_name: str = ""
    sender_address: str = ""
    subject: str = ""
    received_at: datetime | None = None
    snippet: str = ""
    body: str = ""
    unread: bool = False
    recipients: tuple[str, ...] = ()   # addresses from To:, used to resolve "email Priya" to a real address
    recipient_names: tuple[str, ...] = ()   # display names from To: (same order as `recipients`; "" when the header has none)

    def sender_label(self) -> str:
        """What a person would call the sender: the display name, else the part of the address before the @."""
        return self.sender_name or self.sender_address.split("@")[0] or "someone"

    def recipient_label(self) -> str:
        """Who a sent mail went to: the first recipient's display name, else the part of the address before the @."""
        if not self.recipients:
            return "someone"
        name = self.recipient_names[0] if self.recipient_names else ""
        return name or self.recipients[0].split("@")[0]


@dataclass(frozen=True)
class MailDraft:
    id: str
    to: str
    subject: str
    body: str
