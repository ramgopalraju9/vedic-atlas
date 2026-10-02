"""Health API — reports which core services are wired and reachable.

★ new. Deliberately duck-typed against `app.state` (unlike every other
route in this batch, health must not 503 just because one dependency
isn't ready — that's the exact condition it exists to report).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import text

from core.logging_config import logger

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict:
    state = request.app.state
    supervisor = getattr(state, "supervisor", None)
    event_bus = getattr(state, "event_bus", None)
    governance = getattr(state, "governance", None)

    checks: dict[str, bool] = {
        "supervisor": supervisor is not None,
        "event_bus": event_bus is not None,
    }

    db_ok = False
    session_factory = getattr(state, "db_session_factory", None)
    if session_factory is None:
        # A missing factory is a wiring fault, not a DB outage — distinguish
        # the two, since silently reporting False hid exactly this for a while.
        logger.error("[health] db_session_factory not set on app.state — DB check cannot run")
    else:
        try:
            with session_factory() as session:
                session.execute(text("SELECT 1"))
            db_ok = True
        except Exception as e:
            logger.warning(f"[health] database probe failed: {e}")
    checks["database"] = db_ok

    return {
        "status": "ok" if all(checks.values()) else "degraded",
        "checks": checks,
        "governance_enabled": governance is not None,
    }