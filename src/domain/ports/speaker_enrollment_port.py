"""SpeakerEnrollmentPort — records a new speaker's voice profile.

New port, paired with SpeakerRecognitionPort but kept separate (ISP):
VoiceSession never needs to see these methods — only the enrollment
service (service/speakers/speaker_enrollment_service.py) depends on this
port. Bundling enrollment into the recognition port would let the hot
voice loop call setup-only methods it has no business touching.

Shape follows Picovoice Eagle's EagleProfiler: enrollment is iterative
(feed frames until the vendor reports 100%), not a single call.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class SpeakerEnrollmentPort(Protocol):
    """One-at-a-time speaker voice-profile enrollment."""

    @property
    def frame_length(self) -> int:
        """Number of samples this enroller expects per call to enroll_feed()."""
        ...

    @property
    def sample_rate(self) -> int:
        """Sample rate (Hz) this enroller expects."""
        ...

    def enroll_feed(self, frame: bytes) -> tuple[float, str]:
        """Feed one frame of enrollment audio.

        Returns (percentage 0-100 complete, vendor feedback string e.g.
        "audio too short" / "good").
        """
        ...

    def enroll_reset(self) -> None:
        """Discard progress on the in-flight enrollment session."""
        ...

    def enroll_finish(self, speaker_name: str) -> None:
        """Export the completed profile and persist it under speaker_name.

        Raises if enrollment has not reached 100%.
        """
        ...

    def list_enrolled(self) -> list[str]:
        """Names of every currently-persisted speaker profile."""
        ...

    def delete_enrolled(self, speaker_name: str) -> bool:
        """Remove a persisted profile. Returns False if it didn't exist."""
        ...
