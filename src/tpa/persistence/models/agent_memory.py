"""AgentMemoryRow – SQLAlchemy ORM model for the cross-agent memory mesh.

Donor: veda/db/models.py's AgentMemory, read in full. Columns and indexes
ported verbatim.
"""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tpa.persistence.session import Base


class AgentMemoryRow(Base):
    """Persistent agent memory – records routing decisions and agent actions."""

    __tablename__ = "agent_memory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(32), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(40), nullable=False)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    context_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    user_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)

    __table_args__ = (
        Index("ix_memory_session_created", "session_id", "created_at"),
        Index("ix_memory_agent_created", "agent_name", "created_at"),
    )
