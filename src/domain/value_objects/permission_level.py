"""PermissionLevel — how much user oversight a skill's execution requires.

Donor: veda/core/enums.py (PermissionLevel enum), copied verbatim.
"""

from enum import Enum


class PermissionLevel(str, Enum):
    AUTO = "auto"        # Execute immediately, no user interaction
    NOTIFY = "notify"    # Execute and inform the user afterwards
    APPROVE = "approve"  # Wait for explicit user approval before executing

    def __str__(self) -> str:
        return self.value