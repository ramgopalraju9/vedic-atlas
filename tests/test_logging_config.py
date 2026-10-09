import logging

from core.logging_config import (
    _ReminderAccessLogFilter,
    configure_logging,
    logger,
    reset_voice_turn_id,
    set_voice_turn_id,
)


def test_configure_logging_does_not_duplicate_veda_handlers_or_filters():
    logger = logging.getLogger("veda")
    before_handlers = sum(getattr(handler, "_veda_configured", False) for handler in logger.handlers)
    before_filters = sum(type(item).__name__ == "_RequestIdFilter" for item in logger.filters)

    configure_logging("INFO")
    configure_logging("DEBUG")

    after_handlers = sum(getattr(handler, "_veda_configured", False) for handler in logger.handlers)
    after_filters = sum(type(item).__name__ == "_RequestIdFilter" for item in logger.filters)
    assert after_handlers == max(before_handlers, 1)
    assert after_filters == max(before_filters, 1)
    assert logger.level == logging.DEBUG


def test_voice_turn_context_is_added_to_log_records(caplog):
    token = set_voice_turn_id("T0042")
    try:
        with caplog.at_level(logging.INFO, logger="veda"):
            logger.info("[voice][flow] stage=test")
    finally:
        reset_voice_turn_id(token)

    record = next(record for record in caplog.records if record.message.endswith("stage=test"))
    assert record.voice_turn_id == "T0042"


def test_successful_reminder_poll_access_logs_are_suppressed():
    log_filter = _ReminderAccessLogFilter()

    def record(message: str) -> logging.LogRecord:
        return logging.LogRecord("uvicorn.access", logging.INFO, "", 0, message, (), None)

    poll = record('127.0.0.1:1234 - "GET /api/reminders/recent?after=0 HTTP/1.1" 200 OK')
    failed_poll = record('127.0.0.1:1234 - "GET /api/reminders/recent?after=0 HTTP/1.1" 500 Internal Server Error')
    other_request = record('127.0.0.1:1234 - "GET /api/health HTTP/1.1" 200 OK')

    assert not log_filter.filter(poll)
    assert log_filter.filter(failed_poll)
    assert log_filter.filter(other_request)


def test_configure_logging_attaches_reminder_access_filter_once():
    access_logger = logging.getLogger("uvicorn.access")
    configure_logging()
    configure_logging()

    assert sum(isinstance(item, _ReminderAccessLogFilter) for item in access_logger.filters) == 1
