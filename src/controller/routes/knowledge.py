"""Knowledge base API — save and retrieve facts about the user.

Donor: veda/routes/knowledge.py, read in full. The donor's module-level
`_kb` singleton (`get_kb()`) is dropped per migration rule 4 (no
module-level singletons) — the KnowledgeBase instance is constructed once
in server.py and reached here via `Depends(get_knowledge_base)`.
"""

from fastapi import APIRouter, Depends

from controller.dependencies.providers import get_knowledge_base
from schemas.knowledge import FactList, FactRequest
from service.memory.knowledge_base import KnowledgeBase

router = APIRouter()


@router.get("/knowledge", response_model=FactList)
async def list_facts(kb: KnowledgeBase = Depends(get_knowledge_base)):
    """List all stored facts."""
    return FactList(facts=kb.list_facts())


@router.post("/knowledge")
async def add_fact(req: FactRequest, kb: KnowledgeBase = Depends(get_knowledge_base)):
    """Store a new fact about the user."""
    kb.add_fact(req.fact)
    return {"status": "saved", "fact": req.fact}


@router.delete("/knowledge/{index}")
async def remove_fact(index: int, kb: KnowledgeBase = Depends(get_knowledge_base)):
    """Remove a fact by index."""
    if kb.remove_fact(index):
        return {"status": "removed"}
    return {"status": "not_found"}