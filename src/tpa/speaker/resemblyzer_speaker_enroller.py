"""ResemblyzerSpeakerEnroller — implements SpeakerEnrollmentPort via `resemblyzer`.

★ New. Open-source, no-API-key replacement for EagleSpeakerEnroller — see
docs/voice/open-source-wake-speaker-design.md §4.3.

Collects `min_enroll_seconds` of audio, splits it into `sub_utterance_sec`
sub-utterances, embeds each one, and persists the averaged, re-normalized
embedding as the enrolled profile — standard d-vector enrollment practice,
more robust to one noisy stretch of audio than a single long embedding.

Like the recognizer, `frame_length` must be nonzero even though
Resemblyzer has no real per-frame size requirement:
`SpeakerEnrollmentService.feed()` (service/speakers/speaker_enrollment_service.py)
computes `frame_bytes = self._enroller.frame_length * 2` and loops
`while len(self._buffer) >= frame_bytes`, which would spin forever on
empty chunks if frame_length were 0.

Raises a plain `ValueError` on an incomplete enrollment, not an
AppException — tpa/ adapters never import exceptions/core.enums in this
codebase (same as EagleSpeakerEnroller, LlamaCppClient, etc.);
`SpeakerEnrollmentService.finish()` already catches any exception from
`enroll_finish()` and wraps it into `AppException(VALIDATION_ERROR)`
itself (service/speakers/speaker_enrollment_service.py:81-90), so
translation into the app's exception model stays a service-layer
responsibility, never an adapter one.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from tpa.speaker.resemblyzer_profile_store import ResemblyzerProfileStore

_FRAME_SAMPLES = 1280  # 80ms @ 16kHz — arbitrary but nonzero, see module docstring


class ResemblyzerSpeakerEnroller:
    """Implements SpeakerEnrollmentPort via resemblyzer's VoiceEncoder."""

    def __init__(
        self,
        profiles_dir: str | Path,
        min_enroll_seconds: float = 12.0,
        sub_utterance_sec: float = 3.0,
        sample_rate: int = 16000,
    ):
        from resemblyzer import VoiceEncoder

        self._encoder = VoiceEncoder("cpu")
        self._store = ResemblyzerProfileStore(profiles_dir)
        self._sample_rate = sample_rate
        self._min_samples = int(min_enroll_seconds * sample_rate)
        self._sub_samples = int(sub_utterance_sec * sample_rate)
        self._buf = bytearray()

    @property
    def frame_length(self) -> int:
        return _FRAME_SAMPLES

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def enroll_feed(self, frame: bytes) -> tuple[float, str]:
        self._buf += frame
        collected_samples = len(self._buf) // 2
        pct = min(100.0, 100.0 * collected_samples / self._min_samples)
        if pct >= 100.0:
            feedback = "enough audio collected - call finish to save"
        elif pct >= 50.0:
            feedback = "good, keep going"
        else:
            feedback = "collecting"
        return pct, feedback

    def enroll_reset(self) -> None:
        self._buf = bytearray()

    def enroll_finish(self, speaker_name: str) -> None:
        collected_samples = len(self._buf) // 2
        if collected_samples < self._min_samples:
            raise ValueError(
                f"enrollment needs at least {self._min_samples / self._sample_rate:.0f}s of audio, "
                f"got {collected_samples / self._sample_rate:.1f}s"
            )
        pcm = np.frombuffer(bytes(self._buf), dtype=np.int16).astype(np.float32) / 32768.0
        sub_embeds = [
            self._encoder.embed_utterance(pcm[i : i + self._sub_samples])
            for i in range(0, len(pcm) - self._sub_samples + 1, self._sub_samples)
        ]
        averaged = np.mean(sub_embeds, axis=0)
        averaged /= np.linalg.norm(averaged)
        self._store.save(speaker_name, averaged)
        self._buf = bytearray()

    def list_enrolled(self) -> list[str]:
        return sorted(self._store.load_all().keys())

    def delete_enrolled(self, speaker_name: str) -> bool:
        return self._store.delete(speaker_name)
