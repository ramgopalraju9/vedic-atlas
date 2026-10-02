"""SqliteVectorStore — VectorStorePort via packed float32 BLOBs in SQLite.

New (Feature B). Pure-Python cosine over the stored vectors: no native
extension, no extra dependency, so it runs unchanged on a Raspberry Pi.
Full-scan is fine at this scale (facts + summaries = hundreds of rows);
swap for sqlite-vec behind the same port if the corpus ever grows large.
"""

from __future__ import annotations

import struct
from datetime import datetime

from sqlalchemy import delete as sa_delete, select

from domain.value_objects.memory_hit import MemoryHit
from tpa.persistence.models.memory_vector import MemoryVectorRow
from tpa.persistence.session import SessionLocal


def _pack(vector: tuple[float, ...]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _unpack(blob: bytes) -> tuple[float, ...]:
    return struct.unpack(f"<{len(blob) // 4}f", blob)


def _cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class SqliteVectorStore:
    """Implements VectorStorePort over the existing SQLite database."""

    def __init__(self, *, session_factory=None):
        self._session = session_factory or SessionLocal

    def upsert(
        self, *, source: str, ref_id: str, model_id: str, vector: tuple[float, ...], text: str
    ) -> None:
        blob = _pack(vector)
        with self._session() as s, s.begin():
            row = s.scalar(
                select(MemoryVectorRow).where(
                    MemoryVectorRow.source == source,
                    MemoryVectorRow.ref_id == ref_id,
                    MemoryVectorRow.model_id == model_id,
                )
            )
            if row is None:
                s.add(
                    MemoryVectorRow(
                        source=source, ref_id=ref_id, model_id=model_id,
                        dim=len(vector), vector=blob, text=text, created_at=datetime.now(),
                    )
                )
            else:
                row.vector = blob
                row.dim = len(vector)
                row.text = text
                row.created_at = datetime.now()

    def search(
        self,
        *,
        vector: tuple[float, ...],
        model_id: str,
        top_k: int = 5,
        sources: tuple[str, ...] | None = None,
    ) -> list[MemoryHit]:
        with self._session() as s:
            stmt = select(MemoryVectorRow).where(MemoryVectorRow.model_id == model_id)
            if sources:
                stmt = stmt.where(MemoryVectorRow.source.in_(list(sources)))
            rows = list(s.scalars(stmt))
        hits = [
            MemoryHit(source=r.source, ref_id=r.ref_id, text=r.text, score=_cosine(vector, _unpack(r.vector)))
            for r in rows
        ]
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]

    def has(self, *, source: str, ref_id: str, model_id: str) -> bool:
        with self._session() as s:
            return s.scalar(
                select(MemoryVectorRow.id).where(
                    MemoryVectorRow.source == source,
                    MemoryVectorRow.ref_id == ref_id,
                    MemoryVectorRow.model_id == model_id,
                )
            ) is not None

    def delete(self, *, source: str, ref_id: str) -> None:
        with self._session() as s, s.begin():
            s.execute(
                sa_delete(MemoryVectorRow).where(
                    MemoryVectorRow.source == source, MemoryVectorRow.ref_id == ref_id
                )
            )

    def clear(self, source: str) -> None:
        with self._session() as s, s.begin():
            s.execute(sa_delete(MemoryVectorRow).where(MemoryVectorRow.source == source))