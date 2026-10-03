"""Request/response models for the speaker-enrollment API."""

from __future__ import annotations

from pydantic import BaseModel


class SpeakerEnrollStart(BaseModel):
    name: str


class SpeakerEnrollProgress(BaseModel):
    percentage: float
    feedback: str


class SpeakerEnrollResult(BaseModel):
    enrolled: str


class SpeakerList(BaseModel):
    speakers: list[str]
