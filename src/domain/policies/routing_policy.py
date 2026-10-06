"""RoutingPolicy — picks which agent should handle a message.

★ PROVISIONAL — NOT YET CONFIRMED. Applied as the default after the routing
question went unanswered across three "continue" turns (see
MIGRATION_LEDGER.md Batch 4/6). This is new code, not a port — the
donor's actual routing is an LLM call (see the Batch 4 correction note).
Easy to change: this is the ONLY file that implements the decision, and
service/agent/router_policy.py is a thin, swappable adapter over it.

Strategy: match the message against each registered agent's own
`description` (already a plain sentence written for a router prompt —
see AgentProfile), using simple keyword overlap. If no agent scores above
a confidence floor, return None so the caller falls back to an LLM call
(the same behaviour as the donor, just as a fallback rather than the
default path). On a CPU-only Pi, most turns should resolve here without
ever touching the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from domain.entities.agent_profile import AgentProfile

_WORD_RE = re.compile(r"[a-z']+")
_MIN_SCORE = 1  # at least one shared *content* keyword after stopword removal

# Dropped so a shared "the"/"is"/"what" can't route a plain question to a
# specialist — that mis-score was forcing an extra LLM routing call per turn.
_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "am",
    "to", "of", "in", "on", "at", "for", "and", "or", "but", "if", "then", "so",
    "do", "does", "did", "can", "could", "would", "should", "will", "shall",
    "may", "might", "i", "you", "he", "she", "it", "we", "they", "me", "my",
    "your", "our", "their", "what", "whats", "how", "why", "when", "where",
    "who", "which", "this", "that", "these", "those", "with", "as", "by",
    "from", "about", "into", "please", "tell", "give", "show", "get",
})


def _keywords(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower())) - _STOPWORDS


def _pick_by_triggers(message: str, candidates: tuple[AgentProfile, ...]) -> str | None:
    """Deterministic routing: the agent whose declared trigger patterns match most.

    Triggers come from tool manifests, so adding a tool adds its routing with
    no code change. Ties go to the earlier-registered agent. None when no agent
    declares a matching trigger (fall through to keyword overlap).
    """
    best_name: str | None = None
    best_hits = 0
    for profile in candidates:
        hits = sum(1 for pattern in profile.triggers if re.search(pattern, message, re.IGNORECASE))
        if hits > best_hits:
            best_hits, best_name = hits, profile.name
    return best_name


# ---- how sure is a keyword match? (used by the "hybrid" routing mode) -------------------------------------

# Cues that the user is correcting or cancelling: a trigger word in such a sentence is a mention, not a request
# ("i didn't ask about the weather", "forget it", "instead of the news"). Keyword matching cannot read these.
_NEGATION_RES = (
    re.compile(r"\b(?:didn'?t|did not|don'?t|do not|never|not)\b.{0,30}\b(?:ask|asked|want|wanted|mean|meant|say|said|talk|talking|need|about)\b", re.IGNORECASE),
    re.compile(r"\b(?:instead|rather than|never ?mind|forget (?:it|that)|ignore (?:that|it)|stop)\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class RuleMatch:
    """Everything the keyword rules know about one message, not just their pick."""

    agent: str | None                  # the agent the rules would route to (None = nothing matched)
    via: str                           # "trigger" | "overlap" | "none"
    trigger_hits: tuple[tuple[str, int], ...]  # agents with at least one trigger hit, and their hit counts
    negated: bool                      # the message contains a correction/cancel cue


def match_agent_detail(message: str, candidates: tuple[AgentProfile, ...]) -> RuleMatch:
    """Like `match_agent`, but also reports how the decision was reached (for the confidence check)."""
    negated = any(r.search(message) for r in _NEGATION_RES)
    hits = tuple(
        (p.name, n)
        for p in candidates
        if (n := sum(1 for pattern in p.triggers if re.search(pattern, message, re.IGNORECASE))) > 0
    )
    agent = match_agent(message, candidates)
    if hits:
        via = "trigger"
    elif agent is not None:
        via = "overlap"
    else:
        via = "none"
    return RuleMatch(agent=agent, via=via, trigger_hits=hits, negated=negated)


def is_confident(match: RuleMatch) -> bool:
    """True when a keyword match can be trusted without asking the model.

    Sure: a trigger pattern (not a loose word overlap) pointed at exactly ONE agent and the sentence holds no
    correction/cancel cue. Anything else is confusion: nothing matched, only a weak word overlap, two agents
    claimed the message ("current tasks" hit both tasks and web search), or the user is negating something.
    """
    return (
        match.agent is not None
        and match.via == "trigger"
        and len(match.trigger_hits) == 1
        and not match.negated
    )


def pick_agent(message: str, candidates: tuple[AgentProfile, ...], default: str) -> str:
    """Return the best-matching agent name, or the default on no clear match.

    Never returns None: a no-match resolves to `default` directly so the
    supervisor does NOT make an extra LLM routing call for an ordinary turn.
    """
    return match_agent(message, candidates) or default


def match_agent(message: str, candidates: tuple[AgentProfile, ...]) -> str | None:
    """The agent the deterministic rules pick, or None when nothing matched.

    Same rules as `pick_agent` (manifest triggers, then keyword overlap), but a
    miss is reported as None instead of silently becoming the default, so the
    caller can hand the ambiguous message to an LLM router.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0].name

    triggered = _pick_by_triggers(message, candidates)
    if triggered is not None:
        return triggered

    message_words = _keywords(message)
    if not message_words:
        return None

    best_name: str | None = None
    best_score = 0
    for profile in candidates:
        profile_words = _keywords(profile.description)
        score = len(message_words & profile_words)
        if score > best_score:
            best_score = score
            best_name = profile.name

    if best_score >= _MIN_SCORE and best_name is not None:
        return best_name
    return None