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

from domain.entities.agent_profile import AgentProfile

_WORD_RE = re.compile(r"[a-z']+")
_MIN_SCORE = 1  # at least one shared keyword, tune once real usage data exists


def _keywords(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def pick_agent(message: str, candidates: tuple[AgentProfile, ...], default: str) -> str | None:
    """Return the best-matching agent name, or None to signal 'ask the LLM instead'."""
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0].name

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

    if best_score >= _MIN_SCORE:
        return best_name
    return None  # ambiguous — let the caller fall back to an LLM call