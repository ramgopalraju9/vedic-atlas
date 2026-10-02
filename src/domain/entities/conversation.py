from dataclasses import dataclass
from datetime import datetime

@dataclass
class Turn:
    """One message in a conversation session."""
    id: int | None
    session_id: str
    role: str
    content: str
    created_at: datetime
    summarized: bool = False

@dataclass
class ConversationSummary:
    """A compacted range of turns, produced by the summariser."""
    id: int | None
    session_id: str
    from_t: datetime
    to_t: datetime
    content: str
    turn_count: int
    created_at: datetime
