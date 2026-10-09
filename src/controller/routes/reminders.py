"""Reminders API — status, settings, and the text of spoken reminders (so a terminal can print them)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from controller.dependencies.providers import get_reminders
from service.reminders.reminder_service import ReminderService

router = APIRouter()


class ReminderConfigBody(BaseModel):
    enabled: bool | None = None
    lead_minutes: list[int] | None = None
    at_start: bool | None = None
    skip_all_day: bool | None = None


@router.get("/reminders/status")
async def reminders_status(service: ReminderService = Depends(get_reminders)) -> dict:
    return service.status()


@router.get("/reminders/config")
async def get_reminders_config(service: ReminderService = Depends(get_reminders)) -> dict:
    return service.config()


@router.put("/reminders/config")
async def set_reminders_config(body: ReminderConfigBody, service: ReminderService = Depends(get_reminders)) -> dict:
    try:
        return service.update(**body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/reminders/recent")
async def reminders_recent(after: int | None = None, service: ReminderService = Depends(get_reminders)) -> dict:
    return service.recent(after)
