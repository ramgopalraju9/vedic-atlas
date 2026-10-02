"""SoundPlayer — Windows system sound alerts.

Donor: veda/notifications/sound.py, read in full. Teams-specific sound
lookup (`_get_teams_sound`, `play_teams_sound`) dropped; kept the
general/high-priority sound fallback chain and the sync `winsound` calls
run off the event loop via an executor. Not a `NotificationPort` adapter
itself (that Port models one message surface, "toast + optional sound" as
a single `notify()`); `DesktopNotifier` may compose this in a later batch
if a combined toast+sound experience is wanted. Standalone for now.
"""

from __future__ import annotations

import asyncio
import winsound
from pathlib import Path

from domain.value_objects.urgency import Urgency
from core.logging_config import logger

_NOTIFICATION_SOUNDS = (
    r"C:\Windows\Media\Windows Notify System Generic.wav",
    r"C:\Windows\Media\notify.wav",
    r"C:\Windows\Media\Windows Notify.wav",
)
_HIGH_PRIORITY_SOUNDS = (
    r"C:\Windows\Media\Windows Critical Stop.wav",
    r"C:\Windows\Media\Windows Exclamation.wav",
    r"C:\Windows\Media\chord.wav",
)


def _first_existing(paths: tuple[str, ...]) -> str | None:
    for p in paths:
        if Path(p).exists():
            return p
    return None


class SoundPlayer:
    """Plays a Windows system sound, tiered by urgency."""

    def __init__(self):
        self.enabled = True
        self._notification_sound = _first_existing(_NOTIFICATION_SOUNDS)
        self._high_priority_sound = _first_existing(_HIGH_PRIORITY_SOUNDS)

    async def play(self, urgency: Urgency = Urgency.NORMAL) -> bool:
        if not self.enabled:
            return False

        sound_file = self._high_priority_sound if urgency == Urgency.HIGH else self._notification_sound
        try:
            loop = asyncio.get_running_loop()
            if sound_file:
                await loop.run_in_executor(
                    None, lambda: winsound.PlaySound(sound_file, winsound.SND_FILENAME | winsound.SND_ASYNC)
                )
            else:
                await loop.run_in_executor(None, lambda: winsound.MessageBeep(winsound.MB_ICONINFORMATION))
            return True
        except Exception as e:
            logger.warning(f"Failed to play sound alert: {e}")
            return False

    def enable(self) -> None:
        self.enabled = True

    def disable(self) -> None:
        self.enabled = False