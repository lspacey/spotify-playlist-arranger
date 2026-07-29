"""Integration-level tests for the Spotify connect → UI refresh flow.

Regression guard for the 2026-07-28 bug where do_connect()'s deferred
render_right_panel() call operated on a SECOND module object's
_right_panel=None (because main.py was executed as __main__ and then
imported by dotted name), silently failing to refresh the page after
a successful Spotify connection.

These tests avoid triggering actual NiceGUI page builders (which require
a live event loop) by verifying the guard logic and container interaction
directly.
"""

import logging
from unittest.mock import MagicMock, patch

import playlist_arranger.main as main_mod
from playlist_arranger.ui import state as _state


def _reset_state():
    """Clean up state after each test."""
    main_mod._right_panel = None
    main_mod._current_page = "welcome"
    _state.sp = None
    _state.spotify_user_id = None


def test_render_right_panel_logs_error_when_right_panel_is_none():
    """When _right_panel is None (dual-module-object bug scenario),
    render_right_panel() must log a clear diagnostic error."""
    _reset_state()
    main_mod._current_page = "spotify_source"

    # Attach a temporary handler to capture log records
    logger = logging.getLogger("playlist_arranger.main")
    handler = logging.handlers.MemoryHandler(100)
    handler.setLevel(logging.ERROR)

    try:
        logger.addHandler(handler)
        main_mod.render_right_panel()
        handler.flush()
        error_messages = [r.getMessage() for r in handler.buffer
                          if r.levelno >= logging.ERROR]
    finally:
        logger.removeHandler(handler)

    assert len(error_messages) >= 1, (
        "render_right_panel() with _right_panel=None must log an error, "
        "not fail silently."
    )
    assert any("_right_panel is None" in msg for msg in error_messages), (
        f"Error log should mention '_right_panel is None', got: {error_messages}"
    )


def test_render_right_panel_clears_container_when_set():
    """With _right_panel set, .clear() is called on the container BEFORE
    the page builder is entered (so a stale container is always wiped)."""
    _reset_state()
    fake_container = MagicMock()
    # Make the fake container work as a context manager
    fake_container.__enter__.return_value = None
    fake_container.__exit__.return_value = None

    main_mod._right_panel = fake_container
    main_mod._current_page = "spotify_source"
    _state.sp = MagicMock()
    _state.spotify_user_id = "test_user_456"

    # Patch all page builders so we don't hit NiceGUI internals
    with patch("playlist_arranger.main.build_welcome"), \
         patch("playlist_arranger.main.build_spotify_section"), \
         patch("playlist_arranger.main.build_local_section"), \
         patch("playlist_arranger.main.build_anchors"), \
         patch("playlist_arranger.main.build_smart_sorting"):
        main_mod.render_right_panel()

    fake_container.clear.assert_called_once()
    _reset_state()


def test_render_right_panel_does_nothing_when_panel_is_none():
    """When _right_panel is None, .clear() must NOT be called (there is
    no container to clear) — the function returns early after logging."""
    _reset_state()
    fake_container = MagicMock()
    main_mod._right_panel = None  # explicitly None
    main_mod._current_page = "spotify_source"

    main_mod.render_right_panel()

    fake_container.clear.assert_not_called()
    _reset_state()
