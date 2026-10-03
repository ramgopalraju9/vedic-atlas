"""Tool-turn trace response schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class TraceCallOut(BaseModel):
    tool: str
    args: dict[str, Any] = {}
    ok: bool
    ms: int = 0
    result: str = ""
    error: str | None = None


class TraceOut(BaseModel):
    id: int
    request_id: str
    created_at: datetime
    agent: str
    user_message: str
    reply: str
    decided: str
    forced: bool
    narrated: bool
    total_ms: int
    calls: list[TraceCallOut]
    prompt_tokens: dict[str, int] = {}
    timings_ms: dict[str, int] = {}
    notes: list[str] = []


class TraceList(BaseModel):
    traces: list[TraceOut]
