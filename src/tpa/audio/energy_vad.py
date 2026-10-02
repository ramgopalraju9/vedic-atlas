"""EnergyVadDetector – implements VoiceActivityPort with RMS energy only.

* New, and the DEFAULT adapter. Chosen over `webrtcvad` for a concrete
reason: webrtcvad ships no wheel for Python 3.13+ and needs a C compiler
to build, which a locked-down corp laptop and a fresh Raspberry Pi image
both typically lack. This adapter needs numpy and nothing else, so the
always-on loop works on any machine that can run the rest of the stack.

It is also what the donor actually used – veda/voice/stt.py carried
`rms()` and `SILENCE_RMS_THRESHOLD` helpers, which the STT adapter's
docstring correctly flags as "VAD logic, not STT".

The one real weakness of naive energy VAD is that a fixed threshold is
wrong in every room. This adapter calibrates instead: it tracks a running
noise floor from quiet frames and fires when a frame is meaningfully
louder than the ambient level. A fan, a laptop in a quiet room, and a
noisy office therefore all self-tune without reconfiguration.

Trade-off, stated plainly: this detects *sound*, not *speech*. A door
slam can open an utterance where webrtcvad would not. Downstream that
costs one wasted STT pass, which returns empty text and is discarded by
VoiceSession's `min_chars` check. If higher precision is needed later,
`WebRtcVadDetector` implements the same port and swaps in via config.
"""

from __future__ import annotations

import math

from domain.value_objects.audio_window import AudioWindow


class EnergyVadDetector:
    """Implements VoiceActivityPort via adaptive RMS energy thresholding."""

    def __init__(
        self,
        frame_duration_ms: int = 30,
        sample_rate: int = 16_000,
        *,
        min_rms: float = 300.0,
        speech_ratio: float = 3.0,
        noise_adapt: float = 0.05,
        initial_noise_floor: float = 150.0,
    ):
        if frame_duration_ms <= 0:
            raise ValueError(f"frame_duration_ms must be positive, got {frame_duration_ms}")
        if sample_rate <= 0:
            raise ValueError(f"sample_rate must be positive, got {sample_rate}")

        self._frame_duration_ms = frame_duration_ms
        self._sample_rate = sample_rate
        self._expected_bytes = int(sample_rate * (frame_duration_ms / 1000.0)) * 2
        self._min_rms = min_rms
        self._speech_ratio = speech_ratio
        self._noise_adapt = noise_adapt
        self._noise_floor = initial_noise_floor

    @property
    def frame_duration_ms(self) -> int:
        return self._frame_duration_ms

    @property
    def expected_frame_bytes(self) -> int:
        return self._expected_bytes

    @property
    def noise_floor(self) -> float:
        """Current adaptive ambient level – useful for tuning and diagnostics."""
        return self._noise_floor

    @staticmethod
    def _rms(pcm: bytes) -> float:
        if not pcm:
            return 0.0
        try:
            import numpy as np

            samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
            if samples.size == 0:
                return 0.0
            return float(np.sqrt(np.mean(samples * samples)))
        except ImportError:
            # numpy is a core dependency, but never let VAD be the thing
            # that takes the loop down.
            import array

            samples = array.array("h")
            samples.frombytes(pcm[: len(pcm) // 2 * 2])
            if not samples:
                return 0.0
            return math.sqrt(sum(s * s for s in samples) / len(samples))

    def is_speech(self, frame: AudioWindow) -> bool:
        rms = self._rms(frame.pcm)
        threshold = max(self._min_rms, self._noise_floor * self._speech_ratio)

        if rms >= threshold:
            return True

        # Only quiet frames update the noise floor, so sustained speech
        # can't drag the threshold up above itself.
        self._noise_floor = (1 - self._noise_adapt) * self._noise_floor + self._noise_adapt * rms
        return False
