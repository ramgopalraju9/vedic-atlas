"""PermissionPolicy — pure decision of what a permission level requires.

Donor: veda/guardrails/permissions.py's PermissionManager, split per rule 1
(never move a ring-mixing file whole). The donor mixed the pure decision
("AUTO proceeds, APPROVE waits") with async orchestration (asyncio.Event,
a pending-request dict, a 60s timeout wait, VedaException on denial). Only
the decision is domain logic; the orchestration is staged at
service/guardrails/permissions.py in a later batch.
"""

from domain.value_objects.permission_level import PermissionLevel


def requires_approval(level: PermissionLevel) -> bool:
    """True if execution must wait for an explicit user decision."""
    return level == PermissionLevel.APPROVE


def requires_notification(level: PermissionLevel) -> bool:
    """True if the user should be informed after the fact (but not blocked)."""
    return level == PermissionLevel.NOTIFY


def proceeds_immediately(level: PermissionLevel) -> bool:
    """True if execution needs no gating at all."""
    return level == PermissionLevel.AUTO