

"""Memory API — read-only surface over the cross-agent memory mesh.

★ new. Backed by the already-built domain/ports/memory_repository_port.py
and service/memory/cross_agent_context.py — a thin read endpoint for the
UI's debug/inspector views, nothing more.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from controller.dependencies.providers import get_memory
from domain.ports.memory_repository_port import MemoryRepositoryPort
from service.memory.cross_agent_context import CrossAgentContext

router = APIRouter()


@router.get("/memory/recent")
async def recent_activity(
    exclude_agent: str = "",
    limit: int = 20,
    memory: MemoryRepositoryPort = Depends(get_memory),
) -> dict:
    """Recent cross-agent memory records, optionally excluding one agent."""
    ctx = CrossAgentContext(memory)
    records = ctx.recent_activity(exclude_agent=exclude_agent, limit=limit)
    return {
        "records": [
            {
                "agent_name": r.agent_name,
                "action": r.action,
                "context": r.context,
                "user_message": r.user_message,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ]
    }
