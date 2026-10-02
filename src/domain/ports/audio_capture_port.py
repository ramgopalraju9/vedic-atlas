from typing import Protocol, runtime_checkable
from domain.value_objects.audio_window import AudioWindow

@runtime_checkable
class AudioCapturePort(Protocol):
    """Captures microphone audio as bounded, in-memory windows."""

    @property
    def sample_rate(self) -> int:
        ...

    @property
    def channels(self) -> int:
        ...

    def start(self) -> None:
        """Open the capture stream."""
        ...

    def stop(self) -> None:
        """Close the capture stream."""
        ...

    def read(self, timeout: float = 1.0) -> AudioWindow | None:
        """Return the next captured window, or None on timeout."""
        ...
