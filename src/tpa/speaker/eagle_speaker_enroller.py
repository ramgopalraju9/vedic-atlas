"""EagleSpeakerEnroller — implements SpeakerEnrollmentPort via `pveagle`.

Wraps a single `EagleProfiler`, re-set between sessions rather than
recreated (`.reset()` is the vendor's documented way to discard progress
and start enrolling a new speaker without paying native-library setup
cost again).

Verified against the installed `pveagle` package: `EagleProfiler.enroll(pcm)
-> float` returns only a completion percentage, not a structured feedback
object (no `EagleProfilerEnrollFeedback` exists in this SDK version,
despite some vendor docs implying one) - `enroll_feed()` synthesizes a
plain-English feedback string from percentage buckets so the Port's
contract still carries useful text without inventing vendor data that
doesn't exist.
"""

from __future__ import annotations

import struct
from pathlib import Path

from tpa.speaker.profile_store import EagleProfileStore


class EagleSpeakerEnroller:
    """Implements SpeakerEnrollmentPort via pveagle's EagleProfiler."""

    def __init__(self, access_key: str, profiles_dir: str | Path):
        import pveagle

        self._pveagle = pveagle
        self._access_key = access_key
        self._store = EagleProfileStore(profiles_dir)
        self._profiler = pveagle.create_profiler(access_key=access_key)

    @property
    def frame_length(self) -> int:
        return self._profiler.frame_length

    @property
    def sample_rate(self) -> int:
        return self._profiler.sample_rate

    def enroll_feed(self, frame: bytes) -> tuple[float, str]:
        pcm = struct.unpack_from("h" * self.frame_length, frame)
        percentage = self._profiler.enroll(pcm)
        if percentage >= 100.0:
            feedback = "complete"
        elif percentage >= 50.0:
            feedback = "good, keep going"
        else:
            feedback = "collecting"
        return percentage, feedback

    def enroll_reset(self) -> None:
        self._profiler.reset()

    def enroll_finish(self, speaker_name: str) -> None:
        profile = self._profiler.export()
        self._store.save(speaker_name, profile.to_bytes())
        self._profiler.reset()

    def list_enrolled(self) -> list[str]:
        return sorted(self._store.load_all().keys())

    def delete_enrolled(self, speaker_name: str) -> bool:
        return self._store.delete(speaker_name)

    def stop(self) -> None:
        delete = getattr(self._profiler, "delete", None)
        if callable(delete):
            try:
                delete()
            except Exception:
                pass
