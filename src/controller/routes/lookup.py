

"""Lookup API — surfaces the current-public-fact provider registry.

★ new (REQ-M-09). No donor equivalent — VEDA had no online-fact concept.
Backed by the already-built service/lookup/{registry,lookup_service}.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from controller.dependencies.providers import get_lookup_service
from service.lookup.lookup_service import EgressDeniedError, LookupService

router = APIRouter()


class FactQueryBody(BaseModel):
    category: str
    params: dict = {}


@router.get("/lookup/providers")
async def list_providers(lookup: LookupService = Depends(get_lookup_service)) -> dict:
    """Categories with a registered, allow-listed provider."""
    return {"categories": lookup.registry.list_enabled()}


@router.post("/lookup/fetch")
async def fetch_fact(body: FactQueryBody, lookup: LookupService = Depends(get_lookup_service)) -> dict:
    from domain.entities.fact_query import FactQuery

    query = FactQuery(category=body.category, params=body.params)
    try:
        answer = await lookup.fetch(query)
    except EgressDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "provider_id": answer.provider_id,
        "category": answer.category,
        "text": answer.text,
        "sources": list(answer.sources),
        "fetched_at": answer.fetched_at.isoformat() if answer.fetched_at else None,
    }
