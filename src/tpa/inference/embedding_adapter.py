"""FastEmbedProvider — EmbeddingPort implementation via the `fastembed` library.

★ New, no donor equivalent (Memory & Recall, Epic 4, has no donor
precedent). Grounded in docs/roadmap/adr/ADR-005-embedding-provider.md:
BGE-small on laptop, MiniLM on Pi — both ONNX-backed via fastembed, no
torch dependency, no cloud embedding API.
"""

from __future__ import annotations

from pathlib import Path

from domain.value_objects.embedding import Embedding


class FastEmbedProvider:
    """Implements EmbeddingPort via a local ONNX embedding model."""

    def __init__(self, model_id: str = "BAAI/bge-small-en-v1.5", model_path: str | Path | None = None, cache_dir: str | Path | None = None):
        from fastembed import TextEmbedding  # imported lazily, see LlamaCppClient's rationale

        self._model_id = str(model_path or model_id)
        kwargs = {"model_name": self._model_id}
        if cache_dir is not None:
            kwargs["cache_dir"] = str(cache_dir)
        self._model = TextEmbedding(**kwargs)

    @property
    def model_id(self) -> str:
        return self._model_id

    async def embed(self, text: str) -> Embedding:
        import asyncio

        def _do():
            vectors = list(self._model.embed([text]))
            return tuple(float(x) for x in vectors[0])

        vector = await asyncio.to_thread(_do)
        return Embedding(vector=vector, model_id=self._model_id)