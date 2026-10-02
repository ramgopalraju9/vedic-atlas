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
