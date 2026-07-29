"""UI-level regression tests for the stats analysis workflow.

Tests the gating logic, button states, and save behavior without
running actual audio analysis (all external calls mocked per project rules).
"""

from unittest.mock import MagicMock, patch, ANY

import pytest


def _mock_stats_result():
    """Return a minimal StatsResult with flatness calibration."""
    from playlist_arranger.sorting.stats_analysis import StatsResult, ComponentCalibration
    sr = StatsResult(playlist_id="pl_ui", snapshot_id="snap_ui")
    for name, scale in [("dynamic_range", 17.56), ("onset_str", 0.63),
                         ("flatness", 0.005), ("transition", 0.12)]:
        sr.components[name] = ComponentCalibration(
            name=name, calibration_scale=scale,
            observed_min=0.1, observed_max=0.9, observed_mean=0.5,
            observed_std=0.2, observed_cv=0.4,
            histogram_counts=[1]*12, histogram_edges=[0.0]*13,
        )
    sr.weights_used = {"mood": 0.48, "bpm": 0.12, "transition": 0.20,
                       "key": 0.12, "energy": 0.08, "texture": 0.10,
                       "freq_balance": 0.08}
    return sr


def _setup_page_state():
    """Minimal setup for smart_sorting module state."""
    import playlist_arranger.ui.pages.smart_sorting as ss
    ss._playlist_tracks = [
        {"id": "t1", "name": "A", "artist": "X", "uri": "spotify:track:t1",
         "status": "OK"},
        {"id": "t2", "name": "B", "artist": "Y", "uri": "spotify:track:t2",
         "status": "OK"},
    ]
    ss._playlist_name = "Test Playlist"
    ss._current_snapshot_id = "snap_ui"


def test_run_sorting_disabled_without_stats_cache():
    """Run Sorting is disabled when no stats cache exists."""
    import playlist_arranger.ui.pages.smart_sorting as ss
    from playlist_arranger.ui import state as _state

    _state.selected_playlist_id = "pl_ui"
    _setup_page_state()
    ss._stats_result = None

    # Simulate button state
    ss._analyze_stats_btn = MagicMock()
    ss._start_sort_btn = MagicMock()
    ss._analyze_hint = MagicMock()
    ss._refresh_buttons()

    ss._start_sort_btn.set_enabled.assert_called_once_with(False)


def test_run_sorting_enabled_with_valid_cache():
    """Run Sorting is enabled when valid stats cache + all tracks OK."""
    import playlist_arranger.ui.pages.smart_sorting as ss
    from playlist_arranger.ui import state as _state

    _state.selected_playlist_id = "pl_ui"
    _setup_page_state()
    ss._stats_result = _mock_stats_result()

    ss._analyze_stats_btn = MagicMock()
    ss._start_sort_btn = MagicMock()
    ss._analyze_hint = MagicMock()

    # get_track_status checks DB and embeddings — mock it to return OK
    with patch.object(ss, "get_track_status", return_value=ss.STATUS_OK):
        ss._refresh_buttons()

    ss._start_sort_btn.set_enabled.assert_called_once_with(True)


def test_analyze_triggers_analysis_with_correct_args():
    """_on_analyze_statistics calls analyze_playlist_stats with correct pl_id/snapshot_id."""
    import asyncio
    import playlist_arranger.ui.pages.smart_sorting as ss
    from playlist_arranger.ui import state as _state

    _state.selected_playlist_id = "pl_ui"
    _setup_page_state()

    with patch.object(ss, "_logs_expansion"), \
         patch.object(ss, "_spinner"), \
         patch.object(ss, "_enqueue_log"), \
         patch.object(ss, "_drain_log_queue"), \
         patch.object(ss, "_render_full_stats_panel"), \
         patch("playlist_arranger.database.db.load_all",
               return_value={"t1": {"features": {}, "start_seg": {}, "end_seg": {}},
                              "t2": {"features": {}, "start_seg": {}, "end_seg": {}}}), \
         patch("playlist_arranger.sorting.distance._load_embedding",
               return_value=None), \
         patch("playlist_arranger.sorting.stats_analysis.analyze_playlist_stats",
               return_value=_mock_stats_result()) as mock_analyze, \
         patch.object(ss, "ui") as mock_ui:

        ss._analyze_stats_btn = MagicMock()
        asyncio.run(ss._on_analyze_statistics())

        mock_analyze.assert_called_once()
        call_args = mock_analyze.call_args
        # First positional arg = playlist_id
        assert call_args[0][0] == "pl_ui"
        # snapshot_id is keyword or positional index 1
        assert call_args[0][1] == "snap_ui"


def test_save_stats_uses_current_slider_values():
    """_on_save_stats saves weights_used from CURRENT slider values, not stale cached ones."""
    import playlist_arranger.ui.pages.smart_sorting as ss

    sr = _mock_stats_result()
    ss._stats_result = sr
    ss._playlist_tracks = [{"id": "t1", "name": "A", "artist": "X"}]
    ss._analyze_hint = MagicMock()
    ss._start_sort_btn = MagicMock()

    # Simulate user changed weights via sliders
    ss._weight_sliders = {}
    for comp_name in ["mood", "bpm", "transition", "key", "energy", "texture", "freq_balance"]:
        slider = MagicMock()
        # User set mood to 0.60, bpm to 0.05, etc.
        new_vals = {"mood": 0.60, "bpm": 0.05, "transition": 0.15,
                     "key": 0.10, "energy": 0.05, "texture": 0.02, "freq_balance": 0.03}
        slider.value = new_vals.get(comp_name, 0.1)
        ss._weight_sliders[comp_name] = slider

    with patch.object(ss, "_render_stats_panel_summary"), \
         patch.object(ss, "ui") as mock_ui, \
         patch("playlist_arranger.sorting.stats_analysis.save_stats_cache") as mock_save:

        ss._on_save_stats()

        mock_save.assert_called_once()
        saved_result = mock_save.call_args[0][0]
        assert saved_result.weights_used["mood"] == 0.60
        assert saved_result.weights_used["bpm"] == 0.05
        assert saved_result.weights_used["transition"] == 0.15