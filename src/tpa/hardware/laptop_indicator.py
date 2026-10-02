"""BlinkStickIndicator / SoftwareIndicator — laptop IndicatorPort adapters.

★ New, PS-mandatory on the laptop profile (REQ-M-05). BlinkStickIndicator
is the recommended hardware path (docs/roadmap/09-privacy-compliance.md);
SoftwareIndicator is the "no hardware detected" fallback — logs state
changes and is meant to be paired with a persistent tray icon rendered by
the controller layer, not a substitute for real hardware trust.
"""

from __future__ import annotations

from core.logging_config import logger


class BlinkStickIndicator:
    """Implements IndicatorPort via a BlinkStick USB LED."""

    def __init__(self):
        self._stick = None

    def start(self) -> None:
        from blinkstick import blinkstick

        self._stick = blinkstick.find_first()
        if self._stick is None:
            raise RuntimeError("no BlinkStick device found")

    def stop(self) -> None:
        if self._stick is not None:
            self._stick.turn_off()

    def set_muted(self, muted: bool) -> None:
        if self._stick is None:
            return
        if muted:
            self._stick.set_color(red=255, green=0, blue=0)
        else:
            self._stick.set_color(red=0, green=255, blue=0)


class SoftwareIndicator:
    """Fallback IndicatorPort — logs state; pair with a UI tray icon."""

    def set_muted(self, muted: bool) -> None:
        logger.info(f"[indicator:software] mute state -> {muted} (no hardware LED detected)")