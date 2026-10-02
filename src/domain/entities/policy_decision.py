"""PolicyDecision — the result of evaluating an action against governance policy.

Donor: veda/governance/protocol.py (PolicyDecision dataclass), copied
verbatim into the domain layer — it was previously declared inline with
the GovernanceProvider Protocol; split out so other ports (audit_sink,
egress) can depend on it without importing the whole protocol module.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PolicyDecision:
    """Result of evaluating an action against governance policies."""

    allowed: bool = True
    action: str = "allow"  # allow | deny | audit | block
    rule_name: str | None = None
    reason: str = ""
    audit_entry: dict = field(default_factory=dict)