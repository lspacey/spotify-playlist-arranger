"""UI-level regression test: clicking Start Sorting with zero anchors
must NOT show the old blocking warning — it must proceed to call the solver."""

from unittest.mock import MagicMock, patch


def test_on_start_sorting_proceeds_when_no_anchors():
    """Regression: Start Sorting with zero anchors must call the solver,
    not show the old 'create anchors first' blocking warning."""
    import playlist_arranger.ui.pages.smart_sorting as ss
    from playlist_arranger.ui import state as _state

    ss._playlist_tracks = [
        {"id": "t1", "name": "A", "artist": "X", "uri": "spotify:track:t1"},
        {"id": "t2", "name": "B", "artist": "Y", "uri": "spotify:track:t2"},
    ]
    ss._playlist_name = "Test Playlist"
    _state.selected_playlist_id = "pl123"

    fake_result = ([{"track_id": "t1", "name": "A", "artist": "X"},
                     {"track_id": "t2", "name": "B", "artist": "Y"}], 1.2345)

    with patch.object(ss, "_load_anchors_file", return_value=None), \
         patch("playlist_arranger.sorting.solver._run_smart_sorting",
               return_value=fake_result) as mock_solve, \
         patch.object(ss, "_drain_log_queue"), \
         patch.object(ss, "_clear_results"), \
         patch("playlist_arranger.database.db.load_all", return_value={}), \
         patch.object(ss, "_restore_ui_after_sorting"), \
         patch.object(ss, "_render_sorted_results"), \
         patch.object(ss, "_refresh_save_buttons"), \
         patch.object(ss, "ui") as mock_ui:
        import asyncio
        asyncio.run(ss._on_start_sorting())
        mock_solve.assert_called_once()

    # Check that the OLD blocking warning was NOT fired
    warning_calls = [
        c for c in mock_ui.notify.call_args_list
        if c.kwargs.get("type") == "warning"
           and "create anchors first" in str(c.args)
    ]
    assert not warning_calls, (
        "Old blocking warning 'create anchors first on the Anchors page' "
        "must not fire anymore — sorting must proceed without anchors"
    )

    # Check that an INFO notification about free sorting was fired
    info_calls = [
        c for c in mock_ui.notify.call_args_list
        if c.kwargs.get("type") == "info"
           and "anchor" in str(c.args).lower()
    ]
    assert info_calls, (
        "Expected an info notification about sorting without anchors"
    )
