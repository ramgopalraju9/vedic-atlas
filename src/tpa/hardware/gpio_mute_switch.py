"""GpioMuteSwitch — implements MuteSwitchPort via a GPIO button (Raspberry Pi).

★ New, PS-mandatory (REQ-M-04). No donor equivalent — VEDA had no
hardware privacy controls at all. Grounded in docs/roadmap/09-privacy-compliance.md
and ADR-003 (hardware mute is the sole source of truth). Debounced in
software (20ms) per the wiring notes in docs/roadmap/10-edge-readiness.md.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from domain.events.capture_event import CaptureEvent


class GpioMuteSwitch:
    """Implements MuteSwitchPort via a GPIO pin driven by a physical switch.

    Wiring (BCM 17 = header pin 11): a 3-position-style SPDT slide switch with
    its common contact on pin 11, one side on 3V3 (pin 1) and the other on GND
    (pin 6). The pin is therefore always driven HIGH or LOW; which level means
    "muted" is `muted_level` ("low" or "high"). The internal pull is set toward
    the muted level so an unplugged/broken wire fails closed (muted).
    """

    def __init__(self, pin: int = 17, debounce_ms: int = 50, muted_level: str = "low"):
        level = (muted_level or "low").lower()
        if level not in ("low", "high"):
            raise ValueError(f"muted_level must be 'low' or 'high', got {muted_level!r}")
        self._pin = pin
        self._debounce_ms = debounce_ms
        self._muted_is_high = level == "high"
        self._muted = True  # fail closed until start() reads the real position
        self._subscribers: list[Callable[[CaptureEvent], None]] = []
        self._gpio = None

    @property
    def source_name(self) -> str:
        return "gpio"

    def _read_muted(self) -> bool:
        high = self._gpio.input(self._pin) == self._gpio.HIGH
        return high == self._muted_is_high

    def start(self) -> None:
        import RPi.GPIO as GPIO

        self._gpio = GPIO
        GPIO.setmode(GPIO.BCM)
        pull = GPIO.PUD_UP if self._muted_is_high else GPIO.PUD_DOWN
        GPIO.setup(self._pin, GPIO.IN, pull_up_down=pull)
        # Adopt the physical position now: the gate treats is_muted() as authoritative at boot.
        self._muted = self._read_muted()
        GPIO.add_event_detect(
            self._pin, GPIO.BOTH, callback=self._on_edge, bouncetime=self._debounce_ms
        )

    def stop(self) -> None:
        if self._gpio is not None:
            self._gpio.remove_event_detect(self._pin)
            self._gpio.cleanup(self._pin)
            self._gpio = None

    def _on_edge(self, channel: int) -> None:
        if self._gpio is None:
            return
        new_muted = self._read_muted()
        if new_muted == self._muted:
            return
        previous = self._muted
        self._muted = new_muted
        event = CaptureEvent(muted=new_muted, previous_muted=previous, source=self.source_name, changed_at=datetime.now(timezone.utc))
        for callback in self._subscribers:
            callback(event)

    def sync_state(self, muted: bool) -> None:
        """Called by CaptureGate after a software (API/CLI) change so the next
        physical flip is compared against the real current state."""
        self._muted = muted

    def is_forced_muted(self) -> bool:
        """True while the physical switch itself is in the muted position (read live)."""
        return self._gpio is not None and self._read_muted()

    def is_muted(self) -> bool:
        return self._muted

    def subscribe(self, on_change: Callable[[CaptureEvent], None]) -> None:
        self._subscribers.append(on_change)
