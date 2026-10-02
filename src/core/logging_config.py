"""Structured logging with a request-scoped identifier.

Donor: veda/log/__init__.py.

Fix applied (identified during migration analysis): the donor stored the
request identifier as a mutable attribute (`self.identifier`) on the single
process-wide logger instance. Under asyncio, concurrent requests race on
that attribute — request A can log with request B's identifier. Replaced
with a contextvars.ContextVar, which is the correct async-safe primitive:
each task gets its own value, no locking needed.

Also wires `log_level` from config instead of hardcoding DEBUG (previously
declared in VedaConfig.log_level but never read).
"""

import logging
from contextvars import ContextVar

_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def set_request_id(identifier: str) -> None:
    """Bind a request identifier to the current async task / thread context."""
    _request_id.set(identifier)


class _RequestIdFilter(logging.Filter):
    """Injects the current context's request id into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.identifier = _request_id.get()
        return True


def configure_logging(level: str = "INFO") -> logging.Logger:
    """Build and return the app logger. Call once at process startup."""
    logger = logging.getLogger("veda")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.addFilter(_RequestIdFilter())

    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "[%(asctime)s][%(levelname)-7s][%(identifier)s][%(funcName)s]"
        "[%(module)s:%(filename)s].(%(lineno)d)] : %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


# Default instance for modules that just need `from core.logging_config import logger`.
# server.py should call configure_logging(cfg.log_level) once at startup to
# apply the configured level; this default keeps imports working before that.
logger = configure_logging()