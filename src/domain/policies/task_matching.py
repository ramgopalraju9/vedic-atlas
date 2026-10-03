"""TaskMatching — pure rules for resolving a spoken phrase to a task.

"yeah I bought the milk packets" must find the task "get milk home". Titles
and queries are reduced to stemmed content words (stopwords dropped) and a
task matches when they share at least one. Only the best-scoring tasks are
returned, so the caller can tell a unique match from an ambiguous one.
"""

import re

_WORD_RE = re.compile(r"[a-z0-9]+")

# Filler/verb words that carry no identity ("get milk" vs "buy milk").
_STOPWORDS = frozenset({
    "a", "an", "the", "to", "my", "me", "i", "we", "you", "it", "is", "are", "was",
    "and", "or", "of", "for", "on", "in", "at", "some", "that", "this", "up", "out",
    "need", "needs", "have", "has", "get", "got", "bring", "buy", "bought", "take",
    "back", "do", "did", "done", "task", "tasks", "todo", "yeah", "yes", "ok", "okay",
    "just", "also", "please", "add", "remind", "finished", "completed", "complete",
})


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def content_words(text: str) -> frozenset[str]:
    """Stemmed, stopword-free words of `text`."""
    return frozenset(
        _stem(w) for w in _WORD_RE.findall((text or "").lower()) if w not in _STOPWORDS
    )


def normalize_title(title: str) -> str:
    """Canonical form for duplicate detection."""
    return " ".join(sorted(content_words(title))) or (title or "").strip().lower()


def best_matches(query: str, titles: dict[int, str]) -> list[int]:
    """Ids of the best-matching titles for `query`.

    Empty when nothing shares a content word; one id when the match is
    unique; several when they tie (caller should ask the user which).
    """
    q = content_words(query)
    if not q:
        return []
    scored: list[tuple[int, float, int]] = []
    for task_id, title in titles.items():
        t = content_words(title)
        overlap = len(q & t)
        if overlap:
            scored.append((overlap, overlap / len(q | t), task_id))
    if not scored:
        return []
    top = max((o, j) for o, j, _ in scored)
    return [tid for o, j, tid in scored if (o, j) == top]
