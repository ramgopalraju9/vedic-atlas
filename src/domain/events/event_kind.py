"""EventKind — the discriminator on AmbientEvent.

Donor: veda/bus/events.py (EventKind enum), with RUNNER_OUTPUT dropped —
that member existed only for the code-generation agent's raw CLI stdout,
which is out of scope for this build.
"""

from enum import Enum


class EventKind(str, Enum):
    OBSERVATION = "observation"            # Ambient sensing noticed something
    NOTIFICATION = "notification"          # Inbound message, system alert
    HEARTBEAT = "heartbeat"                # Routine tick, usually suppressed
    REMINDER = "reminder"                  # User-scheduled reminder
    SYSTEM = "system"                      # OS/device state change
    APPROVAL_REQUEST = "approval_request"  # A skill needs user approval

    def __str__(self) -> str:
        return self.value