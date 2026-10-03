"""TurnTraceRow — SQLAlchemy model for tool-turn traces. Table `turn_traces`."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tpa.persistence.session import Base


class TurnTraceRow(Base):
    __tablename__ = "turn_traces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now, index=True)
    agent: Mapped[str] = mapped_column(String(40), nullable=False)
    user_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reply: Mapped[str] = mapped_column(Text, nullable=False, default="")
    decided: Mapped[str] = mapped_column(String(16), nullable=False)
    forced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    narrated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    total_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    calls_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    meta_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # prompt_tokens, timings_ms, notes
