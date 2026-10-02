"""DesktopNotifier — Windows toast notifications via PowerShell.

Donor: veda/notifications/desktop.py, read in full. Teams-specific
`show_teams_notification` dropped (Teams is out of scope); the generic
`show_notification(title, message, source, urgency, play_sound)` is kept
and adapted to the single-method `NotificationPort.notify()` contract
(domain/ports/notification_port.py) — `source` fixed to "Veda", play_sound
always on (the toast balloon's own default sound).
"""

from __future__ import annotations

import asyncio

from domain.value_objects.urgency import Urgency
from core.logging_config import logger


class DesktopNotifier:
    """Windows toast notifications, implements NotificationPort."""

    def __init__(self):
        self.enabled = True

    def notify(self, title: str, message: str, urgency: Urgency = Urgency.NORMAL) -> None:
        if not self.enabled:
            return
        try:
            asyncio.get_running_loop().create_task(self._show(title, message))
        except RuntimeError:
            logger.warning("DesktopNotifier.notify called without a running loop; dropped")

    async def _show(self, title: str, message: str) -> bool:
        try:
            script = self._build_toast_script(title, message)
            process = await asyncio.create_subprocess_exec(
                "powershell.exe", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
                "-Command", script,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
                return process.returncode == 0
            except asyncio.TimeoutError:
                return True  # still running — assume success
        except Exception as e:
            logger.warning(f"Failed to show desktop notification: {e}")
            return False

    @staticmethod
    def _build_toast_script(title: str, message: str) -> str:
        title = title.replace("'", "''")
        message = message.replace("'", "''")
        return f"""
Add-Type -AssemblyName System.Windows.Forms;
$notification = New-Object System.Windows.Forms.NotifyIcon;
$notification.Icon = [System.Drawing.SystemIcons]::Information;
$notification.BalloonTipTitle = 'Veda: {title}';
$notification.BalloonTipText = '{message}';
$notification.BalloonTipIcon = 'Info';
$notification.Visible = $true;
$notification.ShowBalloonTip(5000);
Start-Sleep -Seconds 1;
$notification.Dispose();
"""

    def enable(self) -> None:
        self.enabled = True

    def disable(self) -> None:
        self.enabled = False