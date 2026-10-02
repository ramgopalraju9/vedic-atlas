"""SemanticRecall — meaning-based retrieval over indexed memory.

New (Feature B). Embeds the query once, ranks stored facts/summaries by
cosine via the vector store, and filters by a minimum score. No-op when no
embedding provider is available, so callers fall back to recency recall.
"""

from __future__ import annotations

from core.logging_config import logger
from domain.ports.embedding_port import EmbeddingPort
from domain.ports.vector_store_port import VectorStorePort
from domain.value_objects.memory_hit import MemoryHit


class SemanticRecall:
    """Query-time semantic retrieval over the vector store."""

    def __init__(
        self,
        store: VectorStorePort,
        embedding: EmbeddingPort | None,
        *,
        top_k: int = 5,
        min_score: float = 0.3,
        sources: tuple[str, ...] = ("fact", "summary"),
    ):
        self._store = store
        self._embedding = embedding
        self._top_k = top_k
        self._min_score = min_score
        self._sources = sources

    @property
    def enabled(self) -> bool:
        return self._embedding is not None

    async def recall(
        self, query: str, *, top_k: int | None = None, sources: tuple[str, ...] | None = None
    ) -> list[MemoryHit]:
        if self._embedding is None or not (query or "").strip():
            return []
        try:
            emb = await self._embedding.embed(query)
            hits = self._store.search(
                vector=emb.vector,
                model_id=self._embedding.model_id,
                top_k=top_k or self._top_k,
                sources=sources or self._sources,
            )
            return [h for h in hits if h.score >= self._min_score]
        except Exception as e:
            logger.warning(f"[semantic-recall] failed: {e}")
            return []

    @staticmethod
    def as_context_block(hits: list[MemoryHit]) -> str:
        if not hits:
            return ""
        lines = [f"- {h.text}" for h in hits]
        return "RELEVANT THINGS I REMEMBER:\n" + "\n".join(lines)