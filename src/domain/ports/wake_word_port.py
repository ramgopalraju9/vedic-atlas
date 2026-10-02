from typing import Protocol, runtime_checkable

@runtime_checkable
class WakeWordPort(Protocol):
    """Frame-by-frame wake-word detector."""

    @property
    def frame_length(self) -> int:
        """Number of samples this detector expects per call to process()."""
        ...

    @property
    def sample_rate(self) -> int:
        """Sample rate (Hz) this detector expects."""
        ...

    def process(self, frame: bytes) -> bool:
        """Feed one frame of PCM. Returns True the frame the keyword fires on."""
        ...
