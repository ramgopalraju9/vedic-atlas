"""SSE plumbing shared by every streaming route.

★ new — extracted from what were previously two independent, near-identical
StreamingResponse setups in veda/routes/stream.py and veda/routes/ambient.py
(same headers dict, same media type, both read in full this batch).
"""

import asyncio
from typing import AsyncIterator

from fastapi import Request
from fastapi.responses import StreamingResponse

from core.logging_config import logger

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

_DISCONNECT_POLL_SEC = 0.25


def sse_response(generator: AsyncIterator[str]) -> StreamingResponse:
    """Wrap an async generator of SSE-framed strings in a StreamingResponse."""
    return StreamingResponse(generator, media_type="text/event-stream", headers=SSE_HEADERS)


async def watch_disconnect(request: Request, cancel_event: asyncio.Event) -> None:
    """Background task: set cancel_event as soon as the client goes away.

    Donor: veda/routes/stream.py's inline `watch_disconnect`, unchanged.
    Runs alongside a concurrent generator (e.g. supervisor.execute_stream)
    so a dropped connection cancels in-flight inference immediately instead
    of waiting for the next chunk to fail to send.
    """
    try:
        while not cancel_event.is_set():
            if await request.is_disconnected():
                logger.info("client disconnected; cancelling stream")
                cancel_event.set()
                return
            await asyncio.sleep(_DISCONNECT_POLL_SEC)
    except Exception as e:
        logger.warning(f"disconnect watcher error: {e}")