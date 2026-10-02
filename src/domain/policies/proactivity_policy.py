"""ProactivityPolicy — how ambient-event rate limiting scales with mode.

Donor: veda/agents/supervisor.py's `_effective_rate_max`, read in full and
extracted verbatim — chatty mode doubles the rate-limit ceiling so the
user actually hears the extra low-urgency commentary. This is the ONLY
proactivity-related rule directly evidenced in the donor; supervisor.py
does not contain a general "should this event be spoken" truth table
keyed on urgency — that gating happens entirely through the RateLimiter
and Debouncer counters (service/sensing/, a later batch). Do not assume
a richer proactivity/urgency matrix exists until those are ported and
read.
"""

from domain.value_objects.proactivity_level import ProactivityLevel

_CHATTY_MULTIPLIER = 2
_DEFAULT_MULTIPLIER = 1


def rate_limit_multiplier(level: ProactivityLevel) -> int:
    """Factor applied to the base ambient-event rate-limit ceiling."""
    return _CHATTY_MULTIPLIER if level == ProactivityLevel.CHATTY else _DEFAULT_MULTIPLIER