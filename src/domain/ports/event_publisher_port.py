"""EventPublisherPort — the in-process ambient event bus.

Donor: veda/bus/broker.py's EventBus, read in full — method set below
matches its real public API (`publish`, `subscribe`, `unsubscribe`)
exactly. `publish_nowait` (the donor's sync-context wrapper) is a
convenience built on top of `publish` and is an implementation detail of
the adapter, not part of the port contract.
"""

import asyncio
from typing import Protocol, runtime_checkable

from domain.events.ambient_event import AmbientEvent


@runtime_checkable
class EventPublisherPort(Protocol):
    """Fan-out publish/subscribe for AmbientEvents."""

    async def publish(self, event: AmbientEvent) -> int:
        """Fan out to all subscribers. Returns count delivered."""
        ...

    def subscribe(self, maxsize: int = 100) -> "asyncio.Queue[AmbientEvent]":
        """Register a new subscriber queue."""
        ...

    def unsubscribe(self, q: "asyncio.Queue[AmbientEvent]") -> None:
        """Remove a subscriber queue."""
        ...