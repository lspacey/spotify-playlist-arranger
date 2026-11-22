"""Tests for idempotent setup_logging() — guards against handler duplication
when main.py module-level code is re-executed (Windows multiprocessing spawn
or same-process re-import)."""

import logging
from logging.handlers import RotatingFileHandler


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
    """The two handlers must be StreamHandler and RetryRotatingFileHandler."""
    _clear_logger()
    from playlist_arranger.config import setup_logging
    setup_logging()
    logger = _get_pa_logger()
    handler_types = {type(h).__name__ for h in logger.handlers}
    assert "StreamHandler" in handler_types, (
        f"Missing StreamHandler in {handler_types}"
    )
    assert "RetryRotatingFileHandler" in handler_types, (
        f"Missing RetryRotatingFileHandler in {handler_types}"
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


def test_file_handler_has_delay_true():
    """Rotating file handler must use delay=True to defer file open."""
    _clear_logger()
    from playlist_arranger.config import setup_logging
    setup_logging()
    from playlist_arranger.config import RetryRotatingFileHandler
    logger = _get_pa_logger()
    file_handlers = [
        h for h in logger.handlers
        if isinstance(h, RetryRotatingFileHandler)
    ]
    assert len(file_handlers) == 1, (
        f"Expected 1 RetryRotatingFileHandler, got {len(file_handlers)}"
    )
    assert file_handlers[0].delay is True, (
        f"Expected delay=True on RotatingFileHandler, got delay={file_handlers[0].delay}"
    )


def test_retry_handler_accepts_max_attempts(tmp_path):
    """RetryRotatingFileHandler must accept and store max_attempts parameter."""
    from playlist_arranger.config import RetryRotatingFileHandler
    p = tmp_path / "test.log"
    h = RetryRotatingFileHandler(str(p), max_attempts=5, retry_base_s=0.01, delay=True)
    try:
        assert h._max_attempts == 5
        assert h._retry_base_s == 0.01
    finally:
        h.close()


def test_retry_handler_do_rollover_retries_and_skips(tmp_path):
    """doRollover retries on PermissionError, skips after max_attempts exhausted."""
    from playlist_arranger.config import RetryRotatingFileHandler
    from unittest.mock import patch, MagicMock

    p = tmp_path / "test.log"
    # Create min log file so doRollover has something to rotate
    p.write_text("some content")

    h = RetryRotatingFileHandler(str(p), max_attempts=3, retry_base_s=0.001, delay=True)
    h.stream = MagicMock()  # prevent real stream access
    try:
        with patch.object(
            RotatingFileHandler, "doRollover",
            side_effect=PermissionError("WinError 32"),
        ) as mock_super:
            h.doRollover()
            # Should have tried 3 times
            assert mock_super.call_count == 3, (
                f"Expected 3 retry attempts, got {mock_super.call_count}"
            )
    finally:
        h.close()


def test_retry_handler_succeeds_on_second_attempt(tmp_path):
    """doRollover succeeds on second attempt after one PermissionError."""
    from playlist_arranger.config import RetryRotatingFileHandler
    from unittest.mock import patch, MagicMock

    p = tmp_path / "test.log"
    p.write_text("some content")

    h = RetryRotatingFileHandler(str(p), max_attempts=3, retry_base_s=0.001, delay=True)
    h.stream = MagicMock()
    try:
        with patch.object(
            RotatingFileHandler, "doRollover",
            side_effect=[PermissionError("fail1"), None],
        ) as mock_super:
            h.doRollover()
            # Should succeed on second attempt
            assert mock_super.call_count == 2, (
                f"Expected 2 calls (1 fail + 1 success), got {mock_super.call_count}"
            )
    finally:
        h.close()
