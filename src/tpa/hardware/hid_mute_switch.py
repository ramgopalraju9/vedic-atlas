"""HidMuteSwitch - implements MuteSwitchPort via a USB HID button (laptop dev).

* New, PS-mandatory on the laptop profile (REQ-M-04). Grounded in
docs/roadmap/09-privacy-compliance.md's laptop implementation plan
(BlinkStick / Stream Deck key as the recommended device). Polls the
device on a background thread since most simple HID buttons don't push
interrupts to Python.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Callable

from domain.events.capture_event import CaptureEvent


class HidMuteSwitch:
    """Implements MuteSwitchPort via a polled USB HID button."""

    def __init__(self, vendor_id: int, product_id: int, poll_interval_sec: float = 0.05):
        self._vendor_id = vendor_id
        self._product_id = product_id
        self._poll_interval = poll_interval_sec
        self._muted = False
        self._subscribers: list[Callable[[CaptureEvent], None]] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._device = None

    @property
    def source_name(self) -> str:
        return "hid"

    def start(self) -> None:
        import hid

        self._device = hid.device()
        self._device.open(self._vendor_id, self._product_id)
        self._device.set_nonblocking(True)
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        if self._device is not None:
            self._device.close()

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            report = self._device.read(8)
            if report:
                new_muted = bool(report[0] & 0x01)
                if new_muted != self._muted:
                    previous = self._muted
                    self._muted = new_muted
                    event = CaptureEvent(muted=new_muted, previous_muted=previous, source=self.source_name, changed_at=datetime.now(timezone.utc))
                    for callback in self._subscribers:
                        callback(event)
            time.sleep(self._poll_interval)

    def is_muted(self) -> bool:
        return self._muted

    def subscribe(self, on_change: Callable[[CaptureEvent], None]) -> None:
        self._subscribers.append(on_change)
