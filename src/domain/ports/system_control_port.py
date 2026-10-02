"""SystemControlPort — OS-level actions dispatched by the system agent.

Donor: veda/system/actions.py, read in full this batch (Batch 3 had only
read the first 80 lines — app alias/launch tables and psutil usage). The
full file confirms a richer action set than the Batch 3 port declared;
extended here to match: `close_app`, `focus_app`, `battery_info`,
`get_volume`, `mute`, `is_muted` were all missing and are genuine donor
capabilities, not invented. `open_teams_chat` (Teams deep-link) is
excluded — Teams is out of scope. Two adapters: WindowsSystemAdapter
(pywinauto/pycaw, ported from this donor file) and LinuxSystemAdapter
(new — the donor has no Linux path; pywinauto/pycaw do not exist on Pi).
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SystemControlPort(Protocol):
    """OS-level actions: launch/close/focus apps, volume, battery, processes."""

    def open_app(self, name: str) -> tuple[bool, str]:
        """Launch an application by its human-friendly name (or focus if already running)."""
        ...

    def close_app(self, name: str) -> tuple[bool, str]:
        ...

    def focus_app(self, name: str) -> tuple[bool, str]:
        ...

    def battery_info(self) -> dict[str, Any]:
        """{"has_battery": bool, "percent": float, "plugged_in": bool, "secs_left": int | None}."""
        ...

    def get_volume(self) -> int | None:
        ...

    def set_volume(self, level: int) -> tuple[bool, str]:
        """Set system output volume, 0-100."""
        ...

    def mute(self, on: bool) -> bool:
        ...

    def is_muted(self) -> bool | None:
        ...

    def list_running(self, name_filter: str | None = None) -> list[str]:
        """List running process names, optionally filtered."""
        ...

    def top_processes(self, limit: int = 5) -> list[dict[str, Any]]:
        """Busiest processes by CPU, most recent sampling."""
        ...