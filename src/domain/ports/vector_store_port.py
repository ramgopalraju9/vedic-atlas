from typing import Protocol, runtime_checkable
from domain.value_objects.memory_hit import MemoryHit

@runtime_checkable
class VectorStorePort(Protocol):
    """Stores embedding vectors and ranks them against a query vector."""

    def upsert(
        self, source: str, ref_id: str, model_id: str, vector: tuple[float, ...], text: str
    ) -> None:
        """Insert or replace the vector for {source, ref_id, model_id}."""
        ...

    def search(
        self,
        vector: tuple[float, ...],
        model_id: str,
        top_k: int,
        sources: tuple[str, ...] | None = None,
    ) -> list[MemoryHit]:
        """Return the top-k most similar rows for the current model, by cosine."""
        ...

    def has(self, source: str, ref_id: str, model_id: str) -> bool:
        """True if a vector already exists for this key."""
        ...

    def delete(self, source: str, ref_id: str) -> None:
        """Remove the vector(s) for one reference."""
        ...

    def clear(self, source: str) -> None:
        """Remove every vector for a source."""
        ...
