"""Admin routes - shutdown the server from the UI.

Donor: veda/routes/admin.py, copied verbatim.
"""

import os
import signal
import threading

from fastapi import APIRouter

from core.logging_config import logger

router = APIRouter()


def _shutdown_soon(delay_sec: float = 0.4) -> None:
    """Send SIGINT to ourselves after a short delay so the HTTP response
    gets flushed first. uvicorn catches SIGINT and tears everything down
    cleanly (event bus, background tasks, etc.)."""
    def _do():
        try:
            os.kill(os.getpid(), signal.SIGINT)
        except Exception as e:
            logger.error(f"[admin] self-kill failed: {e}; falling back to os._exit")
            os._exit(0)

    threading.Timer(delay_sec, _do).start()


@router.post("/admin/shutdown")
async def shutdown():
    logger.info("[admin] shutdown requested from UI")
    _shutdown_soon()
    return {"status": "shutting down"}
