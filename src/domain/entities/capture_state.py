"""CaptureState — the current mute/listening state of the device.

New in the target architecture — this is the entity capture_gate.py (the
single source of truth for REQ-M-04/M-05) reads and mutates. `mute_source`
records which adapter is currently authoritative (e.g. "gpio", "hid",
"keyboard_fallback", "software_fallback") so the UI/LED can show a weaker
badge when the hardware source is not the one in control.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class CaptureState:
    """Current mute/listening state, held by capture_gate."""

    muted: bool
    mute_source: str
    since: datetime