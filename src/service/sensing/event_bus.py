"""EventBus — in-process async pub/sub for AmbientEvents.

Donor: veda/bus/broker.py, read in full and ported near-verbatim — this
file needed no behavioural changes, only the import paths (AmbientEvent
now comes from domain.events). Implements EventPublisherPort (Batch 3).

Correction (2026-09-22): `publish_nowait` was rebuilt against an
incomplete read the first time (used `loop.create_task` + an immediate
`.done()` check, which is nonsensical — a just-created task is never
done). Fixed to match the real donor implementation: fire-and-forget via
`asyncio.ensure_future`, returning 0 gracefully if there's no running
loop instead of raising.
"""

import asyncio

from domain.events.ambient_event import AmbientEvent
from core.logging_config import logger


class EventBus:
    """Fan-out pub/sub. Each subscriber gets its own asyncio.Queue."""

    def __init__(self):
        self._subscribers: list[asyncio.Queue[AmbientEvent]] = []

    def subscribe(self, maxsize: int = 100) -> asyncio.Queue[AmbientEvent]:
        q: asyncio.Queue[AmbientEvent] = asyncio.Queue(maxsize=maxsize)
        self._subscribers.append(q)
        logger.info(f"EventBus: new subscriber (now {len(self._subscribers)})")
        return q

    def unsubscribe(self, q: asyncio.Queue[AmbientEvent]) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)
            logger.info(f"EventBus: subscriber left (now {len(self._subscribers)})")

    async def publish(self, event: AmbientEvent) -> int:
        """Fan out to all subscribers. Returns count delivered."""
        delivered = 0
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
                delivered += 1
            except asyncio.QueueFull:
                logger.warning(
                    f"EventBus: subscriber queue full; dropping event "
                    f"({event.kind.value}/{event.source})"
                )
        return delivered

    def publish_nowait(self, event: AmbientEvent) -> int:
        """Sync-context convenience — fire-and-forget publish() on the running loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("EventBus: publish_nowait called without running loop")
            return 0
        asyncio.ensure_future(self.publish(event), loop=loop)
        return len(self._subscribers)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)