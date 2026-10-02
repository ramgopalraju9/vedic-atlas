"""VoiceActivityPort — decides whether a short audio frame contains speech.

★ New, PS-mandatory (REQ-M-01 continuous local sensing). Kept as its own
Protocol rather than folding the decision into STTPort because the two
have very different cost profiles: VAD runs on EVERY captured frame and
must be microseconds-cheap, while STT runs once per utterance and costs
hundreds of milliseconds. Separating them is what lets the always-on loop
stay cheap enough to run continuously on a Raspberry Pi.

Adapters live in tpa/audio/. The reference adapter wraps WebRTC's VAD
(pure C, no model file, no network); a Silero/ONNX adapter could be
swapped in later with zero changes above this port.
"""

from typing import Protocol, runtime_checkable

from domain.value_objects.audio_window import AudioWindow


@runtime_checkable
class VoiceActivityPort(Protocol):
    """Classifies a single short audio frame as speech or not."""

    @property
    def frame_duration_ms(self) -> int:
        """Frame size this detector requires (typically 10, 20 or 30 ms)."""
        ...

    def is_speech(self, frame: AudioWindow) -> bool:
        """True if the frame contains speech. Must be cheap — called continuously."""
        ...