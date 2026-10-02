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
    """Implements MuteSwitchPort via a GPIO pin (pull-up, active-low button)."""

    def __init__(self, pin: int = 17, debounce_ms: int = 20):
        self._pin = pin
        self._debounce_ms = debounce_ms
        self._muted = False
        self._subscribers: list[Callable[[CaptureEvent], None]] = []
        self._gpio = None

    @property
    def source_name(self) -> str:
        return "gpio"

    def start(self) -> None:
        import RPi.GPIO as GPIO

        self._gpio = GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self._pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.add_event_detect(
            self._pin, GPIO.BOTH, callback=self._on_edge, bouncetime=self._debounce_ms
        )

    def stop(self) -> None:
        if self._gpio is not None:
            self._gpio.remove_event_detect(self._pin)

    def _on_edge(self, channel: int) -> None:
        # Active-low: pin reads LOW when the button is pressed (muted).
        new_muted = self._gpio.input(self._pin) == self._gpio.LOW
        if new_muted == self._muted:
            return
        previous = self._muted
        self._muted = new_muted
        event = CaptureEvent(muted=new_muted, previous_muted=previous, source=self.source_name, changed_at=datetime.now(timezone.utc))
        for callback in self._subscribers:
            callback(event)

    def is_muted(self) -> bool:
        return self._muted

    def subscribe(self, on_change: Callable[[CaptureEvent], None]) -> None:
        self._subscribers.append(on_change)