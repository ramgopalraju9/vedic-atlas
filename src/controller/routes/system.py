
"""System awareness route — active window info (Windows/laptop profile only).

Donor: veda/routes/system.py, copied verbatim. Degrades gracefully via
`ImportError` on platforms without pywin32 (the Pi profile has no
foreground-window concept anyway).
"""

import asyncio

from fastapi import APIRouter

from schemas.system import SystemInfo

router = APIRouter()


@router.get("/system", response_model=SystemInfo)
async def get_system_info():
    """Get current active window information."""
    return await asyncio.to_thread(_get_active_window)


def _get_active_window() -> SystemInfo:
    """Read active window via Win32 API (runs in thread)."""
    try:
        import win32gui
        import win32process
        import psutil

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return SystemInfo()

        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)

        try:
            proc = psutil.Process(pid)
            return SystemInfo(
                window_title=title[:150],
                process_name=proc.name(),
                exe_name=proc.exe(),
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return SystemInfo(window_title=title[:150])

    except ImportError:
        return SystemInfo(window_title="(pywin32 not available)")
    except Exception:
        return SystemInfo()