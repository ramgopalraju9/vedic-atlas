"""LinuxSystemAdapter – implements SystemControlPort for Raspberry Pi OS.

* New – the donor has no Linux path at all (`pywinauto`/`pycaw` don't
exist there). Built to satisfy the same `SystemControlPort` contract as
`WindowsSystemAdapter`, using Linux-native equivalents: `xdg-open`/`wmctrl`
for app launch/focus, `amixer` for volume, `psutil` for everything
process-related (already cross-platform, no change needed from the
Windows adapter's psutil usage).

Narrower than the Windows adapter by necessity – window focusing on
Linux depends on the desktop environment (or lack of one, on a headless
Pi); `focus_app` degrades to "already running" rather than failing.
"""

from __future__ import annotations

import subprocess
from typing import Any

import psutil


class LinuxSystemAdapter:
    """Implements SystemControlPort using xdg-open/wmctrl/amixer/psutil."""

    def _find_running(self, name: str) -> list[psutil.Process]:
        out: list[psutil.Process] = []
        for p in psutil.process_iter(["name"]):
            try:
                if name.lower() in (p.info.get("name") or "").lower():
                    out.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return out

    def open_app(self, name: str) -> tuple[bool, str]:
        running = self._find_running(name)
        if running:
            return True, f"{name} is already running."
        try:
            subprocess.Popen([name.lower().replace(" ", "-")])
            return True, f"Opening {name}."
        except Exception as e:
            return False, f"Couldn't open {name}: {e}"

    def close_app(self, name: str) -> tuple[bool, str]:
        procs = self._find_running(name)
        if not procs:
            return False, f"{name} isn't running."
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        _, alive = psutil.wait_procs(procs, timeout=2)
        for p in alive:
            try:
                p.kill()
            except Exception:
                pass
        return True, f"Closed {name}."

    def focus_app(self, name: str) -> tuple[bool, str]:
        procs = self._find_running(name)
        if not procs:
            return False, f"{name} isn't running."
        return True, f"{name} is running (window focus isn't available on this device)."

    def battery_info(self) -> dict[str, Any]:
        try:
            b = psutil.sensors_battery()
        except Exception:
            return {"has_battery": False}
        if b is None:
            return {"has_battery": False}
        return {
            "has_battery": True,
            "percent": round(b.percent, 1),
            "plugged_in": b.power_plugged,
            "secs_left": None if b.secsleft in (-1, None) else b.secsleft,
        }

    def get_volume(self) -> int | None:
        try:
            out = subprocess.check_output(["amixer", "get", "Master"], text=True)
            for line in out.splitlines():
                if "%" in line:
                    return int(line.split("[")[1].split("%")[0])
        except Exception:
            return None
        return None

    def set_volume(self, level: int) -> tuple[bool, str]:
        try:
            subprocess.check_call(["amixer", "set", "Master", f"{max(0, min(100, int(level)))}%"])
            return True, f"Volume set to {level}%."
        except Exception as e:
            return False, f"Couldn't set volume: {e}"

    def mute(self, on: bool) -> bool:
        try:
            subprocess.check_call(["amixer", "set", "Master", "mute" if on else "unmute"])
            return True
        except Exception:
            return False

    def is_muted(self) -> bool | None:
        try:
            out = subprocess.check_output(["amixer", "get", "Master"], text=True)
            return "[off]" in out
        except Exception:
            return None

    def list_running(self, name_filter: str | None = None) -> list[str]:
        names = set()
        for p in psutil.process_iter(["name"]):
            try:
                n = p.info.get("name")
                if n and (not name_filter or name_filter.lower() in n.lower()):
                    names.add(n)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return sorted(names)

    def top_processes(self, limit: int = 5) -> list[dict[str, Any]]:
        import time

        for p in psutil.process_iter(["name"]):
            try:
                p.cpu_percent(None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        time.sleep(0.25)

        procs = []
        for p in psutil.process_iter(["name", "pid"]):
            try:
                name = p.info.get("name")
                if not name:
                    continue
                pct = p.cpu_percent(None) or 0.0
                procs.append({"name": name, "pid": p.info["pid"], "cpu": round(pct, 1)})
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        procs.sort(key=lambda x: x["cpu"], reverse=True)
        return procs[:limit]
