import asyncio

from fastapi import APIRouter, Depends, Request

from controller.dependencies.providers import get_supervisor
from controller.sse.encoder import sse_encode
from controller.sse.stream_adapter import sse_response, watch_disconnect
from domain.entities.agent_context import AgentContext
from schemas.chat import StreamRequest
from service.agent.supervisor import SupervisorAgent

router = APIRouter()


@router.post("/stream")
async def stream_chat(req: StreamRequest, request: Request, supervisor: SupervisorAgent = Depends(get_supervisor)):
    """Stream the Supervisor's response via Server-Sent Events."""
    ctx = AgentContext(
        user_message=req.message,
        system_context=(req.system_context or "")[:2000],
        from_voice=req.from_voice,
    )

    cancel_event = asyncio.Event()

    async def event_generator():
        watcher = asyncio.create_task(watch_disconnect(request, cancel_event))
        try:
            async for chunk in supervisor.execute_stream(ctx, cancel_event=cancel_event):
                if cancel_event.is_set():
                    break
                if chunk:
                    yield sse_encode(chunk)
            if not cancel_event.is_set():
                yield sse_encode("[DONE]")
        finally:
            cancel_event.set()
            watcher.cancel()
            try:
                await watcher
            except (asyncio.CancelledError, Exception):
                pass

    return sse_response(event_generator())