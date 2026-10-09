"""Gmail API payload -> plain text and MailMessage. Pure functions, no I/O (so they are easy to test).

A body is the first text/plain part, else the text/html part with its markup removed; quoted history ("> ..." lines and
everything after "On ... wrote:") is cut, and the result is capped, so a long thread never becomes a long document.
"""

from __future__ import annotations

import base64
import re
from datetime import datetime
from email.utils import getaddresses, parseaddr
from html.parser import HTMLParser

from domain.entities.mail_message import MailMessage

BODY_CHARS = 2000
_WROTE = re.compile(r"^\s*On .{5,120} wrote:\s*$", re.IGNORECASE)
_SKIP_TAGS = {"script", "style", "head", "title"}
_BREAK_TAGS = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"}


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BREAK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in _BREAK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    parser = _Text()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:   # broken markup: whatever was read so far is still the best text we have
        pass
    return "".join(parser.parts)


def _decode(data: str, charset: str | None) -> str:
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    try:
        return raw.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _charset(part: dict) -> str | None:
    for h in part.get("headers") or []:
        if h.get("name", "").lower() == "content-type":
            m = re.search(r"charset=\"?([\w\-]+)", h.get("value", ""), re.IGNORECASE)
            return m.group(1) if m else None
    return None


def _find(part: dict, mime: str) -> str | None:
    if part.get("mimeType") == mime and (part.get("body") or {}).get("data"):
        return _decode(part["body"]["data"], _charset(part))
    for sub in part.get("parts") or []:
        found = _find(sub, mime)
        if found is not None:
            return found
    return None


def strip_quoted(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines():
        if _WROTE.match(line):
            break
        if line.lstrip().startswith(">"):
            continue
        kept.append(line)
    return "\n".join(kept)


def body_text(payload: dict, limit: int = BODY_CHARS) -> str:
    plain = _find(payload, "text/plain")
    if plain is None:
        html = _find(payload, "text/html")
        plain = html_to_text(html) if html is not None else ""
    flat = "\n".join(" ".join(line.split()) for line in strip_quoted(plain).splitlines())
    flat = re.sub(r"\n{3,}", "\n\n", flat).strip()
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _headers(payload: dict) -> dict[str, str]:
    return {h.get("name", "").lower(): h.get("value", "") for h in payload.get("headers") or []}


def to_message(item: dict, *, with_body: bool = False) -> MailMessage:
    payload = item.get("payload") or {}
    headers = _headers(payload)
    name, address = parseaddr(headers.get("from", ""))
    received = None
    if item.get("internalDate"):
        try:
            received = datetime.fromtimestamp(int(item["internalDate"]) / 1000).astimezone()
        except (TypeError, ValueError, OSError):
            received = None
    to_pairs = [(n.strip(), a) for n, a in getaddresses([headers.get("to", "")]) if a]
    recipients = tuple(a for _, a in to_pairs)
    return MailMessage(
        id=str(item.get("id") or ""),
        sender_name=name.strip(),
        sender_address=address.strip(),
        subject=headers.get("subject", "").strip(),
        received_at=received,
        snippet=" ".join(str(item.get("snippet") or "").split()),
        body=body_text(payload) if with_body else "",
        unread="UNREAD" in (item.get("labelIds") or []),
        recipients=recipients,
        recipient_names=tuple(n for n, _ in to_pairs),
    )
