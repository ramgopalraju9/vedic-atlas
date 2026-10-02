"""SoftwareMuteSwitch - MuteSwitchPort with no hardware and no dependencies.

* New. The laptop demo path until real mute hardware arrives. Unlike
`KeyboardMuteFallback` (which needs `pynput` and a focused desktop
session), this is driven purely by calls from the API/UI/CLI, so it works
headless and in tests.

WEAKER TRUST, deliberately labelled: `source_name` is "software" so the
Privacy panel can badge it as such. A software switch can be bypassed by
any code in this process; a hardware switch cuts the mic line. The PS
requires a *physical* switch - this adapter exists so the rest of the
capture-gate chain can be built, demoed, and tested now, and swapped for
`GpioMuteSwitch`/`HidMuteSwitch` with a one-line change in server.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from domain.events.capture_event import CaptureEvent


class SoftwareMuteSwitch:
    """Implements MuteSwitchPort; state changes come from `toggle`/`set_muted`."""

    def __init__(self, start_muted: bool = True):
        self._muted = start_muted
        self._subscribers: list[Callable[[CaptureEvent], None]] = []

    @property
    def source_name(self) -> str:
        return "software"

    def is_muted(self) -> bool:
        return self._muted

    def subscribe(self, on_change: Callable[[CaptureEvent], None]) -> None:
        self._subscribers.append(on_change)

    def set_muted(self, muted: bool) -> None:
        if muted == self._muted:
            return
        previous = self._muted
        self._muted = muted
        event = CaptureEvent(
            muted=muted,
            previous_muted=previous,
            source=self.source_name,
            changed_at=datetime.now(timezone.utc),
        )
        for callback in self._subscribers:
            callback(event)

    def toggle(self) -> bool:
        self.set_muted(not self._muted)
        return self._muted
