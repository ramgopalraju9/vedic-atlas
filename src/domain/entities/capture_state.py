from dataclasses import dataclass
from datetime import datetime

@dataclass
class CaptureState:
    """Current mute/listening state, held by capture_gate."""
    muted: bool
    mute_source: str
    since: datetime
