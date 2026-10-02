from __future__ import annotations

from fastapi import APIRouter, Depends

from controller.dependencies.providers import get_memory, get_semantic_recall
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


@router.get("/memory/search")
async def memory_search(q: str, k: int = 5, recall=Depends(get_semantic_recall)) -> dict:
    """Meaning-based retrieval over indexed facts and conversation summaries."""
    if recall is None or not recall.enabled:
        return {"query": q, "enabled": False, "hits": []}
    hits = await recall.recall(q, top_k=k)
    return {
        "query": q,
        "enabled": True,
        "hits": [
            {"source": h.source, "ref_id": h.ref_id, "text": h.text, "score": round(h.score, 4)}
            for h in hits
        ],
    }