"""Tests for nav button (Anchors/Sorting) enabled-state lifecycle.

Regression guard: after do_connect()'s deferred render_right_panel()
call (which bypasses set_page() and previously never refreshed nav
button state), Anchors and Sorting buttons must become enabled
automatically — no manual page navigation required.
"""

from unittest.mock import MagicMock, patch

import playlist_arranger.main as main_mod
from playlist_arranger.ui import state as _state


def _reset():
    """Clean state between tests."""
    main_mod._anchor_btn = None
    main_mod._sort_btn = None
    main_mod._right_panel = None
    main_mod._current_page = "welcome"
    _state.sp = None
    _state.spotify_user_id = None


def test_refresh_nav_buttons_disables_when_sp_none():
    """Buttons must be disabled when _state.sp is None (not connected)."""
    _reset()
    main_mod._anchor_btn = MagicMock()
    main_mod._sort_btn = MagicMock()
    _state.sp = None

    main_mod._refresh_nav_buttons()

    main_mod._anchor_btn.set_enabled.assert_called_once_with(False)
    main_mod._sort_btn.set_enabled.assert_called_once_with(False)
    _reset()


def test_refresh_nav_buttons_enables_when_sp_set():
    """Buttons must be enabled when _state.sp is set (connected)."""
    _reset()
    main_mod._anchor_btn = MagicMock()
    main_mod._sort_btn = MagicMock()
    _state.sp = MagicMock()

    main_mod._refresh_nav_buttons()

    main_mod._anchor_btn.set_enabled.assert_called_once_with(True)
    main_mod._sort_btn.set_enabled.assert_called_once_with(True)
    _reset()


def test_render_right_panel_refreshes_nav_buttons():
    """Regression test: render_right_panel() must call _refresh_nav_buttons()
    even when called directly (bypassing set_page()), as do_connect()'s
    _deferred_rebuild() does after a successful Spotify connect."""
    _reset()
    main_mod._anchor_btn = MagicMock()
    main_mod._sort_btn = MagicMock()
    fake_container = MagicMock()
    fake_container.__enter__.return_value = None
    fake_container.__exit__.return_value = None
    main_mod._right_panel = fake_container
    main_mod._current_page = "spotify_source"
    _state.sp = MagicMock()
    _state.spotify_user_id = "test_user"

    # Patch builders so we don't hit NiceGUI internals
    with patch("playlist_arranger.main.build_welcome"), \
         patch("playlist_arranger.main.build_spotify_section"), \
         patch("playlist_arranger.main.build_local_section"), \
         patch("playlist_arranger.main.build_anchors"), \
         patch("playlist_arranger.main.build_smart_sorting"):
        main_mod.render_right_panel()

    main_mod._anchor_btn.set_enabled.assert_called_with(True)
    main_mod._sort_btn.set_enabled.assert_called_with(True)
    _reset()


def test_render_right_panel_keeps_buttons_disabled_without_connect():
    """Sanity: buttons stay disabled when _state.sp is None, even after
    render_right_panel() is called."""
    _reset()
    main_mod._anchor_btn = MagicMock()
    main_mod._sort_btn = MagicMock()
    fake_container = MagicMock()
    fake_container.__enter__.return_value = None
    fake_container.__exit__.return_value = None
    main_mod._right_panel = fake_container
    main_mod._current_page = "welcome"
    _state.sp = None

    with patch("playlist_arranger.main.build_welcome"), \
         patch("playlist_arranger.main.build_spotify_section"), \
         patch("playlist_arranger.main.build_local_section"), \
         patch("playlist_arranger.main.build_anchors"), \
         patch("playlist_arranger.main.build_smart_sorting"):
        main_mod.render_right_panel()

    main_mod._anchor_btn.set_enabled.assert_called_with(False)
    main_mod._sort_btn.set_enabled.assert_called_with(False)
    _reset()