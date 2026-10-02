"""WindowsSystemAdapter – implements SystemControlPort via psutil/pycaw/win32.

Donor: veda/system/actions.py, read in full and ported closely:
  - `open_teams_chat` DROPPED – Teams-specific deep-link, out of scope.
  - App alias table trimmed of `"teams"`/`"microsoft teams"` entries for
    the same reason; every other alias kept.
  - `pywinauto`/`pycaw`/`win32*` imports are lazy (inside methods, not at
    module top) so this file can be imported on non-Windows dev machines
    without those libraries installed – only actually calling a method
    requires them, matching the pattern already used for llama_cpp/fastembed.
"""

from __future__ import annotations

import subprocess
from typing import Any

import psutil

_APP_ALIASES: dict[str, str] = {
    "vs code": "Code.exe", "vscode": "Code.exe", "code": "Code.exe",
    "visual studio code": "Code.exe", "visual studio": "devenv.exe",
    "cursor": "Cursor.exe",
    "pycharm": "pycharm64.exe",
    "intellij": "idea64.exe", "intellij idea": "idea64.exe",
    "webstorm": "webstorm64.exe",
    "chrome": "chrome.exe", "google chrome": "chrome.exe",
    "edge": "msedge.exe", "microsoft edge": "msedge.exe",
    "firefox": "firefox.exe", "mozilla firefox": "firefox.exe",
    "slack": "slack.exe",
    "outlook": "outlook.exe",
    "spotify": "Spotify.exe",
    "notion": "Notion.exe",
    "file explorer": "explorer.exe", "explorer": "explorer.exe", "files": "explorer.exe",
    "terminal": "WindowsTerminal.exe", "windows terminal": "WindowsTerminal.exe",
    "command prompt": "cmd.exe", "cmd": "cmd.exe",
    "powershell": "pwsh.exe",
    "zoom": "Zoom.exe",
    "notepad": "notepad.exe",
    "calculator": "Calculator.exe", "calc": "Calculator.exe",
    "paint": "mspaint.exe",
}

_APP_LAUNCH: dict[str, str] = {
    "Code.exe": "code", "Cursor.exe": "cursor", "pycharm64.exe": "pycharm",
    "idea64.exe": "idea", "webstorm64.exe": "webstorm",
    "msedge.exe": "msedge", "firefox.exe": "firefox", "chrome.exe": "chrome",
    "slack.exe": "slack",
    "outlook.exe": "outlook", "Spotify.exe": "spotify", "Notion.exe": "notion",
    "explorer.exe": "explorer", "WindowsTerminal.exe": "wt", "cmd.exe": "cmd",
    "pwsh.exe": "pwsh", "Zoom.exe": "zoom", "notepad.exe": "notepad",
    "Calculator.exe": "calc", "mspaint.exe": "mspaint",
}


class WindowsSystemAdapter:
    """Implements SystemControlPort using psutil + pycaw + pywin32."""

    def _resolve_app(self, name: str) -> str | None:
        if not name:
            return None
        key = name.strip().lower().strip("'\"")
        if key in _APP_ALIASES:
            return _APP_ALIASES[key]
        if key.endswith(".exe"):
            return key
        return None

    def _find_running(self, exe_name: str) -> list[psutil.Process]:
        out: list[psutil.Process] = []
        for p in psutil.process_iter(["name"]):
            try:
                if (p.info.get("name") or "").lower() == exe_name.lower():
                    out.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return out

    def _focus_pid(self, pid: int) -> bool:
        try:
            import win32con, win32gui, win32process
        except ImportError:
            return False
        hwnds: list[int] = []

        def _enum(hwnd, results):
            try:
                _, p_id = win32process.GetWindowThreadProcessId(hwnd)
                if p_id == pid and win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
                    results.append(hwnd)
            except Exception:
                pass

        try:
            win32gui.EnumWindows(_enum, hwnds)
            if not hwnds:
                return False
            win32gui.ShowWindow(hwnds[0], win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnds[0])
            return True
        except Exception:
            return False

    def open_app(self, name: str) -> tuple[bool, str]:
        exe = self._resolve_app(name)
        if not exe:
            try:
                subprocess.Popen(["cmd", "/c", "start", "", name], shell=False)
                return True, f"Tried to open {name}. Let me know if it didn't show up."
            except Exception as e:
                return False, f"I don't know an app called {name!r} ({e})."

        running = self._find_running(exe)
        if running:
            if self._focus_pid(running[0].pid):
                return True, f"{name} is already open – brought it to the front."
            return True, f"{name} is already running."

        cmd = _APP_LAUNCH.get(exe, exe.rsplit(".", 1)[0])
        try:
            subprocess.Popen(["cmd", "/c", "start", "", cmd], shell=False)
            return True, f"Opening {name}."
        except Exception as e:
            return False, f"Couldn't open {name}: {e}"

    def close_app(self, name: str) -> tuple[bool, str]:
        exe = self._resolve_app(name)
        if not exe:
            return False, f"I don't know an app called {name!r}."
        procs = self._find_running(exe)
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
        exe = self._resolve_app(name)
        if not exe:
            return False, f"I don't know an app called {name!r}."
        procs = self._find_running(exe)
        if not procs:
            return False, f"{name} isn't running."
        if self._focus_pid(procs[0].pid):
            return True, f"Switched to {name}."
        return False, f"{name} is running, but I couldn't bring it to the front."

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
            "secs_left": None if b.secsleft == psutil.POWER_TIME_UNLIMITED else b.secsleft,
        }

    def _endpoint_volume(self):
        from pycaw.pycaw import AudioUtilities

        return AudioUtilities.GetSpeakers().EndpointVolume

    def get_volume(self) -> int | None:
        try:
            return int(round(self._endpoint_volume().GetMasterVolumeLevelScalar() * 100))
        except Exception:
            return None

    def set_volume(self, level: int) -> tuple[bool, str]:
        try:
            self._endpoint_volume().SetMasterVolumeLevelScalar(max(0, min(100, int(level))) / 100.0, None)
            return True, f"Volume set to {level}%."
        except Exception as e:
            return False, f"Couldn't set volume: {e}"

    def mute(self, on: bool) -> bool:
        try:
            self._endpoint_volume().SetMute(1 if on else 0, None)
            return True
        except Exception:
            return False

    def is_muted(self) -> bool | None:
        try:
            return bool(self._endpoint_volume().GetMute())
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
                if not name or name in ("System Idle Process", "Idle"):
                    continue
                pct = p.cpu_percent(None) or 0.0
                procs.append({"name": name, "pid": p.info["pid"], "cpu": round(pct, 1)})
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        procs.sort(key=lambda x: x["cpu"], reverse=True)
        return procs[:limit]
