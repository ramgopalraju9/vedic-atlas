"""ExchangeNotePolicy — one question-and-answer as a short memory, built without a model.

The note is the words that were actually said, clipped, not a paraphrase: a paraphrase costs a model call (minutes on a
Raspberry Pi) and can get a detail wrong, while the spoken text is already short. Exchanges that carry nothing to recall
("ok", "hi", an empty reply) produce no note.
"""

MIN_CHARS = 6


def _flat(text: str | None) -> str:
    return " ".join((text or "").split())


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    return (cut[: cut.rfind(" ")] if " " in cut[limit // 2:] else cut).rstrip(",;: ") + "…"


def exchange_note(user: str | None, reply: str | None, *, user_chars: int = 110, reply_chars: int = 170) -> str:
    u, r = _flat(user), _flat(reply)
    if len(u) < MIN_CHARS or len(r) < MIN_CHARS:
        return ""
    return f"You: {_clip(u, user_chars)} | Veda: {_clip(r, reply_chars)}"
