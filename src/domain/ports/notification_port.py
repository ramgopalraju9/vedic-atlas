"""NotificationPort — desktop/sound alerts for ambient events.

New protocol shape. Donor had veda/notifications/desktop.py and sound.py
as concrete, Teams-coupled implementations that were not read in full
during this migration (out of scope — Teams is dropped entirely). This
Protocol defines the minimal, generic surface a non-Teams notifier needs;
reconcile against the donor's exact desktop/sound APIs only if a concrete
adapter is ported later; do not assume this matches them 1:1.
"""

from typing import Protocol, runtime_checkable

from domain.value_objects.urgency import Urgency


@runtime_checkable
class NotificationPort(Protocol):
    """Surfaces a message to the user outside the chat/voice channel."""

    def notify(self, title: str, message: str, urgency: Urgency = Urgency.NORMAL) -> None:
        ...