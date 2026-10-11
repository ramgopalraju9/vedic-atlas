"""Governance REST API — status and audit statistics (used by the CLI banner and `veda status`).

Donor: veda/routes/governance.py. The provider is constructor-injected
(`Depends(get_governance)`) and may legitimately be `None` if governance is
disabled for this deployment — every endpoint treats that as "disabled", not
an error. The `hasattr` probes are kept: different provider implementations
may not expose all of `rules_count`/`breaker_states`/`audit_backend`.
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
