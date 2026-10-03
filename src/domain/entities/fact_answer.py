"""FactAnswer — the result of a fact-lookup provider call.

New in the target architecture. `sources` is the exact host(s) that
answered — the Privacy panel renders this per-turn so a judge can see
that only the declared category's host was contacted (REQ-M-13).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class FactAnswer:
    """The answer to a FactQuery, with provenance."""

    provider_id: str
    category: str
    text: str
    sources: tuple[str, ...] = field(default_factory=tuple)
    fetched_at: datetime | None = None
    # Structured form of `text` (numbers, places, hits) for the tool layer, so
    # callers never have to re-parse prose. Provider-specific keys.
    data: dict[str, Any] = field(default_factory=dict)
    cached: bool = False  # served from the TTL cache, not a fresh network call