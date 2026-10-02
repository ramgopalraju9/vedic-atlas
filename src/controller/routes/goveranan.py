

"""Governance REST API — dashboard data endpoints.

Donor: veda/routes/governance.py, read in full. The donor read a
module-level `get_provider()` singleton; here the provider is
constructor-injected (`Depends(get_governance)`) and may legitimately be
`None` if governance is disabled for this deployment — every endpoint
below treats that as "disabled", not an error. The `hasattr` probes for
`rules_count`/`breaker_states`/`audit_backend`/`_rules` are kept as-is:
the concrete governance provider (`tpa/governance/`) lands in a later
batch, and different provider implementations may not expose all of them.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from controller.dependencies.providers import get_governance
from domain.ports.governance_port import GovernanceProvider

router = APIRouter(prefix="/governance", tags=["governance"])


@router.get("/status")
async def governance_status(provider: GovernanceProvider | None = Depends(get_governance)) -> dict:
    """Current governance provider status."""
    if provider is None:
        return {"enabled": False, "provider": "null"}
    result: dict = {"enabled": provider.name != "null", "provider": provider.name}
    if hasattr(provider, "rules_count"):
        result["rules_count"] = provider.rules_count
    if hasattr(provider, "breaker_states"):
        result["breaker_states"] = provider.breaker_states
    return result


@router.get("/audit/stats")
async def audit_stats(provider: GovernanceProvider | None = Depends(get_governance)) -> dict:
    """Audit trail summary statistics."""
    if provider is not None and hasattr(provider, "audit_backend"):
        backend = provider.audit_backend
        if hasattr(backend, "stats"):
            stats = backend.stats()
            total = stats.get("total", 0)
            denied = stats.get("denied", 0)
            return {**stats, "violation_rate": round(denied / total * 100, 2) if total > 0 else 0.0}
    return {"total": 0, "allowed": 0, "denied": 0, "violation_rate": 0.0}


@router.get("/audit/entries")
async def audit_entries(
    event_type: str | None = None,
    agent: str | None = None,
    limit: int = 50,
    provider: GovernanceProvider | None = Depends(get_governance),
) -> list[dict]:
    """Recent audit trail entries with optional filters."""
    if provider is not None and hasattr(provider, "audit_backend"):
        backend = provider.audit_backend
        if hasattr(backend, "query"):
            return backend.query(event_type=event_type, agent=agent, limit=limit)
    return []


@router.get("/audit/verify")
async def verify_chain(provider: GovernanceProvider | None = Depends(get_governance)) -> dict:
    """Verify the hash chain integrity of the audit trail."""
    if provider is not None and hasattr(provider, "audit_backend"):
        backend = provider.audit_backend
        if hasattr(backend, "verify_chain"):
            return backend.verify_chain()
    return {"valid": True, "checked": 0, "broken_at": None}


@router.get("/health")
async def backend_health(provider: GovernanceProvider | None = Depends(get_governance)) -> dict:
    """Circuit breaker health for all inference backends."""
    if provider is None:
        return {}
    backends = ["ollama", "llama_cpp"]
    result = {}
    for b in backends:
        healthy = provider.is_healthy(b)
        result[b] = {"healthy": healthy, "status": "healthy" if healthy else "tripped"}
    if hasattr(provider, "breaker_states"):
        for name, state in provider.breaker_states.items():
            if name in result:
                result[name].update(state)
    return result


@router.get("/policies")
async def list_policies(provider: GovernanceProvider | None = Depends(get_governance)) -> dict:
    """List loaded policy info."""
    if provider is None:
        return {"provider": "null", "rules": []}
    result: dict = {"provider": provider.name, "rules": []}
    if hasattr(provider, "_rules"):
        result["rules"] = [
            {
                "name": r.name,
                "field": r.condition_field,
                "operator": r.operator,
                "value": str(r.value),
                "action": r.action,
                "priority": r.priority,
            }
            for r in provider._rules
        ]
    if hasattr(provider, "rules_count"):
        result["rules_count"] = provider.rules_count
    return result

