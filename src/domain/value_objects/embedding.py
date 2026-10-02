"""Embedding — a fixed-length vector produced by the embedding provider.

New. Kept dependency-free deliberately (a plain tuple of floats, not a
numpy array) so the domain layer never needs numpy as a dependency —
conversion to/from numpy happens at the tpa/inference adapter boundary.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Embedding:
    """A fixed-length embedding vector with its provenance."""

    vector: tuple[float, ...]
    model_id: str

    @property
    def dim(self) -> int:
        return len(self.vector)