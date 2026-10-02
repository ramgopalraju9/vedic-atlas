from typing import Callable, Protocol, runtime_checkable
from domain.events.capture_event import CaptureEvent

@runtime_checkable
class MuteSwitchPort(Protocol):
    """A hardware or software source of mute/unmute transitions."""

    @property
    def source_name(self) -> str:
        """Which adapter this is. "gpio" | "hid" | "keyboard_fallback" | ... """
        ...

    def is_muted(self) -> bool:
        """Current state, read synchronously."""
        ...

    def subscribe(self, on_change: Callable[[CaptureEvent], None]) -> None:
        """Register a callback fired on every state transition."""
        ...
