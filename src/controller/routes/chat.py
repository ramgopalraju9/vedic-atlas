"""Chat API route - routes a text message through the Supervisor agent.

Donor: veda/routes/chat.py, read in full. Vision dropped entirely:
`apply_vision_intent`, `image_paths` validation, and the recognized-user
injection from face recognition are all gone (`_vision_bridge.py` /
`uploads.py` are out of scope - see FILE_MAP.md). The `warm_pool` mark
also drops - Ollama's `keep_alive` covers that concept.
"""

from fastapi import APIRouter, Depends

from controller.dependencies.providers import get_supervisor
from domain.entities.agent_context import AgentContext
from schemas.chat import ChatRequest, ChatResponse
from service.agent.supervisor import SupervisorAgent

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, supervisor: SupervisorAgent = Depends(get_supervisor)):
    """Process a text chat message via the Supervisor agent."""
    ctx = AgentContext(
        user_message=req.message,
        system_context=(req.system_context or "")[:2000],
        from_voice=req.from_voice,
    )
    result = await supervisor.execute(ctx)
    return ChatResponse(response=result.response)