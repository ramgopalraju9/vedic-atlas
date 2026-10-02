from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

@dataclass
class FactQuery:
    """A request for a current public fact, routed by category."""
    category: str
    params: dict[str, Any] = field(default_factory=dict)
    requested_at: datetime | None = None
