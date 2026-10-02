"""ConversationTurnRow / ConversationSummaryRow — SQLAlchemy ORM models.

Donor: veda/db/models.py's ConversationTurn/ConversationSummary, read in
full. Columns and indexes ported verbatim; classes renamed with a `Row`
suffix to keep them visually distinct from the domain entities
(domain.entities.conversation.Turn / ConversationSummary) that
tpa/persistence/mappers.py converts them to/from. Person/FaceEmbedding/
Observation dropped entirely — vision is out of scope.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tpa.persistence.session import Base


class ConversationTurnRow(Base):
    __tablename__ = "conversation_turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(32), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    summarized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_turns_session_created", "session_id", "created_at"),
        Index("ix_turns_summarized_session_created", "summarized", "session_id", "created_at"),
    )


class ConversationSummaryRow(Base):
    __tablename__ = "conversation_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(32), nullable=False)
    from_ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    to_ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)

    __table_args__ = (
        Index("ix_summaries_session_created", "session_id", "created_at"),
    )