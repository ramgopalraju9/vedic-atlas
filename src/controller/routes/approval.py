from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from controller.dependencies.providers import get_approval_broker
from service.approval.approval_broker import ApprovalBroker

router = APIRouter()


class ApprovalRequestBody(BaseModel):
    action_name: str
    arguments: dict = {}
    reason: str = ""


@router.post("/approval/request")
async def request_approval(body: ApprovalRequestBody, broker: ApprovalBroker = Depends(get_approval_broker)):
    """Blocks until resolved (approved, denied, or timed out)."""
    allow, reason = await broker.request_approval(
        action_name=body.action_name,
        arguments=body.arguments,
        reason=body.reason,
    )
    return {"allow": allow, "reason": reason}


@router.post("/approval/{request_id}/approve")
async def approve(request_id: str, broker: ApprovalBroker = Depends(get_approval_broker)):
    if not broker.approve(request_id, source="ui"):
        raise HTTPException(status_code=404, detail="approval not found or already resolved")
    return {"status": "approved"}


@router.post("/approval/{request_id}/deny")
async def deny(request_id: str, broker: ApprovalBroker = Depends(get_approval_broker)):
    if not broker.deny(request_id, source="ui"):
        raise HTTPException(status_code=404, detail="approval not found or already resolved")
    return {"status": "denied"}


@router.get("/approval/pending")
async def pending(broker: ApprovalBroker = Depends(get_approval_broker)):
    now = datetime.now(timezone.utc)
    out = [
        {
            "request_id": p.request_id,
            "action_name": p.action_name,
            "age_sec": round(p.age_sec(now), 1),
            "summary": broker.summarize(p),
        }
        for p in broker.pending()
    ]
    return {"pending": out}


@router.post("/approval/yolo/on")
async def yolo_on(broker: ApprovalBroker = Depends(get_approval_broker)):
    broker.set_yolo(True)
    return {"yolo": True}


@router.post("/approval/yolo/off")
async def yolo_off(broker: ApprovalBroker = Depends(get_approval_broker)):
    broker.set_yolo(False)
    return {"yolo": False}


@router.get("/approval/yolo")
async def yolo_state(broker: ApprovalBroker = Depends(get_approval_broker)):
    return {"yolo": broker.is_yolo()}
