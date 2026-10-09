import logging

from core.logging_config import (
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
