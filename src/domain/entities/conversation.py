"""Conversation entities — pure data, no ORM.

Donor: veda/db/models.py (ConversationTurn, ConversationSummary only —
Person and FaceEmbedding are dropped, vision is out of scope; Observation
is dropped for the same reason). The SQLAlchemy `Mapped`/`mapped_column`
machinery and the `__tablename__`/`Index` definitions stay behind in
tpa/persistence/models/ (a later batch) with an explicit mapper converting
between the ORM row and these dataclasses — per migration rule 6, no
SQLAlchemy type may cross into service/ or domain/.
"""

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
    from_ts: datetime
    to_ts: datetime
    content: str
    turn_count: int
    created_at: datetime