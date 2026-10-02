"""HotkeyWakeWord – a fallback trigger when no wake-word engine is available.

* New – grounded in the config concept confirmed in the donor's
`VoiceConfig.hotkey`/`daemon_trigger` fields (config.py, read during Phase
1 of the codebase-guide work) but the actual pynput-based implementation
was never read in this migration (voice/triggers.py's exact API wasn't
grounded). Built as a minimal, honestly-new WakeWordPort-shaped adapter:
`process()` always returns False (a hotkey isn't frame-driven); a
separate `start()`/`stop()` pair registers a global hotkey callback,
which the ambient loop can treat as an alternate wake signal.
"""

from __future__ import annotations

from typing import Callable


class HotkeyWakeWord:
    """Push-to-talk fallback trigger – not frame-driven, so WakeWordPort.process()
    always returns False. Callers should also check `is_pressed()` alongside
    the normal per-frame WakeWordPort flow."""

    def __init__(self, hotkey: str = "<f8>"):
        self._hotkey = hotkey
        self._pressed = False
        self._listener = None

    @property
    def frame_length(self) -> int:
        return 0

    @property
    def sample_rate(self) -> int:
        return 0

    def process(self, frame: bytes) -> bool:
        return False

    def is_pressed(self) -> bool:
        return self._pressed

    def start(self, on_press: Callable[[], None] | None = None) -> None:
        from pynput import keyboard

        def _on_activate():
            self._pressed = True
            if on_press:
                on_press()

        self._listener = keyboard.GlobalHotKeys({self._hotkey: _on_activate})
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
