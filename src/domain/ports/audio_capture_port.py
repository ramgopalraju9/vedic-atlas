"""AudioCapturePort — the only interface allowed to yield raw microphone audio.

Donor: veda/voice/audio_io.py's MicStream (sounddevice RawInputStream +
thread-safe queue), adapted to the Protocol shape: `start()`/`stop()`/
`read()` map directly to MicStream's real methods. `read()` returns a
domain AudioWindow (not a bare numpy array) so nothing above this port
ever sees a vendor-specific buffer type.

REQ-M-06 invariant: only tpa/audio/ may implement this Protocol, and
nothing implementing it may write its output to disk or to a socket
before it passes through service/privacy/capture_gate.py.
"""

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