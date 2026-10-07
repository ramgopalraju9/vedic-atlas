"""SessionContextRow — SQLAlchemy model for per-(session, speaker) working state. Table `session_contexts`."""

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tpa.persistence.session import Base


class SessionContextRow(Base):
    __tablename__ = "session_contexts"

    session_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    speaker_id: Mapped[str] = mapped_column(String(64), primary_key=True, default="")
    tool: Mapped[str] = mapped_column(String(64), nullable=False)
    slots_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    last_result_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    pending_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
