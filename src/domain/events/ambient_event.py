"""AmbientEvent — one thing a sensor noticed and believes worth considering.

Donor: veda/bus/events.py (AmbientEvent dataclass), copied verbatim except
its EventKind/Urgency now come from domain/value_objects and domain/events
rather than being declared in the same file as the dataclass.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from domain.events.event_kind import EventKind
from domain.value_objects.urgency import Urgency


@dataclass
class AmbientEvent:
    """One thing a sensor noticed and believes worth considering."""

    kind: EventKind
    description: str
    urgency: Urgency = Urgency.NORMAL
    source: str = ""
    # Events sharing a dedupe_key within the debounce window collapse to one.
    dedupe_key: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: uuid4().hex[:12])
    timestamp: datetime = field(default_factory=datetime.now)