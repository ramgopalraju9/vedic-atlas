"""STTPort — speech-to-text.

New protocol shape (the donor's veda/voice/stt.py returned a bare string
from a single faster-whisper call site — there was no abstraction to
port). Adapters: FasterWhisperProvider (laptop), WhisperCppProvider (Pi).
Takes an AudioWindow rather than a file path — REQ-M-06 requires raw
audio to stay in memory, never touch disk.
"""

from typing import Protocol, runtime_checkable

from domain.value_objects.audio_window import AudioWindow
from domain.value_objects.transcript import Transcript


@runtime_checkable
class STTPort(Protocol):
    """Transcribes an in-memory audio window to text."""

    async def transcribe(self, audio: AudioWindow) -> Transcript:
        """Transcribe one bounded audio segment."""
        ...