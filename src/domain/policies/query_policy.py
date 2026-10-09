"""QueryPolicy — make a web-search query unambiguous, and check results are on topic.

Two pure helpers (no clock access: `today` is passed in, no I/O):

* `resolve_relative_dates` rewrites "this month" / "next week" / "today" ... into
  concrete dates. A search engine has no idea what "this month" means, and a
  small model often forgets to add the date itself; the same query returned a
  September list one run and an October list the next.
* `results_look_relevant` is a cheap sanity gate on search results: the
  distinctive words of the query (not generic ones like "upcoming movies") must
  show up in the result titles/snippets. Regional film-industry names count as
  their language ("Tollywood" ~ "Telugu"), because that is how the pages are written.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

_ALIASES: tuple[frozenset[str], ...] = (
    frozenset({"tollywood", "telugu"}),
    frozenset({"bollywood", "hindi"}),
    frozenset({"kollywood", "tamil"}),
    frozenset({"mollywood", "malayalam"}),
    frozenset({"sandalwood", "kannada"}),
)

# Words that say what *kind* of thing is asked about, not which thing — never required to match.
_GENERIC = frozenset({
    "a", "an", "the", "of", "in", "on", "at", "for", "to", "and", "or", "is", "are", "was", "were", "be", "about",
    "what", "which", "who", "when", "where", "how", "any", "some", "me", "my", "tell", "show", "give", "list", "please",
    "upcoming", "releasing", "release", "releases", "released", "latest", "new", "newest", "current", "currently", "best",
    "top", "news", "movies", "movie", "films", "film", "series", "shows", "show", "songs", "song", "price", "rate",
    "today", "tomorrow", "yesterday", "tonight", "this", "next", "last", "week", "weekend", "month", "year", "now",
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december",
    "from", "with", "that", "have", "has", "had", "can", "could", "will", "would", "do", "does", "did",
})

_WORD_RE = re.compile(r"[a-z0-9]+")


def _month_year(d: date) -> str:
    return f"{d.strftime('%B')} {d.year}"


def _add_months(d: date, n: int) -> date:
    index = d.year * 12 + (d.month - 1) + n
    return date(index // 12, index % 12 + 1, 1)


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def resolve_relative_dates(query: str, today: date) -> str:
    """Replace relative time phrases with concrete dates ("this month" -> "October 2026")."""
    first = date(today.year, today.month, 1)
    saturday = today + timedelta(days=(5 - today.weekday()) % 7)
    replacements = [
        (r"\bthis month\b", _month_year(first)),
        (r"\bnext month\b", _month_year(_add_months(first, 1))),
        (r"\blast month\b", _month_year(_add_months(first, -1))),
        (r"\bthis year\b", str(today.year)),
        (r"\bnext year\b", str(today.year + 1)),
        (r"\blast year\b", str(today.year - 1)),
        (r"\b(today|tonight)\b", f"{today.day} {_month_year(today)}"),
        (r"\btomorrow\b", f"{(today + timedelta(days=1)).day} {_month_year(today + timedelta(days=1))}"),
        (r"\byesterday\b", f"{(today - timedelta(days=1)).day} {_month_year(today - timedelta(days=1))}"),
        (r"\bthis week\b", f"week of {_monday(today).day} {_month_year(_monday(today))}"),
        (r"\bnext week\b", f"week of {(_monday(today) + timedelta(days=7)).day} {_month_year(_monday(today) + timedelta(days=7))}"),
        (r"\bthis weekend\b", f"weekend of {saturday.day} {_month_year(saturday)}"),
    ]
    out = query
    for pattern, value in replacements:
        out = re.sub(pattern, value, out, flags=re.IGNORECASE)
    return out


def distinctive_terms(query: str) -> list[frozenset[str]]:
    """The words that identify what is asked about, each as a set of acceptable spellings."""
    terms: list[frozenset[str]] = []
    for word in _WORD_RE.findall((query or "").lower()):
        if word in _GENERIC or len(word) < 3 or word.isdigit():
            continue
        group = next((g for g in _ALIASES if word in g), frozenset({word}))
        if group not in terms:
            terms.append(group)
    return terms


def results_look_relevant(query: str, texts: list[str], min_fraction: float = 0.5) -> bool:
    """True if at least `min_fraction` of the query's distinctive terms appear in the result texts.

    A query with no distinctive terms can't be judged, so it passes.
    """
    terms = distinctive_terms(query)
    if not terms:
        return True
    haystack = " ".join(texts).lower()
    found = sum(1 for group in terms if any(alt in haystack for alt in group))
    return found / len(terms) >= min_fraction


_REFERENCE_WORDS = frozenset({
    "it", "its", "they", "them", "their", "theirs", "there", "that", "those", "these", "this", "he", "she", "him", "her",
    "his", "hers", "one", "ones",
})
# Verbs and fillers that ask a question ABOUT something without naming it ("where are they from", "what is happening there").
_VAGUE = frozenset({
    "happening", "happened", "going", "located", "location", "based", "born", "founded", "started", "come", "comes",
    "came", "doing", "done", "mean", "means", "meaning", "about", "tell", "more", "else", "other", "also", "same",
    "founder", "founders", "owner", "owners", "made", "make", "makes", "work", "works", "like", "know",
})


def has_unresolved_reference(query: str) -> bool:
    """True when a search query leans on an earlier turn it cannot see: it contains a pronoun ("it", "they", "there") and
    nothing in it names a subject. The control model is meant to rewrite such a follow-up into a self-contained query; when
    it passes the pronoun through, a search returns whatever the engine finds for "there", so the caller asks instead."""
    words = _WORD_RE.findall((query or "").lower())
    if not any(w in _REFERENCE_WORDS for w in words):
        return False
    named = [w for w in words if w not in _REFERENCE_WORDS and w not in _VAGUE and w not in _GENERIC and len(w) >= 3 and not w.isdigit()]
    return not named
