"""ApprovalRequest — a pending or resolved user approval.

Donor: veda/code/approval/broker.py's PendingApproval dataclass, adapted:
  - `tool_name` -> `action_name`, `tool_input` -> `arguments`: the donor's
    naming came from Claude/Copilot CLI tool-call vocabulary ("Bash",
    "Edit", "Write"). That vocabulary doesn't exist once the cloud CLIs are
    gone — approvals in the target architecture gate SKILL execution
    (terminal, file_ops), so the fields are renamed to match.
  - `future: asyncio.Future` is DROPPED. A domain entity is plain data; the
    async wait-for-resolution mechanics belong to the orchestrator
    (service/approval/approval_broker.py), not the entity itself.
  - `status` is added explicitly (the donor tracked resolution only via
    resolving/rejecting the future) so the state is inspectable without an
    event loop.
"""

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
    status: str = "pending"  # "pending" | "approved" | "denied" | "timed_out"
    resolved_via: str | None = None  # "ui" | "voice" | "timeout" | None

    def age_sec(self, now: datetime) -> float:
        return (now - self.created_at).total_seconds()