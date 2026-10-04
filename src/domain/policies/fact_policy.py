"""FactPolicy — how a remembered fact is shaped, matched and screened.

A fact is one short line, "<topic>: <value>" ("favourite sweet: gulab jamun"). The topic is the key:
saying a new favourite sweet replaces the old one instead of piling up beside it. Pure functions only —
the storage lives behind KnowledgeStorePort.
"""

from __future__ import annotations

import re

MAX_TOPIC_CHARS = 60
MAX_VALUE_CHARS = 200
MAX_FACTS = 40  # every fact is in the prompt on every turn, so the list stays small

_SEPARATOR = ": "
_WORD_RE = re.compile(r"[a-z0-9']+")
_FAVOURITE_FORMS = {"fav", "faves", "favorite", "favourite", "favorites", "favourites", "favs"}
_FILLER = frozenset({"my", "the", "our", "most", "a", "an", "of"})

# Things that must never be written to a plain file and read back into every prompt.
_SENSITIVE_RE = re.compile(
    r"\b(password|passcode|passwd|otp|pin( number| code)?|cvv|cvc|card number|credit card|debit card|"
    r"bank account|account number|aadhaar|aadhar|ssn|social security|secret key|api key|token)\b",
    re.IGNORECASE,
)


def normalise_topic(topic: str) -> str:
    """Canonical topic: lower-case, fav/favorite/favourite unified, filler words dropped.

    "My most fav noodles" -> "favourite noodles"; "Favorite sweet" -> "favourite sweet".
    """
    words = [("favourite" if w in _FAVOURITE_FORMS else w) for w in _WORD_RE.findall((topic or "").lower())]
    return " ".join(w for w in words if w not in _FILLER)


def format_fact(topic: str, value: str) -> str:
    return f"{normalise_topic(topic)}{_SEPARATOR}{' '.join((value or '').split())}"


def split_fact(fact: str) -> tuple[str, str] | None:
    """(normalised topic, value) of a keyed fact, or None for a free-text fact with no topic."""
    head, sep, tail = (fact or "").partition(_SEPARATOR)
    if not sep or not head.strip() or not tail.strip():
        return None
    return normalise_topic(head), tail.strip()


def is_sensitive(*texts: str) -> bool:
    return any(_SENSITIVE_RE.search(t or "") for t in texts)
