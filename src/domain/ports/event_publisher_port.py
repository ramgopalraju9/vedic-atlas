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
