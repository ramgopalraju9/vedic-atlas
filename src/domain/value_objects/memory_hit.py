"""MemoryHit — one ranked result from a semantic memory search.

New (Feature B, Memory & Recall). Kept dependency-free: a plain dataclass
so the domain layer never needs the vector store or embedding library.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryHit:
    """A single similarity-search result with its provenance and score."""

    source: str  # "fact" | "summary" | ...
    ref_id: str
    text: str
    score: float