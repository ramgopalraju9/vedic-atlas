"""WakeWordPort — detects the trigger word in a stream of audio frames.

Donor: veda/voice/wakeword.py's WakeWordDetector (wraps pvporcupine).
The donor exposed `frame_length` / `sample_rate` properties (kept here
verbatim) but the actual per-frame detection call lived outside the class
(in the daemon/trigger loop, not yet read in this migration). `process()`
below is the natural Protocol shape for that call: porcupine's real API
returns a keyword index (>=0 on detection, -1 otherwise); adapters map
that to a bool so callers don't need to know porcupine's convention.
"""

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