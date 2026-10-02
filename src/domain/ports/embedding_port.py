"""EmbeddingPort — turns text into a fixed-length vector for memory retrieval.

New — the donor had no embedding concept (Memory & Recall is a new
domain, see roadmap Epic 4). Per ADR-005: fastembed-backed adapters
(BGE-small on laptop, MiniLM on Pi), never a cloud embedding API.
"""

from typing import Protocol, runtime_checkable

from domain.value_objects.embedding import Embedding


@runtime_checkable
class EmbeddingPort(Protocol):
    """Produces embeddings for storage and similarity search."""

    @property
    def model_id(self) -> str:
        """Identifier of the embedding model in use."""
        ...

    async def embed(self, text: str) -> Embedding:
        """Embed a single piece of text."""
        ...