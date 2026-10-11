"""Lookup API — health probe for the online tool dependencies (`veda doctor`).

★ new (REQ-M-09). Backed by service/lookup/health_service.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from controller.dependencies.providers import get_lookup_health
from service.lookup.health_service import LookupHealthService

router = APIRouter()


@router.get("/lookup/health")
async def lookup_health(health: LookupHealthService = Depends(get_lookup_health)) -> dict:
    """Live probe of every online tool dependency (weather, currency, search, ...)."""
    results = await health.run()
    return {
        "ok": all(r.ok for r in results),
        "providers": [
            {"name": r.name, "ok": r.ok, "configured": r.configured, "latency_ms": r.latency_ms, "detail": r.detail}
            for r in results
        ],
    }
