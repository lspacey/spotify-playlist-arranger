"""Tests for idempotent setup_logging() — guards against handler duplication
when main.py module-level code is re-executed (Windows multiprocessing spawn
or same-process re-import)."""

import logging


def _get_pa_logger():
    return logging.getLogger("playlist_arranger")


def _clear_logger():
    """Ensure a clean logger state for a test."""
    logger = _get_pa_logger()
    logger.handlers.clear()


def test_setup_logging_adds_handlers_once():
    """First call should add exactly 2 handlers (console + rotating file)."""
    _clear_logger()
    from playlist_arranger.config import setup_logging
    setup_logging()
    logger = _get_pa_logger()
    assert len(logger.handlers) == 2, (
        f"Expected 2 handlers (console + file), got {len(logger.handlers)}"
    )


def test_setup_logging_is_idempotent_on_repeated_calls():
    """Calling setup_logging() multiple times must NOT accumulate handlers."""
    _clear_logger()
    from playlist_arranger.config import setup_logging
    setup_logging()
    setup_logging()
    setup_logging()
    logger = _get_pa_logger()
    assert len(logger.handlers) == 2, (
        f"Expected exactly 2 handlers after 3 calls, got {len(logger.handlers)}"
    )


def test_handler_types_are_correct():
    """The two handlers must be StreamHandler and RotatingFileHandler."""
    _clear_logger()
    from playlist_arranger.config import setup_logging
    setup_logging()
    logger = _get_pa_logger()
    handler_types = {type(h).__name__ for h in logger.handlers}
    assert "StreamHandler" in handler_types, (
        f"Missing StreamHandler in {handler_types}"
    )
    assert "RotatingFileHandler" in handler_types, (
        f"Missing RotatingFileHandler in {handler_types}"
    )


def test_setup_logging_preserves_level_setting():
    """Ensure the logger level is set to DEBUG after setup."""
    _clear_logger()
    from playlist_arranger.config import setup_logging
    setup_logging()
    logger = _get_pa_logger()
    assert logger.level == logging.DEBUG, (
        f"Expected DEBUG (10), got {logging.getLevelName(logger.level)} ({logger.level})"
    )