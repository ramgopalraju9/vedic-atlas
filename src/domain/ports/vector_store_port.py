"""VectorStorePort — local similarity search over stored embeddings.

New (Feature B, Memory & Recall). Per ADR-005 the embedding provider is
local; this port is the storage/search side of the same concern. Kept
backend-agnostic so a pure-Python SQLite store (laptop/Pi) can be swapped
for sqlite-vec or another index later with no caller change.
"""

from typing import Protocol, runtime_checkable

from domain.value_objects.memory_hit import MemoryHit


@runtime_checkable
class VectorStorePort(Protocol):
    """Stores embedding vectors and ranks them against a query vector."""

    def upsert(
        self, *, source: str, ref_id: str, model_id: str, vector: tuple[float, ...], text: str
    ) -> None:
        """Insert or replace the vector for (source, ref_id, model_id)."""
        ...

    def search(
        self,
        *,
        vector: tuple[float, ...],
        model_id: str,
        top_k: int = 5,
        sources: tuple[str, ...] | None = None,
    ) -> list[MemoryHit]:
        """Return the top-k most similar rows for the current model, by cosine."""
        ...

    def has(self, *, source: str, ref_id: str, model_id: str) -> bool:
        """True if a vector already exists for this key (for idempotent backfill)."""
        ...

    def delete(self, *, source: str, ref_id: str) -> None:
        """Remove the vector(s) for one reference."""
        ...

    def clear(self, source: str) -> None:
        """Remove every vector for a source (used to resync a small set)."""
        ...