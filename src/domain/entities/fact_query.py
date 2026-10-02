"""FactQuery — a request into the fact-lookup provider registry.

New in the target architecture (no donor equivalent — the donor had no
online-fact concept at all). Deliberately generic: `category` selects the
registered FactProvider ("weather", "stocks", "flights", ...), `params`
carries whatever that category needs (e.g. {"lat": .., "lon": ..} for
weather). Adding a new category never requires changing this shape.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class FactQuery:
    """A request for a current public fact, routed by category."""

    category: str
    params: dict[str, Any] = field(default_factory=dict)
    requested_at: datetime | None = None