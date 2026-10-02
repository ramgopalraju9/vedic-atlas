from typing import Protocol, runtime_checkable

@runtime_checkable
class IndicatorPort(Protocol):
    """A physical or software indicator of the current mute state."""

    def set_muted(self, muted: bool) -> None:
        """Reflect the current mute state. Called only by capture_gate."""
        ...
