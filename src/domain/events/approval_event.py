"""ApprovalEvent — an approval request's lifecycle transition.

New. Published when an ApprovalRequest is created, approved, denied, or
times out, so the UI/voice layer and the governance audit sink can both
subscribe without polling the approval broker's internal state.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class ApprovalEvent:
    """One lifecycle transition of an ApprovalRequest."""

    request_id: str
    status: str  # "pending" | "approved" | "denied" | "timed_out"
    resolved_via: str | None
    occurred_at: datetime