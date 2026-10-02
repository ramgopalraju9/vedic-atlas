"""SessionBoundaryPolicy — when does a new conversation session start.

Donor: veda/db/conversation_repo.py's ConversationRepository.current_session_id,
read in full. The real rule, extracted verbatim as a pure function: if the
gap since the last turn exceeds `session_gap_min` (donor default: 30), the
next turn starts a new session. The donor computed this inline against a
database row's timestamp; here it takes `last_activity_at` as a plain
argument so it's testable with no repository or clock dependency.
"""

from datetime import datetime, timedelta

DEFAULT_SESSION_GAP_MIN = 30


def is_same_session(
    last_activity_at: datetime,
    now: datetime,
    session_gap_min: int = DEFAULT_SESSION_GAP_MIN,
) -> bool:
    """True if `now` still belongs to the session that last acted at `last_activity_at`."""
    return (now - last_activity_at) <= timedelta(minutes=session_gap_min)