"""MemoryVectorRow — SQLAlchemy model for the local embedding index.

New (Feature B). Vectors are stored as packed float32 bytes in a BLOB; the
`model_id` column lets search filter to the current embedding model so a
model change never mixes incompatible vectors.
"""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tpa.persistence.session import Base


class MemoryVectorRow(Base):
    """One embedded memory item (a fact or a conversation summary)."""

    __tablename__ = "memory_vectors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    ref_id: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(160), nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("source", "ref_id", "model_id", name="uq_memvec_source_ref_model"),
        Index("ix_memvec_model_source", "model_id", "source"),
    )