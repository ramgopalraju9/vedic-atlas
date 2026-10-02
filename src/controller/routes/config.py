"""
Donor: veda/routes/config.py, copied verbatim (proactivity is the only
knob wired so far).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from controller.dependencies.providers import get_supervisor
from service.agent.supervisor import SupervisorAgent

router = APIRouter()

_VALID_PROACTIVITY = ("conservative", "medium", "chatty")


class ProactivityBody(BaseModel):
    level: str = Field(..., description="conservative | medium | chatty")


@router.get("/config/proactivity")
async def get_proactivity(supervisor: SupervisorAgent = Depends(get_supervisor)):
    return {"level": supervisor.proactivity, "valid": list(_VALID_PROACTIVITY)}


@router.post("/config/proactivity")
async def set_proactivity(body: ProactivityBody, supervisor: SupervisorAgent = Depends(get_supervisor)):
    try:
        level = supervisor.set_proactivity(body.level)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"level": level, "valid": list(_VALID_PROACTIVITY)}