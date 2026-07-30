"""Tests for smart_sorting.py — post-save behaviour and client-context guards.

Tests verify:
  1. ``client = ui.context.client`` is captured before ``asyncio.to_thread``
     in both save handlers (source inspection).
  2. ``_refresh_playlist_dropdown()`` updates the dropdown and preserves the
     current selection.
  3. ``_refresh_current_playlist_tracks()`` re-fetches tracks, writes cache,
     and calls ``_rebuild_all()``.
  4. Failures in post-save refresh helpers are caught, logged, and never
     prevent the success ``ui.notify()`` from firing.
"""

from __future__ import annotations

import sys

sys.path.insert(0, r"e:\Projects\Spotify_playlists\repository")

import asyncio
import logging
from unittest.mock import MagicMock

# Mock nicegui before importing modules that import it at module level
try:
    import nicegui  # noqa: F401
except ImportError:
    sys.modules["nicegui"] = MagicMock()
    sys.modules["nicegui.ui"] = MagicMock()

import pytest

logger = logging.getLogger(__name__)

# ── Path to the source file ──────────────────────────────────────────────────
import pathlib

_SRC_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "playlist_arranger"
    / "ui"
    / "pages"
    / "smart_sorting.py"
)


# ══════════════════════════════════════════════════════════════════════════════
#  Dummy helpers
# ══════════════════════════════════════════════════════════════════════════════


def _make_track(track_id: str, uri: str, name: str, artist: str = "Artist") -> dict:
    return {
        "id": track_id,
        "uri": uri,
        "name": name,
        "artist": artist,
        "album": "Test Album",
        "duration_ms": 200000,
    }


def _run(aw):
    """Run an async awaitable synchronously (no pytest-asyncio needed)."""
    return asyncio.get_event_loop().run_until_complete(aw)


# ══════════════════════════════════════════════════════════════════════════════
#  BUG FIX: client-context guard (source inspection)
# ══════════════════════════════════════════════════════════════════════════════


class TestClientContextGuard:
    """Verify that ``ui.context.client`` is captured before background work
    in both save handlers."""

    def test_client_captured_in_on_save_new_playlist(self):
        """``_on_save_new_playlist`` captures ``ui.context.client`` before await."""
        source = _SRC_PATH.read_text(encoding="utf-8")

        assert "client = ui.context.client" in source, (
            "Expected 'client = ui.context.client' in _on_save_new_playlist"
        )
        client_pos = source.index("client = ui.context.client")
        to_thread_pos = source.index("asyncio.to_thread")
        assert client_pos < to_thread_pos, (
            f"client must be captured before asyncio.to_thread "
            f"(client at {client_pos}, to_thread at {to_thread_pos})"
        )
        after_to_thread = source[to_thread_pos:]
        assert "with client:" in after_to_thread, (
            "Expected 'with client:' wrapping post-await UI calls"
        )

    def test_client_captured_in_on_save_current_playlist(self):
        """The ``client`` is captured BEFORE ``_do_overwrite`` is defined
        and ``asyncio.ensure_future`` spawns the background task."""
        source = _SRC_PATH.read_text(encoding="utf-8")

        client_pos = source.index("client = ui.context.client")
        do_overwrite_pos = source.index("def _do_overwrite")
        assert client_pos < do_overwrite_pos, (
            f"client must be captured before _do_overwrite "
            f"(client at {client_pos}, _do_overwrite at {do_overwrite_pos})"
        )
        after_do_overwrite = source[do_overwrite_pos:]
        assert "with client:" in after_do_overwrite, (
            "Expected 'with client:' wrapping post-await UI calls in _execute"
        )


# ══════════════════════════════════════════════════════════════════════════════
#  FEATURE 1: _refresh_playlist_dropdown
# ══════════════════════════════════════════════════════════════════════════════


class TestRefreshPlaylistDropdown:
    """Tests for ``_refresh_playlist_dropdown()``."""

    @pytest.fixture(autouse=True)
    def _setup_fixture(self):
        from playlist_arranger.ui.pages import smart_sorting as ss

        self.ss = ss

        # Mock state
        self.mock_sp = MagicMock()
        self.orig_sp = self.ss._state.sp
        self.orig_uid = self.ss._state.spotify_user_id
        self.ss._state.sp = self.mock_sp
        self.ss._state.spotify_user_id = "test_user"

        # Mock get_own_playlists
        self.mock_get_own = MagicMock(
            return_value=[
                {"id": "plA", "name": "Playlist A"},
                {"id": "plB", "name": "Playlist B"},
                {"id": "plNew", "name": "New Playlist"},
            ]
        )
        self.orig_get_own = self.ss.get_own_playlists
        self.ss.get_own_playlists = self.mock_get_own

        # Set up a fake dropdown
        self.orig_sort_select = self.ss._sort_select
        self.fake_select = MagicMock()
        self.fake_select.value = "plA"
        self.ss._sort_select = self.fake_select

        yield

        self.ss._state.sp = self.orig_sp
        self.ss._state.spotify_user_id = self.orig_uid
        self.ss.get_own_playlists = self.orig_get_own
        self.ss._sort_select = self.orig_sort_select

    def test_sets_options_and_preserves_current_value(self):
        """After saving a new playlist, the dropdown gets new options and
        the currently selected playlist is preserved."""
        _run(self.ss._refresh_playlist_dropdown())

        expected_options = {
            "plA": "Playlist A",
            "plB": "Playlist B",
            "plNew": "New Playlist",
        }
        self.fake_select.set_options.assert_called_once_with(expected_options)
        assert self.fake_select.value == "plA"

    def test_noop_when_dropdown_is_none(self):
        """Returns early without errors when _sort_select is None."""
        self.ss._sort_select = None
        _run(self.ss._refresh_playlist_dropdown())  # Should not raise

    def test_noop_when_sp_not_connected(self):
        """Returns early when Spotify client is not connected."""
        self.ss._state.sp = None
        self.ss._state.spotify_user_id = None
        _run(self.ss._refresh_playlist_dropdown())
        self.mock_get_own.assert_not_called()

    def test_failure_is_caught_and_logged(self, caplog):
        """A failure in get_own_playlists is caught and logged, never raised."""
        self.mock_get_own.side_effect = RuntimeError("API down")

        with caplog.at_level(logging.ERROR, logger="playlist_arranger.ui.pages.smart_sorting"):
            _run(self.ss._refresh_playlist_dropdown())

        assert "Failed to refresh playlist dropdown after save-as-new" in caplog.text


# ══════════════════════════════════════════════════════════════════════════════
#  FEATURE 2: _refresh_current_playlist_tracks
# ══════════════════════════════════════════════════════════════════════════════


class TestRefreshCurrentPlaylistTracks:
    """Tests for ``_refresh_current_playlist_tracks()``."""

    @pytest.fixture(autouse=True)
    def _setup_fixture(self):
        from playlist_arranger.ui.pages import smart_sorting as ss

        self.ss = ss

        # Mock state
        self.mock_sp = MagicMock()
        self.mock_sp.playlist.return_value = {"snapshot_id": "snap_new", "name": "My List"}
        self.orig_sp = self.ss._state.sp
        self.ss._state.sp = self.mock_sp
        self.orig_plid = getattr(self.ss._state, "selected_playlist_id", None)
        self.ss._state.selected_playlist_id = "pl_saved"

        # Mock get_playlist_tracks
        self.fake_tracks = [
            _make_track("t1", "spotify:track:t1", "Track 1"),
            _make_track("t2", "spotify:track:t2", "Track 2"),
        ]
        self.mock_get_tracks = MagicMock(return_value=self.fake_tracks)
        self.orig_get_tracks = self.ss.get_playlist_tracks
        self.ss.get_playlist_tracks = self.mock_get_tracks

        # Mock atomic_write_json
        self.write_calls = []
        self.orig_atomic = self.ss.atomic_write_json
        self.ss.atomic_write_json = lambda path, data: self.write_calls.append((path, data))

        # Mock _rebuild_all
        self.rebuild_calls = []
        self.orig_rebuild = self.ss._rebuild_all
        self.ss._rebuild_all = lambda: self.rebuild_calls.append(1)

        # Save original globals
        self.orig_tracks = self.ss._playlist_tracks
        self.orig_snap = self.ss._current_snapshot_id
        self.ss._playlist_tracks = []
        self.ss._current_snapshot_id = ""

        yield

        self.ss._state.sp = self.orig_sp
        self.ss._state.selected_playlist_id = self.orig_plid
        self.ss.get_playlist_tracks = self.orig_get_tracks
        self.ss.atomic_write_json = self.orig_atomic
        self.ss._rebuild_all = self.orig_rebuild
        self.ss._playlist_tracks = self.orig_tracks
        self.ss._current_snapshot_id = self.orig_snap

    def test_fetches_tracks_and_writes_cache(self):
        """After reorder, the handler re-fetches tracks, writes cache, and redraws."""
        _run(self.ss._refresh_current_playlist_tracks())

        self.mock_get_tracks.assert_called_once_with(self.mock_sp, "pl_saved")
        assert len(self.ss._playlist_tracks) == 2
        assert self.ss._playlist_tracks[0]["id"] == "t1"

        assert len(self.write_calls) == 1
        path, _data = self.write_calls[0]
        assert "pl_saved" in str(path)

        assert len(self.rebuild_calls) == 1

    def test_noop_when_no_playlist_selected(self):
        """Returns early when no playlist is selected."""
        self.ss._state.selected_playlist_id = None
        _run(self.ss._refresh_current_playlist_tracks())
        self.mock_get_tracks.assert_not_called()

    def test_warns_on_empty_result(self, caplog):
        """Logs a warning when get_playlist_tracks returns an empty list."""
        self.mock_get_tracks.return_value = []

        with caplog.at_level(logging.WARNING, logger="playlist_arranger.ui.pages.smart_sorting"):
            _run(self.ss._refresh_current_playlist_tracks())

        assert "returned empty list" in caplog.text

    def test_failure_is_caught_and_logged(self, caplog):
        """A failure in get_playlist_tracks is caught, logged, and never raised."""
        self.mock_get_tracks.side_effect = RuntimeError("API down")

        with caplog.at_level(logging.ERROR, logger="playlist_arranger.ui.pages.smart_sorting"):
            _run(self.ss._refresh_current_playlist_tracks())

        assert "Failed to refresh playlist tracks after reorder" in caplog.text

    def test_snapshot_fetch_failure_is_graceful(self):
        """If fetching the new snapshot_id fails, the flow still completes."""
        self.mock_sp.playlist.side_effect = RuntimeError("snapshot fetch failed")

        _run(self.ss._refresh_current_playlist_tracks())

        assert len(self.ss._playlist_tracks) == 2
        assert len(self.rebuild_calls) == 1
        assert self.ss._current_snapshot_id == ""


# ══════════════════════════════════════════════════════════════════════════════
#  Notify ordering guarantee
# ══════════════════════════════════════════════════════════════════════════════


class TestNotifySurvivesHelperFailure:
    """The success ``ui.notify`` fires BEFORE the post-save refresh helper
    is awaited, so even if the helper raises an exception, the notification
    has already been delivered."""

    def test_on_save_new_playlist_notifies_before_dropdown_refresh(self):
        source = _SRC_PATH.read_text(encoding="utf-8")
        success_start = source.index('if result["success"]:')
        success_section = source[success_start:]

        notify_pos = success_section.index(
            'ui.notify(f"Saved to new playlist:'
        )
        refresh_pos = success_section.index("await _refresh_playlist_dropdown()")

        assert notify_pos < refresh_pos, (
            "ui.notify must appear BEFORE _refresh_playlist_dropdown"
        )

    def test_on_save_current_playlist_notifies_before_track_refresh(self):
        source = _SRC_PATH.read_text(encoding="utf-8")
        success_start = source.index('if result["success"]:')
        success_section = source[success_start:]

        notify_pos = success_section.index(
            'ui.notify(f"Playlist reordered:'
        )
        refresh_pos = success_section.index(
            "await _refresh_current_playlist_tracks()"
        )

        assert notify_pos < refresh_pos, (
            "ui.notify must appear BEFORE _refresh_current_playlist_tracks"
        )


# ══════════════════════════════════════════════════════════════════════════════
#  Dropdown selection preservation
# ══════════════════════════════════════════════════════════════════════════════


def test_dropdown_preserves_selection_source_present():
    """Verify the source code preserves the current value after set_options."""
    source = _SRC_PATH.read_text(encoding="utf-8")

    assert "current_value = _sort_select.value" in source, (
        "Expected to capture current value before set_options"
    )
    assert "_sort_select.set_options(options)" in source, (
        "Expected set_options call"
    )
    assert (
        "_sort_select.value = current_value" in source
        or "_sort_select.set_value(current_value)" in source
    ), "Expected preserved value after set_options"


# ══════════════════════════════════════════════════════════════════════════════
#  Stale-reference cleanup
# ══════════════════════════════════════════════════════════════════════════════


class TestStaleReferenceCleanup:
    """Verify that ``build_smart_sorting()`` resets all UI element globals
    to None before creating new ones, and that timer cleanup happens."""

    _ALL_UI_GLOBALS = [
        "_sort_select",
        "_track_list_container",
        "_anchors_list_container",
        "_analyze_stats_btn",
        "_start_sort_btn",
        "_analyze_hint",
        "_logs_expansion",
        "_logs_content",
        "_logs_summary",
        "_histogram_image",
        "_stats_panel",
        "_results_container",
        "_spinner",
        "_save_new_btn",
        "_save_current_btn",
        "_save_spinner",
        "_insert_last_btn",
        "_insert_n_input",
    ]

    def test_all_ui_globals_reset_in_build(self):
        """Every UI global in the audit list is set to None at the top of
        ``build_smart_sorting()``, before the call to ``_on_playlist_selected``."""
        source = _SRC_PATH.read_text(encoding="utf-8")

        # Find the body of build_smart_sorting
        build_start = source.index("def build_smart_sorting():")
        playlist_select_call = source.index(
            "_on_playlist_selected(default_val)", build_start
        )
        build_body = source[build_start:playlist_select_call]

        for var_name in self._ALL_UI_GLOBALS:
            assert f"{var_name} = None" in build_body, (
                f"Expected '{var_name} = None' in build_smart_sorting BEFORE "
                f"_on_playlist_selected(default_val)"
            )

    def test_timer_cancel_before_new_timer(self):
        """The stale ``_log_timer`` is cancelled before a new one is created."""
        source = _SRC_PATH.read_text(encoding="utf-8")

        build_start = source.index("def build_smart_sorting():")
        build_body = source[build_start:]

        # Timer cleanup must appear before "ui.timer(0.3"
        timer_cancel_pos = build_body.index("_log_timer.cancel()")
        new_timer_pos = build_body.index("ui.timer(0.3")
        assert timer_cancel_pos < new_timer_pos, (
            "Old _log_timer must be cancelled before creating a new one"
        )

    def test_stale_reference_regression(self):
        """Simulate the reported bug: set stale button refs on the module,
        then verify _refresh_save_buttons does not crash."""
        from playlist_arranger.ui.pages import smart_sorting as ss

        # Create a mock button that raises RuntimeError on set_enabled
        mock_btn = MagicMock()
        mock_btn.set_enabled.side_effect = RuntimeError("element has been deleted")

        # Set stale refs
        save_new = ss._save_new_btn
        save_cur = ss._save_current_btn
        save_spin = ss._save_spinner
        ss._save_new_btn = mock_btn
        ss._save_current_btn = mock_btn
        # Also test spinner
        mock_spinner = MagicMock()
        mock_spinner.set_visibility.side_effect = RuntimeError("element has been deleted")
        ss._save_spinner = mock_spinner

        try:
            # Should not raise — caught and logged
            ss._refresh_save_buttons()
            # Button set_enabled was called (then raised, which was caught)
            assert mock_btn.set_enabled.called
        finally:
            ss._save_new_btn = save_new
            ss._save_current_btn = save_cur
            ss._save_spinner = save_spin

    def test_refresh_save_buttons_noop_when_none(self):
        """_refresh_save_buttons is a no-op when all button refs are None."""
        from playlist_arranger.ui.pages import smart_sorting as ss

        save_new = ss._save_new_btn
        save_cur = ss._save_current_btn
        save_spin = ss._save_spinner
        ss._save_new_btn = None
        ss._save_current_btn = None
        ss._save_spinner = None

        try:
            # Should not raise
            ss._refresh_save_buttons()
        finally:
            ss._save_new_btn = save_new
            ss._save_current_btn = save_cur
            ss._save_spinner = save_spin

    def test_refresh_buttons_catches_stale_reference(self):
        """_refresh_buttons does not raise when all button refs are stale."""
        from playlist_arranger.ui.pages import smart_sorting as ss

        mock_btn = MagicMock()
        mock_btn.set_enabled.side_effect = RuntimeError("element has been deleted")

        orig_analyze = ss._analyze_stats_btn
        orig_start = ss._start_sort_btn
        orig_insert = ss._insert_last_btn
        orig_hint = ss._analyze_hint
        ss._analyze_stats_btn = mock_btn
        ss._start_sort_btn = mock_btn
        ss._insert_last_btn = mock_btn
        ss._analyze_hint = None

        try:
            # Should not raise
            ss._refresh_buttons()
            assert mock_btn.set_enabled.called
        finally:
            ss._analyze_stats_btn = orig_analyze
            ss._start_sort_btn = orig_start
            ss._insert_last_btn = orig_insert
            ss._analyze_hint = orig_hint
