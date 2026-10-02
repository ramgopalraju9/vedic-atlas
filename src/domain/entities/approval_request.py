from dataclasses import dataclass
from datetime import datetime
from typing import Any

@dataclass
class ApprovalRequest:
    """A request to run a gated skill, awaiting (or having received) a decision."""

    request_id: str
    action_name: str
    arguments: dict[str, Any]
    reason: str
    created_at: datetime
    timeout_sec: int = 60
    status: str = "pending"
    # pending | approved | denied | timed_out
    result: str | None = None

    def age_sec(self, now: datetime) -> float:
        return (now - self.created_at).total_seconds()
