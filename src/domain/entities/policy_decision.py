from dataclasses import dataclass, field

@dataclass(frozen=True)
class PolicyDecision:
    """Result of evaluating an action against governance policies."""

    allowed: bool = True
    action: str = "allow"
    rule_name: str | None = None
    reason: str = ""
    audit_entry: dict = field(default_factory=dict)
