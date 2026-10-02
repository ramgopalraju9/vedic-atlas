"""GpioIndicator - implements IndicatorPort via a GPIO LED (Raspberry Pi).

* New, PS-mandatory (REQ-M-05). Driven only by capture_gate reacting to
the CaptureEvent - never by UI state directly (ADR-003).
"""

from __future__ import annotations


class GpioIndicator:
    """Implements IndicatorPort via a plain GPIO LED (or WS2812 in "on/off" mode)."""

    def __init__(self, pin: int = 27):
        self._pin = pin
        self._gpio = None

    def start(self) -> None:
        import RPi.GPIO as GPIO

        self._gpio = GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self._pin, GPIO.OUT)
        GPIO.output(self._pin, GPIO.LOW)

    def stop(self) -> None:
        if self._gpio is not None:
            self._gpio.output(self._pin, self._gpio.LOW)

    def set_muted(self, muted: bool) -> None:
        if self._gpio is None:
            return
        # LED on (listening) when NOT muted; off when muted.
        self._gpio.output(self._pin, self._gpio.LOW if muted else self._gpio.HIGH)
