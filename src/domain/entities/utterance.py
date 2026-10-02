"""Utterance — one complete stretch of speech, bounded by silence.

★ New. The unit the voice pipeline actually reasons about: VAD produces
frames, but STT and the agent chain want a whole utterance. Carries the
same "never persisted, never serialised" constraint as AudioWindow
(REQ-M-06) — it is raw microphone audio held in memory only, discarded
as soon as it has been transcribed.
"""

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

    # True when the utterance was cut off by the max-duration guard rather
    # than by natural silence — the transcript may be mid-sentence.
    truncated: bool = False