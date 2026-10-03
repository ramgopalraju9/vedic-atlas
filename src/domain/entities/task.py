"""Task — a single user to-do item.

New (Feature D, Productivity). Pure domain entity: no persistence or I/O.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Task:
    """A user task/reminder."""

    id: int | None
    title: str
    done: bool = False
    notes: str = ""
    due_at: datetime | None = None
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None