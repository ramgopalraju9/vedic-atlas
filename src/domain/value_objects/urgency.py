"""Urgency — how insistently an ambient event should interrupt the user.

Donor: veda/bus/events.py (Urgency enum), copied verbatim. The donor's
middle tier is named NORMAL; some of the planning docs in this migration
refer to the same tier as "MEDIUM" — same concept, this is the grounded
value from the actual bus implementation.
"""

from enum import Enum


class Urgency(str, Enum):
    LOW = "low"        # Suppress unless the user is in chatty mode
    NORMAL = "normal"  # Speak once, rate-limited with the rest
    HIGH = "high"       # Speak immediately, bypasses the rate limiter

    def __str__(self) -> str:
        return self.value