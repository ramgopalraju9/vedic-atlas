"""CaptureEvent — a mute/unmute state transition.

New, PS-mandatory. Published by capture_gate.py on every state change and
consumed by exactly two subscribers: the audio capture adapter (to gate
itself) and indicator_controller.py (to drive the LED/display). Both
subscribe to the SAME event so they cannot diverge — see ADR-003.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class CaptureEvent:
    """A mute/unmute transition, broadcast to every capture-gated consumer."""

    muted: bool
    previous_muted: bool
    source: str  # which MuteSwitch adapter raised this: "gpio" | "hid" | "keyboard_fallback" | ...
    changed_at: datetime