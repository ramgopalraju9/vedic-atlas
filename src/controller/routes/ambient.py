"""Ambient-event API: SSE stream out, test publish in.

Donor: veda/routes/ambient.py, read in full. The RUNNER_OUTPUT bypass
branch is dropped — that EventKind member doesn't exist in this build
(code-runner is out of scope; see domain/events/event_kind.py).
"""

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from controller.dependencies.providers import get_event_bus, get_supervisor
from controller.sse.stream_adapter import sse_response
from domain.events.ambient_event import AmbientEvent
from domain.events.event_kind import EventKind
from domain.value_objects.urgency import Urgency
from core.logging_config import logger

router = APIRouter()


class PublishRequest(BaseModel):
    kind: str = EventKind.OBSERVATION.value
    description: str
    urgency: str = Urgency.NORMAL.value
    source: str = "manual"
    dedupe_key: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


@router.post("/ambient/publish")
async def publish_ambient(req: PublishRequest, bus=Depends(get_event_bus)):
    """Test hook: inject an ambient event. Real sensors publish directly."""
    try:
        event = AmbientEvent(
            kind=EventKind(req.kind),
            description=req.description,
            urgency=Urgency(req.urgency),
            source=req.source,
            dedupe_key=req.dedupe_key,
            payload=req.payload,
        )
    except ValueError as e:
        return {"status": "error", "detail": str(e)}
    delivered = await bus.publish(event)
    return {"status": "published", "event_id": event.event_id, "delivered_to": delivered}


@router.get("/ambient/stream")
async def ambient_stream(request: Request, bus=Depends(get_event_bus), supervisor=Depends(get_supervisor)):
    """SSE stream: yields narrations for ambient events that pass Supervisor filters."""
    q = bus.subscribe(maxsize=100)

    async def gen():
        try:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue

                narration = await supervisor.dispatch_ambient(event)
                if not narration:
                    continue
                payload = {
                    "id": event.event_id,
                    "text": narration,
                    "kind": event.kind.value,
                    "source": event.source,
                    "urgency": event.urgency.value,
                    # Top-level dedupe_key so the browser can coalesce repeats
                    # of the same observation (retried toast, sensor
                    # re-detection, etc.) instead of queuing duplicate tiles.
                    "dedupe_key": event.dedupe_key,
                    # event-specific data — approval_id etc. travel here.
                    "payload": event.payload,
                }
                yield f"data: {json.dumps(payload)}\n\n"
        except Exception as e:
            logger.warning(f"ambient stream error: {e}")
        finally:
            bus.unsubscribe(q)

    return sse_response(gen())