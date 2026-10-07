"""SessionContext — what we are doing *right now*, per (session, speaker).

Small structured state, distinct from conversation history (prose) and saved facts
(permanent). It records the last successful tool and the validated slots it was called
with, so a follow-up such as "should I bring an umbrella?" can reuse `place=Tokyo`.

Pure data. One row per (session_id, speaker_id); persisted by a SessionContextPort adapter.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class SessionContext:
    session_id: str
    speaker_id: str            # "" until speaker profiles ship; part of the key from day one
    tool: str                  # last successful tool name
    slots: dict[str, Any]      # the validated args that tool was actually called with
    updated_at: datetime
    expires_at: datetime
    last_result_ids: list[str] = field(default_factory=list)  # references for "the second one" follow-ups
    pending: dict[str, Any] = field(default_factory=dict)     # an unanswered clarification (used from Phase 3)
