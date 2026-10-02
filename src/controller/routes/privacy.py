from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()

# Sources that any in-process code could have triggered. A real hardware
# switch cuts the mic line; these can't make that claim.
_SOFTWARE_SOURCES = {"software", "keyboard_fallback", "api", "shutdown"}


class MuteBody(BaseModel):
    muted: bool


def get_capture_gate(request: Request):
    gate = getattr(request.app.state, "capture_gate", None)
    if gate is None:
        raise HTTPException(status_code=503, detail="capture gate not initialized")
    return gate


def _snapshot(request: Request, gate) -> dict:
    state = gate.state
    allow_list = getattr(request.app.state, "egress_allow_list", frozenSet())
    return {
        "muted": state.muted,
        "source": state.mute_source,
        "hardware_switch": state.mute_source.split(":")[0] not in _SOFTWARE_SOURCES,
        "since": state.since.isoformat(),
        "egress_allow_list": sorted(allow_list),
    }


@router.get("/privacy/status")
async def privacy_status(request: Request, gate=Depends(get_capture_gate)) -> dict:
    return _snapshot(request, gate)


@router.post("/privacy/mute")
async def set_mute(body: MuteBody, request: Request, gate=Depends(get_capture_gate)) -> dict:
    gate.set_muted(body.muted, source="api")
    return _snapshot(request, gate)


@router.post("/privacy/mute/toggle")
async def toggle_mute(request: Request, gate=Depends(get_capture_gate)) -> dict:
    gate.set_muted(not gate.is_muted(), source="api")
    return _snapshot(request, gate)