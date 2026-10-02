from __future__ import annotations

from core.logging_config import logger
from domain.ports.embedding_port import EmbeddingPort
from domain.ports.vector_store_port import VectorStorePort


class MemoryIndexer:
    """Writes embeddings for facts and summaries into the vector store."""

    def __init__(self, store: VectorStorePort, embedding: EmbeddingPort | None):
        self._store = store
        self._embedding = embedding

    @property
    def enabled(self) -> bool:
        return self._embedding is not None

    async def index(self, self_source: str, ref_id: str, text: str, *, skip_existing: bool = False) -> bool:
        if self._embedding is None or not (text or "").strip():
            return False
        try:
            model_id = self._embedding.model_id
            if skip_existing and self._store.has(source=self_source, ref_id=str(ref_id), model_id=model_id):
                return False
            emb = await self._embedding.embed(text)
            self._store.upsert(
                source=self_source, ref_id=str(ref_id), model_id=model_id, vector=emb.vector, text=text
            )
            return True
        except Exception as e:
            logger.warning(f"[memory-index] failed to index {self_source}:{ref_id}: {e}")
            return False

    def remove(self, source: str, ref_id: str) -> None:
        try:
            self._store.delete(source=source, ref_id=str(ref_id))
        except Exception as e:
            logger.warning(f"[memory-index] failed to remove {source}:{ref_id}: {e}")