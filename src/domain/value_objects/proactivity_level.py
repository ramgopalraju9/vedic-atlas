"""ProactivityLevel — how chatty the supervisor is about ambient events.

New as a typed value object; the donor (veda/config.py VoiceConfig.proactivity)
carried this as a bare string ("conservative" | "medium" | "chatty") with the
allowed values only documented in a comment. Promoted to an enum so an
invalid value fails at config-load time instead of at first ambient event.
"""

from enum import Enum


class ProactivityLevel(str, Enum):
    CONSERVATIVE = "conservative"
    MEDIUM = "medium"
    CHATTY = "chatty"

    def __str__(self) -> str:
        return self.value