"""SpeakerRecognitionPort — identifies which enrolled speaker is talking.

New port, no donor equivalent (the donor had no multi-user concept at
all). Grounded in the PS-mandatory "must not react to stray talk"
requirement: an adapter implementing this lets VoiceSession tell an
enrolled household member's voice apart from an unrecognized one.

Shape follows Picovoice Eagle's real API rather than flattening it to a
single best-match call: `process()` returns one score per enrolled
speaker (order matching `speaker_names`), same as Eagle's own
`Eagle.process(pcm) -> list[float]`. The caller (VoiceSession) does the
argmax/threshold itself — see its `_resolve_speaker()`.

Deliberately separate from SpeakerEnrollmentPort (enrollment is a
one-time setup lifecycle used only by an enrollment service; this port
is the hot path VoiceSession calls every frame while armed). Mirrors the
existing WakeWordPort split between detection and lifecycle.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class SpeakerRecognitionPort(Protocol):
    """Frame-by-frame multi-speaker identification."""

    @property
    def frame_length(self) -> int:
        """Number of samples this recognizer expects per call to process()."""
        ...

    @property
    def sample_rate(self) -> int:
        """Sample rate (Hz) this recognizer expects."""
        ...

    @property
    def speaker_names(self) -> list[str]:
        """Enrolled speaker names, in the same order as process()'s scores."""
        ...

    def process(self, frame: bytes) -> list[float]:
        """Feed one frame of PCM. Returns one score (0.0-1.0) per enrolled
        speaker, empty list if no speakers are enrolled yet."""
        ...

    def reload_profiles(self) -> None:
        """Re-read persisted speaker profiles from disk.

        Called after an enrollment completes so a live session picks up
        the new speaker without a restart.
        """
        ...
