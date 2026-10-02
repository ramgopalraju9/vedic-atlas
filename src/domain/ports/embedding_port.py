from typing import Protocol, runtime_checkable
from domain.value_objects.embedding import Embedding

@runtime_checkable
class EmbeddingPort(Protocol):
    """Turns text into a fixed-length vector for memory retrieval."""

    @property
    def model_id(self) -> str:
        """Identifier of the embedding model in use."""
        ...

    async def embed(self, text: str) -> Embedding:
        """Embed a single piece of text."""
        ...
