"""AudioWindow — a bounded segment of captured PCM audio.

New. This is the ONLY shape raw audio is allowed to take anywhere in the
system (REQ-M-06: raw audio never leaves the device). It is in-memory
only — nothing may serialise, log, or persist an AudioWindow's `pcm`
field. Enforcement lives in service/privacy/raw_audio_lifecycle.py and
guardrails/audio_egress_blocker; this file only carries the shape.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class AudioWindow:
    """A bounded PCM audio segment. Never persisted, never serialised."""

    pcm: bytes
    sample_rate: int
    channels: int
    started_at: datetime
    duration_sec: float