"""ReplyFillerPolicy — drops stock "offer more help" sentences from chat replies.

Small models close almost every reply with "Let me know if there's anything else I can help with." That is
dead air in a spoken reply, and once it is saved to history the model copies it into every later reply. Only
sentences that are pure offers are dropped; if that would leave nothing, the reply is left as it was.

Pure text logic, no I/O: the responder applies it to the final text and to the streamed sentences.
"""

from __future__ import annotations

import re

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_HAS_WORD_RE = re.compile(r"\w")

# "Let me know if you need anything else", "Feel free to ask", "Is there anything else...", "I'm here to help".
_OFFER_RES = (
    re.compile(
        r"^\W*(?:and\W+)?(?:please\W+)?(?:let me know|tell me|feel free to\b.*|don'?t hesitate to\b.*)"
        r".*\b(?:anything else|something else|else i can|need (?:help|anything|more|any)|help with|assist|"
        r"questions?|more (?:details|info\w*)|further|ask)\b.*$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r"^\W*(?:and\W+)?is there (?:anything|something) (?:else|more)\b.*$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\W*(?:and\W+)?(?:is there )?anything else (?:i can|you(?:'d| would| want| need))\b.*$", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\W*i(?:'m| am) (?:always )?(?:here|ready) (?:to help|if you need|whenever|for anything)\b.*$", re.IGNORECASE | re.DOTALL),
)


def is_filler(sentence: str) -> bool:
    """True when the sentence is only a stock offer of further help."""
    text = sentence.strip()
    return bool(text) and any(r.match(text) for r in _OFFER_RES)


def _keep_flags(sentences: list[str]) -> list[bool]:
    """Per sentence: keep it? A bare emoji/punctuation fragment follows the fate of the sentence before it."""
    keep: list[bool] = []
    for s in sentences:
        if not _HAS_WORD_RE.search(s):
            keep.append(keep[-1] if keep else True)
        else:
            keep.append(not is_filler(s))
    return keep


def strip_filler(text: str) -> str:
    """The reply without stock offer sentences; unchanged if everything in it is filler."""
    original = (text or "").strip()
    if not original:
        return original
    sentences = _SENTENCE_SPLIT_RE.split(original)
    kept = [s for s, k in zip(sentences, _keep_flags(sentences)) if k]
    return " ".join(kept).strip() or original


class FillerStreamFilter:
    """Streaming version: feed chunks, emit only finished non-filler sentences (one sentence of lag).

    `finish()` flushes the unfinished tail. If the whole reply was filler, it is returned in full
    so the user still hears something.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._held_back: list[str] = []  # dropped sentences, in case the whole reply turns out to be filler
        self._emitted = False
        self._last_dropped = False

    def _take(self, sentences: list[str]) -> str:
        out: list[str] = []
        for s in sentences:
            if not _HAS_WORD_RE.search(s):
                drop = self._last_dropped
            else:
                drop = is_filler(s)
            self._last_dropped = drop
            if drop:
                self._held_back.append(s)
            else:
                out.append(s)
        if not out:
            return ""
        text = " ".join(out)
        if self._emitted:
            text = " " + text
        self._emitted = True
        return text

    def feed(self, chunk: str) -> str:
        self._buffer += chunk
        parts = _SENTENCE_SPLIT_RE.split(self._buffer)
        if len(parts) < 2:
            return ""
        self._buffer = parts[-1]
        return self._take(parts[:-1])

    def finish(self) -> str:
        tail = self._buffer.strip()
        self._buffer = ""
        out = self._take([tail]) if tail else ""
        if not self._emitted and self._held_back:
            return " ".join(self._held_back).strip()  # nothing but filler: better than silence
        return out
