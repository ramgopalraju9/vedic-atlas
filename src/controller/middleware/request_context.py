"""Request-context middleware — binds a per-request id to the logging ContextVar.

★ new. The donor had no correlation-id concept; every log line just showed
whatever `self.identifier` happened to be set to last (the race condition
fixed in core/logging_config.py — see that file's docstring). This
middleware is what actually calls `set_request_id()` once per request so
concurrent requests' log lines stay attributable.
"""

import uuid

from fastapi import Request

from core.logging_config import set_request_id


async def request_context(request: Request, call_next):
    set_request_id(uuid.uuid4().hex[:8])
    return await call_next(request)