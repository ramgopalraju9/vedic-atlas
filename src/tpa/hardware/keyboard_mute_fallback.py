"""KeyboardMuteFallback - implements MuteSwitchPort via a global hotkey.

* New. Fallback path when no hardware mute device is detected - per
docs/roadmap/09-privacy-compliance.md, this is explicitly WEAKER than a
hardware switch (software can be bypassed) and must surface a
"software mute - no hardware detected" badge in the UI. That badge is a
controller-layer concern; this adapter just implements the port and
reports `source_name = "keyboard_fallback"` so callers can render the
weaker-trust badge.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from domain.events.capture_event import CaptureEvent


class KeyboardMuteFallback:
    """Implements MuteSwitchPort via a toggling global hotkey (pynput)."""

    def __init__(self, hotkey: str = "<f9>"):
        self._hotkey = hotkey
        self._muted = False
        self._subscribers: list[Callable[[CaptureEvent], None]] = []
        self._listener = None

    @property
    def source_name(self) -> str:
        return "keyboard_fallback"

    def start(self) -> None:
        from pynput import keyboard

        def _toggle():
            previous = self._muted
            self._muted = not self._muted
            event = CaptureEvent(muted=self._muted, previous_muted=previous, source=self.source_name, changed_at=datetime.now(timezone.utc))
            for callback in self._subscribers:
                callback(event)

        self._listener = keyboard.GlobalHotKeys({self._hotkey: _toggle})
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def is_muted(self) -> bool:
        return self._muted

    def subscribe(self, on_change: Callable[[CaptureEvent], None]) -> None:
        self._subscribers.append(on_change)
