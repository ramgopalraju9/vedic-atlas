"""Task request/response schemas (Feature D)."""

from datetime import datetime

from pydantic import BaseModel


class TaskCreate(BaseModel):
    title: str
    notes: str = ""
    due_at: datetime | None = None


class TaskOut(BaseModel):
    id: int
    title: str
    done: bool
    notes: str = ""
    due_at: datetime | None = None
    created_at: datetime | None = None


class TaskList(BaseModel):
    tasks: list[TaskOut]