"""EgressTarget — a candidate destination for an outbound network call.

New — the input to domain/policies/egress_policy.py's pure allow/deny
decision. `category` ties the target back to why the call is being made
(e.g. "weather", "search") so the decision function can enforce
"this category may only talk to its own declared hosts", not just
"this host is on the allow-list" in the abstract.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class EgressTarget:
    """A destination host being evaluated for an outbound call."""

    host: str
    category: str
    reason: str = ""