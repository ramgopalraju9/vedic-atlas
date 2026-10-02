from dataclasses import dataclass
from datetime import datetime
from domain.value_objects.audio_window import AudioWindow

@dataclass
class Utterance:
    """A speech segment ready for transcription. In-memory only."""

    audio: AudioWindow
    started_at: datetime
    duration_sec: float
    frame_count: int
    truncated: bool = False
