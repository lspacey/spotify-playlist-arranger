"""Regression test: re-import of main.py's module-level setup_logging() call
must not accumulate duplicate handlers (fixes the double-log-line bug)."""

import logging
import sys
import importlib


def _clear_logger():
    """Ensure a clean logger state for a test."""
    logger = logging.getLogger("playlist_arranger")
    logger.handlers.clear()


def test_reimporting_main_does_not_duplicate_handlers():
    """Re-executing setup_logging() via module-level code must be idempotent."""
    _clear_logger()

    # Simulate what happens when main.py is imported once
    from playlist_arranger.config import setup_logging
    setup_logging()
    first_count = len(logging.getLogger("playlist_arranger").handlers)

    # Simulate re-import/re-execution (e.g. Windows spawn subprocess
    # re-importing __main__, or same-process worker restart)
    setup_logging()
    second_count = len(logging.getLogger("playlist_arranger").handlers)

    assert first_count == second_count, (
        f"Handler count grew from {first_count} to {second_count} — "
        "setup_logging() must be idempotent"
    )
    assert second_count == 2, (
        f"Expected exactly 2 handlers, got {second_count}"
    )


def test_three_reimports_still_only_two_handlers():
    """Edge case: many re-imports still produce only 2 handlers."""
    _clear_logger()

    from playlist_arranger.config import setup_logging
    for _ in range(5):
        setup_logging()

    logger = logging.getLogger("playlist_arranger")
    assert len(logger.handlers) == 2, (
        f"After 5 calls, expected 2 handlers, got {len(logger.handlers)}"
    )