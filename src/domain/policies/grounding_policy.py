"""GroundingPolicy — is a model's narration supported by the tool result it was given?

The narrate stage is the only place a model phrases real-world facts, so this
is the last line of defence against an invented number. Rule: every number in
the reply (a temperature, a score, a price, a year) must also appear in the
tool result, the user's own question, or today's date. A reply that cites a
URL is rejected too — the observation never contains one for the model to
copy, so a URL could only be made up.

Deliberately simple and strict. A rejected narration falls back to the tool's
own deterministic `spoken` sentence, so a false rejection costs a little
fluency, never correctness.
"""

from __future__ import annotations

import re
from typing import Iterable

_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
_TOLERANCE = 0.5  # the model may round 29.0 -> 29 or 96.324 -> 96.32


def _numbers(text: str) -> list[float]:
    out = []
    for token in _NUMBER_RE.findall(text or ""):
        try:
            out.append(float(token.replace(",", "")))
        except ValueError:
            continue
    return out


def ungrounded_numbers(reply: str, sources: Iterable[str]) -> list[float]:
    """Numbers in `reply` that no source contains (within rounding)."""
    allowed = [n for src in sources for n in _numbers(src)]
    return [n for n in _numbers(reply) if not any(abs(n - a) <= _TOLERANCE for a in allowed)]


def is_grounded(reply: str, sources: Iterable[str]) -> bool:
    if _URL_RE.search(reply or ""):
        return False
    return not ungrounded_numbers(reply, list(sources))
