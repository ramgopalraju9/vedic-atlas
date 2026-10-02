from typing import Protocol, runtime_checkable
from domain.value_objects.audio_window import AudioWindow
from domain.value_objects.transcript import Transcript

@runtime_checkable
class STTPort(Protocol):
    """Transcribes an in-memory audio window to text."""

    async def transcribe(self, audio: AudioWindow) -> Transcript:
        """Transcribe one bounded audio segment."""
        ...
