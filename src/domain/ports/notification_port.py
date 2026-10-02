from typing import Protocol, runtime_checkable
from domain.value_objects.urgency import Urgency

@runtime_checkable
class NotificationPort(Protocol):
    """Surfaces a message to the user outside the chat/voice channel."""

    def notify(self, title: str, message: str, urgency: Urgency = Urgency.NORMAL) -> None:
        ...
