"""SemanticRecall — meaning-based retrieval over indexed memory.

Embeds the query once, ranks stored facts/summaries by cosine via the vector store, then keeps only what is worth the
prompt tokens. No-op when no embedding provider is available, so callers fall back to recency recall.

What "worth it" means (measured on the real summaries with bge-small, whose scores are compressed into ~0.45-0.85):
  * a floor: questions about the past score >= 0.66 against the right summary, ordinary questions have a median of ~0.56,
    so a floor near 0.62 keeps memory out of "what is 2 plus 2" (0.5 let 15 of 16 ordinary questions through);
  * a margin: the right summary is often second, only a few hundredths behind, so besides the best hit, every hit
    within `margin` of it is kept (up to top_k) instead of betting on rank 1;
  * a budget: each hit is cut to `max_hit_chars` and the block to `max_total_chars`, because on a Raspberry Pi every
    injected token is prefill time;
  * dates: a hit is labelled with the day it is from and the newest comes first, so "yesterday" and "last week" mean
    something to the model.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from core.logging_config import logger
from domain.ports.embedding_port import EmbeddingPort
from domain.ports.vector_store_port import VectorStorePort
from domain.value_objects.memory_hit import MemoryHit
from domain.policies.day_label_policy import day_label as _day
from service.conversation.backfill import cap_summary

# Example phrasings of 'what have we been talking about'. A question that is almost one of these is about the conversation
# itself, so it is answered from the NEWEST conversations, not from whichever summaries happen to be most similar.
# Measured with bge-small: unseen paraphrases of these score 0.82-1.0; specific questions ("what did we say about Barcelona")
# score <= 0.82 and ordinary ones <= 0.77, so the cut-off sits at 0.82.
CONVERSATION_PROTOTYPES = (
    "what are we discussing", "what have we been talking about", "what did we talk about earlier",
    "what did we talk about recently", "summarise our earlier conversation", "remind me what we discussed before",
    "what was our last conversation about", "what did we do last time", "what have we covered so far",
)


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
        margin: float | None = None,
        when: Callable[[MemoryHit], datetime | None] | None = None,
        max_hit_chars: int = 300,
        max_total_chars: int = 700,
        conversation_intent_min: float | None = None,
        min_score_by_source: dict[str, float] | None = None,
        conversation_prototypes: tuple[str, ...] = CONVERSATION_PROTOTYPES,
    ):
        self._store = store
        self._embedding = embedding
        self._top_k = top_k
        self._min_score = min_score
        self._sources = sources
        self._margin = margin
        self._when = when
        self._max_hit_chars = max_hit_chars
        self._max_total_chars = max_total_chars
        self._intent_min = conversation_intent_min
        self._floors = dict(min_score_by_source or {})   # a stricter floor for a source whose scores run high (short exchanges)
        self._prototypes = conversation_prototypes
        self._prototype_vectors: list[tuple[float, ...]] | None = None

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
            wanted = top_k or self._top_k
            searched = tuple(sources or self._sources)
            if self._floors and len(searched) > 1:
                # Each source is searched on its own and the results merged: a source with a stricter floor (many short
                # exchanges scoring just under it) must not fill the top few and crowd the others out before the floors apply.
                hits = sorted(
                    (h for src in searched for h in self._store.search(
                        vector=emb.vector, model_id=self._embedding.model_id, top_k=wanted, sources=(src,))),
                    key=lambda h: h.score, reverse=True,
                )
            else:
                hits = self._store.search(
                    vector=emb.vector, model_id=self._embedding.model_id, top_k=wanted, sources=searched,
                )
        except Exception as e:
            logger.warning(f"[semantic-recall] failed: {e}")
            return []
        hits = [h for h in hits if h.score >= self._floors.get(h.source, self._min_score)]
        if self._margin is not None and hits:
            best = hits[0].score
            hits = [h for h in hits if h.score >= best - self._margin]
        return hits[:wanted]

    async def conversation_intent_score(self, query: str) -> float:
        """How close the question is to "what have we been talking about?", 0..1 (cosine to the nearest example phrasing).
        0.0 when the mode is off (no threshold configured) or no embedding model is available."""
        if self._embedding is None or self._intent_min is None or not (query or "").strip():
            return 0.0
        try:
            if self._prototype_vectors is None:
                self._prototype_vectors = [(await self._embedding.embed(p)).vector for p in self._prototypes]
            vector = (await self._embedding.embed(query)).vector
        except Exception as e:
            logger.warning(f"[semantic-recall] intent check failed: {e}")
            return 0.0
        return max(sum(a * b for a, b in zip(vector, proto)) for proto in self._prototype_vectors)

    async def asks_about_conversation(self, query: str) -> bool:
        """True when the question is about the conversation itself ("what are we discussing?"), by meaning: it is nearly one
        of the example phrasings."""
        return self._intent_min is not None and await self.conversation_intent_score(query) >= self._intent_min

    def _when_of(self, hit: MemoryHit) -> datetime | None:
        try:
            return self._when(hit) if self._when else None
        except Exception as e:   # a missing date must never cost the memory itself
            logger.debug(f"[semantic-recall] no date for {hit.source}:{hit.ref_id}: {e}")
            return None

    def as_context_block(self, hits: list[MemoryHit]) -> str:
        if not hits:
            return ""
        # The budget goes to the best matches first (hits arrive best-first), then what survived is shown newest first.
        kept: list[tuple[datetime | None, str]] = []
        used = 0
        for hit in hits:
            when = self._when_of(hit)
            text = cap_summary(hit.text, self._max_hit_chars)
            line = f"- ({_day(when)}) {text}" if when else f"- {text}"
            if kept and used + len(line) > self._max_total_chars:
                continue   # a smaller, lower-ranked hit may still fit
            kept.append((when, line))
            used += len(line)
        kept.sort(key=lambda p: (p[0] is None, -(p[0].timestamp()) if p[0] else 0.0))
        return "RELEVANT THINGS I REMEMBER:\n" + "\n".join(line for _, line in kept)
