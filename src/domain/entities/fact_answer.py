from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class FactAnswer:
    """The answer to a FactQuery, with provenance."""

    provider_id: str
    category: str
    text: str
    sources: tuple[str, ...] = field(default_factory=tuple)
    fetched_at: datetime | None = None
