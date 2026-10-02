"""MuteSwitchPort — the physical mute switch, as an event source.

New, PS-mandatory (REQ-M-04). No donor equivalent — VEDA had no hardware
privacy controls. Deliberately event-driven per the migration contract's
own rule: "MuteSwitchPort must be event-driven, not polled by callers."
capture_gate.py is the only subscriber that matters functionally; the
indicator and the audio adapter both react to capture_gate's own
re-broadcast of this event (see ADR-003 — one source of truth, so the
gate and the LED cannot diverge).
"""

from typing import Callable, Protocol, runtime_checkable

from domain.events.capture_event import CaptureEvent


@runtime_checkable
class MuteSwitchPort(Protocol):
    """A hardware or software source of mute/unmute transitions."""

    @property
    def source_name(self) -> str:
        """Which adapter this is: "gpio" | "hid" | "keyboard_fallback" | ..."""
        ...

    def is_muted(self) -> bool:
        """Current state, read synchronously (e.g. at startup)."""
        ...

    def subscribe(self, on_change: Callable[[CaptureEvent], None]) -> None:
        """Register a callback fired on every state transition."""
        ...