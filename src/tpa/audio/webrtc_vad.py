"""WebRtcVadDetector – implements VoiceActivityPort via `webrtcvad`.

* New. WebRTC's VAD is a pure-C GMM classifier: no model file, no
network, sub-millisecond per frame. That combination is why it's the
reference adapter here – a neural VAD (Silero) would be more accurate
but needs a model file on disk and ~10x the CPU, which the Pi profile
can't spare on a loop that runs continuously.

Hard constraints from the underlying library, validated at construction
rather than failing cryptically per-frame later:
  * sample rate must be 8k / 16k / 32k / 48k Hz
  * frame must be exactly 10, 20 or 30 ms of 16-bit mono PCM
"""

from __future__ import annotations

from domain.value_objects.audio_window import AudioWindow

_VALID_RATES = (8000, 16000, 32000, 48000)
_VALID_FRAME_MS = (10, 20, 30)


class WebRtcVadDetector:
    """Implements VoiceActivityPort using WebRTC's GMM voice activity detector."""

    def __init__(self, aggressiveness: int = 2, frame_duration_ms: int = 30, sample_rate: int = 16_000):
        if frame_duration_ms not in _VALID_FRAME_MS:
            raise ValueError(f"frame_duration_ms must be one of {_VALID_FRAME_MS}, got {frame_duration_ms}")
        if sample_rate not in _VALID_RATES:
            raise ValueError(f"sample_rate must be one of {_VALID_RATES}, got {sample_rate}")
        if not 0 <= aggressiveness <= 3:
            raise ValueError(f"aggressiveness must be 0-3, got {aggressiveness}")

        import webrtcvad  # lazy: keeps this module importable without the extra installed

        self._vad = webrtcvad.Vad(aggressiveness)
        self._frame_duration_ms = frame_duration_ms
        self._sample_rate = sample_rate
        # 16-bit mono => 2 bytes per sample.
        self._expected_bytes = int(sample_rate * (frame_duration_ms / 1000.0)) * 2

    @property
    def frame_duration_ms(self) -> int:
        return self._frame_duration_ms

    @property
    def expected_frame_bytes(self) -> int:
        return self._expected_bytes

    def is_speech(self, frame: AudioWindow) -> bool:
        # A wrong-sized frame means the capture blocksize doesn't match the
        # VAD frame size. Returning False (rather than raising) keeps the
        # always-on loop alive; the mismatch is caught at wiring time by
        # VoiceSession, which sizes capture from this detector.
        if len(frame.pcm) != self._expected_bytes:
            return False
        try:
            return self._vad.is_speech(frame.pcm, self._sample_rate)
        except Exception:
            return False
