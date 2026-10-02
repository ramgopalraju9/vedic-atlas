"""IndicatorPort — the visible listening indicator (LED).

New, PS-mandatory (REQ-M-05). No donor equivalent. Must only ever be
driven by capture_gate.py reacting to the same CaptureEvent the audio
adapter reacts to — never by UI state — so the light can never lie
about whether the microphone is actually live (ADR-003).
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class IndicatorPort(Protocol):
    """A physical or software indicator of the current mute state."""

    def set_muted(self, muted: bool) -> None:
        """Reflect the current mute state. Called only by capture_gate."""
        ...