"""`veda server` subcommand — runs the FastAPI app via uvicorn.

Donor: veda/cli/server.py, adapted to the new composition root
(`server:app` instead of `veda.app:app`) and config split
(`core.config.load_full_config().app.port` instead of `veda.config.load_config().port`).
"""

from __future__ import annotations

import uvicorn

from core.config import load_full_config
from core.constants import PROJECT_NAME, VERSION
from core.logging_config import logger


def cmd_server(*, port: int | None = None, host: str = "127.0.0.1") -> int:
    cfg = load_full_config()
    use_port = port or cfg.app.port
    logger.info(f"{PROJECT_NAME} v{VERSION} - Personal AI Assistant")
    logger.info(f"Starting on http://{host}:{use_port}")
    uvicorn.run("server:app", host=host, port=use_port, log_level="warning", reload=False, access_log=False)
    return 0