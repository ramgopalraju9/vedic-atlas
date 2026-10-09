"""MailPolicy — pure rules for mail: what a usable address is, which address a spoken name means, how a list is read out.

Mail text (subjects, senders, bodies) is untrusted input written by other people. Everything spoken or stored from it
passes through `clean_text`, which flattens it to one short line so it cannot smuggle layout or control characters into a
prompt or a reply. Nothing here decides an action from the user's message (principle P1).
"""

import re
from collections import Counter
from typing import Sequence

from domain.entities.mail_message import MailMessage

MAX_LIST = 5
_SUBJECT_CHARS = 60
_CONTROL = re.compile(r"[\x00-\x1f\x7f​-‏‪-‮⁦-⁩]")
_ADDRESS = re.compile(r"^[A-Za-z0-9._%+\-']+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")
_QUERY_UNSAFE = re.compile(r'["\\]')


def clean_text(text: str | None, limit: int = 200) -> str:
    """One line, no control characters, at most `limit` characters."""
    flat = " ".join(_CONTROL.sub(" ", text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def is_address(value: str | None) -> bool:
    return bool(_ADDRESS.match((value or "").strip()))


def quote_for_query(name: str) -> str:
    """A name as one quoted search term, so it cannot add operators of its own to the query."""
    return '"' + _QUERY_UNSAFE.sub("", clean_text(name, 60)) + '"'


def pick_address(name: str, messages: Sequence[MailMessage]) -> list[str]:
    """Addresses that a spoken name could mean, most frequent first.

    A sender matches when every word of the name appears in its display name or address; a recipient (only an address
    is known) when every word appears in the address. The caller sends only when EXACTLY ONE address comes back."""
    words = [w for w in re.findall(r"[a-z0-9]+", (name or "").lower()) if len(w) >= 2]
    if not words:
        return []
    seen: Counter[str] = Counter()
    for m in messages:
        if m.sender_address and all(w in f"{m.sender_name} {m.sender_address}".lower() for w in words):
            seen[m.sender_address.lower()] += 1
        for r in m.recipients:
            if all(w in r.lower() for w in words):
                seen[r.lower()] += 1
    return [addr for addr, _ in seen.most_common()]


def spoken_list(messages: Sequence[MailMessage], *, unread_only: bool = False, sent: bool = False) -> str:
    if not messages:
        return "You have no unread emails." if unread_only else ("You haven't sent any matching emails." if sent else "I found no matching emails.")
    shown = list(messages)[:MAX_LIST]
    items = "; ".join(
        f"{'To ' + clean_text(m.recipient_label(), 40) if sent else clean_text(m.sender_label(), 40)}: "
        f"{clean_text(m.subject, _SUBJECT_CHARS) or 'no subject'}"
        for m in shown
    )
    noun = "unread email" if unread_only else ("sent email" if sent else "email")
    count = f"{len(shown)} {noun}{'' if len(shown) == 1 else 's'}"
    return f"{count}. {items}."


def spoken_draft(to: str, subject: str, body: str) -> str:
    return (
        f"Draft ready for {to}, subject {clean_text(subject, 80)}: {clean_text(body, 160)} "
        "Say send it when you want me to send it."
    )


_NO_CONTENT_WORDS = frozenset({
    "email", "mail", "send", "write", "draft", "compose", "message", "please", "can", "could", "you", "the", "and", "for",
    "with", "about", "saying", "say", "tell", "that", "this", "new", "now", "him", "her", "them", "his", "mine", "also",
})


def message_has_content(message: str | None, to: str | None) -> bool:
    """Did the user say WHAT to write, beyond "email Priya"? A model asked to write an email it was given nothing for
    invents one (even the prompt's example text), so the skill asks instead of drafting."""
    skip = set(re.findall(r"\w+", (to or "").lower())) | _NO_CONTENT_WORDS
    words = re.findall(r"\w+", re.sub(r"\S+@\S+", " ", (message or "").lower()))   # an address typed in the request is not content
    return any(len(w) >= 3 and w not in skip for w in words)
