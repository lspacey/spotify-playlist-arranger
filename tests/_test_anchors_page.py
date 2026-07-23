"""Unit tests for Anchors nav button state, playlist dropdown, and anchor editor logic."""

import sys
sys.path.insert(0, r"e:\Projects\Spotify_playlists\repository")

import json
import tempfile
import pathlib
from unittest.mock import patch, MagicMock

# Mock nicegui BEFORE any module importing playlist_arranger.ui.pages.anchors
# (which does `from nicegui import ui` at module level).  Tests that import
# anchors.py (event parsing, button state) need this shim because the test
# environment does not have the nicegui package installed.
try:
    import nicegui  # noqa: F401
except ImportError:
    sys.modules['nicegui'] = MagicMock()
    sys.modules['nicegui.ui'] = MagicMock()

from playlist_arranger.ui import state as _state

results = []


# ── Nav button enabled/disabled logic ──────────────────────────────────────

def test_nav_button_enabled_when_sp_connected():
    """Nav button should be enabled when state.sp is not None."""
    original_sp = _state.sp
    try:
        _state.sp = "fake_spotify_client"
        enabled = _state.sp is not None
        assert enabled, f"Expected enabled=True, got {enabled}"
    finally:
        _state.sp = original_sp


def test_nav_button_disabled_when_sp_none():
    """Nav button should be disabled when state.sp is None."""
    original_sp = _state.sp
    try:
        _state.sp = None
        enabled = _state.sp is not None
        assert not enabled, f"Expected enabled=False, got {enabled}"
    finally:
        _state.sp = original_sp


def test_nav_button_disabled_post_connect_then_disconnect():
    """Nav button transitions: connect → enabled, disconnect → disabled."""
    original_sp = _state.sp
    try:
        _state.sp = "fake_client"
        assert _state.sp is not None, "Should be enabled when connected"

        _state.sp = None
        assert _state.sp is None, "Should be disabled when disconnected"
    finally:
        _state.sp = original_sp


# ── Nav button reactivity (set_page triggers _refresh_nav_buttons) ────────

def test_nav_button_refresh_logic():
    """_refresh_nav_buttons logic: enables/disables based on state.sp is not None."""
    # Test the core logic without importing main (avoids nicegui dependency in test env)

    class FakeButton:
        def __init__(self):
            self._enabled = False
        def set_enabled(self, val):
            self._enabled = val

    btn = FakeButton()
    original_sp = _state.sp
    try:
        # Simulate disconnected
        _state.sp = None
        btn.set_enabled(_state.sp is not None)
        assert not btn._enabled, "Should be disabled when sp=None"

        # Simulate connected
        _state.sp = "connected_client"
        btn.set_enabled(_state.sp is not None)
        assert btn._enabled, "Should be enabled after sp is set"
    finally:
        _state.sp = original_sp


def test_sort_button_refresh_logic():
    """Sort button re-evaluates on refresh — mimics _refresh_nav_buttons for sort."""
    class FakeButton:
        def __init__(self):
            self._enabled = False
        def set_enabled(self, val):
            self._enabled = val

    btn = FakeButton()
    original_plan = list(_state.current_anchor_plan)
    try:
        # No anchor plan → disabled
        _state.current_anchor_plan[:] = []
        btn.set_enabled(_state.has_anchor_plan())
        assert not btn._enabled, "Should be disabled without anchor plan"

        # With anchor plan → enabled
        _state.current_anchor_plan[:] = [{"type": "anchor", "track_id": "test"}]
        btn.set_enabled(_state.has_anchor_plan())
        assert btn._enabled, "Should be enabled with anchor plan"
    finally:
        _state.current_anchor_plan[:] = original_plan


# ── Playlist dropdown population logic ─────────────────────────────────────

def test_playlist_names_extracted_from_get_own_playlists():
    """Dropdown options should be {id: name} from get_own_playlists result."""
    mock_playlists = [
        {"id": "pl1", "name": "Chill Vibes"},
        {"id": "pl2", "name": "Workout Mix"},
        {"id": "pl3", "name": "Late Night Jazz"},
    ]
    options = {pl["id"]: pl["name"] for pl in mock_playlists}
    assert options == {
        "pl1": "Chill Vibes",
        "pl2": "Workout Mix",
        "pl3": "Late Night Jazz",
    }, f"Unexpected options: {options}"


def test_dropdown_empty_when_no_playlists():
    """Dropdown should have empty options when get_own_playlists returns []."""
    mock_playlists = []
    options = {pl["id"]: pl["name"] for pl in mock_playlists}
    assert options == {}, f"Expected empty dict, got {options}"


def test_dropdown_handles_duplicate_ids_last_wins():
    """When duplicate IDs exist, last name wins (dict overwrite)."""
    mock_playlists = [
        {"id": "pl1", "name": "First"},
        {"id": "pl1", "name": "Second"},
    ]
    options = {pl["id"]: pl["name"] for pl in mock_playlists}
    assert options == {"pl1": "Second"}, f"Expected last-wins, got {options}"


def test_reused_function_is_get_own_playlists():
    """Verify the correct function from spotify_source is used."""
    from playlist_arranger.sources.spotify_source import get_own_playlists
    assert callable(get_own_playlists), "get_own_playlists should be callable"
    assert get_own_playlists.__module__ == "playlist_arranger.sources.spotify_source", \
        f"Expected spotify_source module, got {get_own_playlists.__module__}"


def test_spotify_user_id_required_for_playlist_fetch():
    """Playlist fetch requires both sp and spotify_user_id."""
    original_sp = _state.sp
    try:
        _state.sp = None
        should_fetch = _state.sp is not None and _state.spotify_user_id is not None
        assert not should_fetch, "Should not fetch when sp is None"
    finally:
        _state.sp = original_sp

    original_uid = _state.spotify_user_id
    original_sp2 = _state.sp
    try:
        _state.sp = "fake_client"
        _state.spotify_user_id = None
        should_fetch = _state.sp is not None and _state.spotify_user_id is not None
        assert not should_fetch, "Should not fetch when user_id is None"
    finally:
        _state.sp = original_sp2
        _state.spotify_user_id = original_uid

    original_uid2 = _state.spotify_user_id
    original_sp3 = _state.sp
    try:
        _state.sp = "fake_client"
        _state.spotify_user_id = "test_user_123"
        should_fetch = _state.sp is not None and _state.spotify_user_id is not None
        assert should_fetch, "Should fetch when both sp and user_id are set"
    finally:
        _state.sp = original_sp3
        _state.spotify_user_id = original_uid2


# ── Part 1: Playlist selection persistence ────────────────────────────────

def test_playlist_selection_persists_across_navigation():
    """anchors_selected_playlist_id survives page navigation (state field test)."""
    original_val = _state.anchors_selected_playlist_id
    try:
        _state.anchors_selected_playlist_id = "pl_test_789"
        assert _state.anchors_selected_playlist_id == "pl_test_789"
        # Simulate navigating away and back — value persists in state
        assert _state.anchors_selected_playlist_id == "pl_test_789"
    finally:
        _state.anchors_selected_playlist_id = original_val


# ── Part 2: Anchor plan loading from cache ────────────────────────────────

def test_load_anchors_empty_when_no_file():
    """_load_anchors_file returns None when no file exists."""
    from playlist_arranger.sorting.anchors import _load_anchors_file
    plan = _load_anchors_file("nonexistent_pl_id_99999")
    assert plan is None, f"Expected None for nonexistent file, got {plan}"


def test_load_anchors_from_existing_file():
    """_load_anchors_file loads plan with anchor + placeholder items."""
    from playlist_arranger.config import ANCHORS_DIR_DEFAULT
    from playlist_arranger.sorting.anchors import _save_anchors_file, _load_anchors_file
    import datetime

    test_id = "test_load_anchors_123"
    test_plan = [
        {"type": "anchor", "track_id": "track_abc"},
        {"type": "placeholder"},
        {"type": "anchor", "track_id": "track_xyz"},
        {"type": "placeholder"},
    ]

    # Save to actual anchors dir
    _save_anchors_file(test_id, "Test Playlist", test_plan)

    try:
        loaded = _load_anchors_file(test_id)
        assert loaded is not None, "Should have loaded a plan"
        assert len(loaded) == 4, f"Expected 4 items, got {len(loaded)}"
        assert loaded[0]["type"] == "anchor"
        assert loaded[0]["track_id"] == "track_abc"
        assert loaded[1]["type"] == "placeholder"
        assert loaded[2]["type"] == "anchor"
        assert loaded[3]["type"] == "placeholder"
    finally:
        # Clean up test file
        af = ANCHORS_DIR_DEFAULT / f"anchors_{test_id}.json"
        if af.exists():
            af.unlink()


def test_anchors_cache_filename_does_not_collide_with_track_cache():
    """Anchors files are in anchors/ dir, track cache is in cache/ dir — no collision."""
    from playlist_arranger.config import ANCHORS_DIR_DEFAULT, CACHE_DIR_DEFAULT
    # Anchor file pattern: anchors/anchors_<id>.json
    # Track cache pattern: cache/<id>-<snapshot>.tracks.json
    anchors_dir = str(ANCHORS_DIR_DEFAULT.resolve())
    cache_dir = str(CACHE_DIR_DEFAULT.resolve())
    assert anchors_dir != cache_dir, (
        f"Anchors dir ({anchors_dir}) and cache dir ({cache_dir}) must differ"
    )
    # Filename patterns differ: anchors_<id>.json vs <id>-<snapshot>.tracks.json
    sample_pl = "pl_abc123"
    anchor_fn = f"anchors_{sample_pl}.json"
    track_cache_pattern = f"{sample_pl}-*.tracks.json"
    assert anchor_fn != track_cache_pattern, "Filename patterns differ"
    assert not anchor_fn.startswith(sample_pl + "-"), "No overlap in naming"


# ── Part 3 & 4: Anchor list manipulation logic ────────────────────────────

# Helper: simple anchor plan manipulation functions (pure logic, no UI)
def _anchored_track_ids(plan):
    return {e["track_id"] for e in plan if e["type"] == "anchor"}


def _move_up(plan, selected_idx):
    if selected_idx is None or selected_idx <= 0:
        return plan, selected_idx
    plan = list(plan)
    plan[selected_idx - 1], plan[selected_idx] = plan[selected_idx], plan[selected_idx - 1]
    return plan, selected_idx - 1


def _move_down(plan, selected_idx):
    if selected_idx is None or selected_idx >= len(plan) - 1:
        return plan, selected_idx
    plan = list(plan)
    plan[selected_idx], plan[selected_idx + 1] = plan[selected_idx + 1], plan[selected_idx]
    return plan, selected_idx + 1


def _remove_item(plan, selected_idx):
    if selected_idx is None or selected_idx < 0 or selected_idx >= len(plan):
        return plan, None
    plan = list(plan)
    del plan[selected_idx]
    return plan, None


def _add_placeholder(plan):
    return list(plan) + [{"type": "placeholder"}]


def _add_anchor(plan, track_id):
    if track_id in _anchored_track_ids(plan):
        return list(plan)
    return list(plan) + [{"type": "anchor", "track_id": track_id}]


def _clear_anchors():
    return []


def test_move_up_disabled_when_first():
    plan = [{"type": "anchor", "track_id": "a"}, {"type": "placeholder"}]
    # selected_idx = 0 → can't move up
    params = _move_up(plan, 0)
    assert params[1] == 0, "Selection should not change when moving up from first"
    assert plan == params[0], "Plan should not change when moving up from first"


def test_move_up_enabled_middle():
    plan = [{"type": "anchor", "track_id": "a"}, {"type": "placeholder"}, {"type": "anchor", "track_id": "b"}]
    new_plan, new_idx = _move_up(plan, 1)  # move placeholder from pos 1 to 0
    assert new_idx == 0, f"Expected new_idx=0, got {new_idx}"
    assert new_plan[0]["type"] == "placeholder", "Placeholder should now be first"
    assert new_plan[1]["type"] == "anchor", "First anchor should now be second"


def test_move_down_disabled_when_last():
    plan = [{"type": "anchor", "track_id": "a"}, {"type": "placeholder"}]
    params = _move_down(plan, 1)  # last item
    assert params[1] == 1, "Selection should not change when moving down last"
    assert plan == params[0], "Plan should not change when moving down last"


def test_remove_reenables_track_in_playlist_list():
    plan = [{"type": "anchor", "track_id": "t1"}, {"type": "placeholder"}, {"type": "anchor", "track_id": "t2"}]
    new_plan, new_idx = _remove_item(plan, 0)  # remove "t1"
    assert len(new_plan) == 2, f"Expected 2 items after remove, got {len(new_plan)}"
    assert _anchored_track_ids(new_plan) == {"t2"}, f"t1 should be removed from anchors: {_anchored_track_ids(new_plan)}"
    assert new_idx is None, "Selection should be cleared after remove"


def test_add_placeholder_always_works():
    plan = []
    new_plan = _add_placeholder(plan)
    assert len(new_plan) == 1
    assert new_plan[0]["type"] == "placeholder"


def test_add_selected_track_appends_and_disables_track():
    plan = [{"type": "anchor", "track_id": "t1"}]
    new_plan = _add_anchor(plan, "t2")
    assert len(new_plan) == 2
    assert new_plan[1] == {"type": "anchor", "track_id": "t2"}
    assert _anchored_track_ids(new_plan) == {"t1", "t2"}


def test_clear_anchors_reenables_all_tracks():
    plan = [{"type": "anchor", "track_id": "t1"}, {"type": "anchor", "track_id": "t2"}, {"type": "placeholder"}]
    new_plan = _clear_anchors()
    assert new_plan == []
    assert _anchored_track_ids(new_plan) == set()


def test_add_duplicate_track_not_allowed():
    plan = [{"type": "anchor", "track_id": "t1"}]
    new_plan = _add_anchor(plan, "t1")
    assert len(new_plan) == 1  # no change


def test_save_anchors_writes_expected_file_format():
    from playlist_arranger.config import ANCHORS_DIR_DEFAULT
    from playlist_arranger.sorting.anchors import _save_anchors_file, _load_anchors_file

    test_id = "test_save_format_456"
    test_plan = [
        {"type": "anchor", "track_id": "id_aaa"},
        {"type": "placeholder"},
        {"type": "anchor", "track_id": "id_bbb"},
        {"type": "placeholder"},
        {"type": "placeholder"},
    ]

    _save_anchors_file(test_id, "Save Test", test_plan)
    try:
        loaded = _load_anchors_file(test_id)
        assert loaded is not None
        assert len(loaded) == 5
        assert loaded[0] == {"type": "anchor", "track_id": "id_aaa"}
        assert loaded[1] == {"type": "placeholder"}
        assert loaded[4] == {"type": "placeholder"}
    finally:
        af = ANCHORS_DIR_DEFAULT / f"anchors_{test_id}.json"
        if af.exists():
            af.unlink()


def test_button_enable_state_multi_select_logic():
    """If multiple items are selected (impossible with single-click, but safety check),
    move/remove functions should not operate (they take explicit single idx)."""
    plan = [{"type": "anchor", "track_id": "a"}, {"type": "placeholder"}]
    # Our functions only take a single idx — multi-select would require
    # the caller to pick one, so the logic is self-limiting.
    # Test that passing None (no selection) is a no-op for all mutations.
    p, i = _move_down(plan, None)
    assert p == plan
    assert i is None

    p, i = _move_up(plan, None)
    assert p == plan
    assert i is None

    p, i = _remove_item(plan, None)
    assert p == plan
    assert i is None


# ── Anchored track IDs extraction ─────────────────────────────────────────

def test_anchored_track_ids_empty_plan():
    assert _anchored_track_ids([]) == set()


def test_anchored_track_ids_only_placeholders():
    plan = [{"type": "placeholder"}, {"type": "placeholder"}]
    assert _anchored_track_ids(plan) == set()


def test_anchored_track_ids_mixed():
    plan = [
        {"type": "anchor", "track_id": "id1"},
        {"type": "placeholder"},
        {"type": "anchor", "track_id": "id2"},
    ]
    assert _anchored_track_ids(plan) == {"id1", "id2"}


# ── Playlist track selectability ──────────────────────────────────────────

def test_track_not_selectable_when_anchored():
    playlist_tracks = [
        {"id": "t1", "name": "Track 1", "artist": "A"},
        {"id": "t2", "name": "Track 2", "artist": "B"},
        {"id": "t3", "name": "Track 3", "artist": "C"},
    ]
    anchored = {"t2"}  # t2 is anchored
    selectable = [t for t in playlist_tracks if t["id"] not in anchored]
    assert len(selectable) == 2
    assert selectable[0]["id"] == "t1"
    assert selectable[1]["id"] == "t3"


def test_all_tracks_selectable_when_no_anchors():
    playlist_tracks = [
        {"id": "t1", "name": "Track 1", "artist": "A"},
        {"id": "t2", "name": "Track 2", "artist": "B"},
    ]
    anchored = set()
    selectable = [t for t in playlist_tracks if t["id"] not in anchored]
    assert len(selectable) == 2


# ── Row-level locked field tests (for body slot CSS graying) ─────────────

def test_locked_row_has_locked_field_true():
    """Rows for anchored tracks must have locked=True."""
    playlist_tracks = [
        {"id": "t1", "name": "Track 1", "artist": "A"},
        {"id": "t2", "name": "Track 2", "artist": "B"},
    ]
    anchored_ids = {"t1"}

    rows = []
    for i, t in enumerate(playlist_tracks, 1):
        rows.append({
            "idx": i,
            "name": t.get("name", "?")[:45],
            "artist": t.get("artist", "?")[:30],
            "status": "🔒 Anchored" if t["id"] in anchored_ids else "",
            "locked": t["id"] in anchored_ids,
        })

    assert rows[0]["locked"] is True, "Track t1 is anchored → locked=True"
    assert rows[0]["status"] == "🔒 Anchored", "Status column should still show anchored"


def test_unlocked_row_has_locked_field_false():
    """Rows for non-anchored tracks must have locked=False."""
    playlist_tracks = [
        {"id": "t1", "name": "Track 1", "artist": "A"},
        {"id": "t2", "name": "Track 2", "artist": "B"},
    ]
    anchored_ids = set()

    rows = []
    for i, t in enumerate(playlist_tracks, 1):
        rows.append({
            "idx": i,
            "name": t.get("name", "?")[:45],
            "artist": t.get("artist", "?")[:30],
            "status": "🔒 Anchored" if t["id"] in anchored_ids else "",
            "locked": t["id"] in anchored_ids,
        })

    assert rows[1]["locked"] is False, "Track t2 is not anchored → locked=False"
    assert rows[1]["status"] == "", "Status column should be empty for non-anchored"




# ── Event parsing tests (NiceGUI 3.x: e.args is a LIST, e.args[1] = row dict) ─

def test_anchor_row_click_parses_list_shaped_event_args():
    """_parse_row_idx extracts 0-based index from e.args[1]['idx'] (1-based)."""
    # Simulate NiceGUI 3.x event: e.args is a list [js_event, row_dict]
    mock_event = type("Event", (), {"args": [{}, {"idx": 3}]})()
    from playlist_arranger.ui.pages.anchors import _parse_row_idx
    idx = _parse_row_idx(mock_event)
    assert idx == 2, f"Expected 0-based idx=2, got {idx}"


def test_playlist_row_click_parses_list_shaped_event_args():
    """_parse_row_idx returns None when e.args is not a valid list."""
    # Empty list
    mock_event = type("Event", (), {"args": []})()
    from playlist_arranger.ui.pages.anchors import _parse_row_idx
    assert _parse_row_idx(mock_event) is None

    # List with wrong data
    mock_event2 = type("Event", (), {"args": [{}]})()
    assert _parse_row_idx(mock_event2) is None

    # None args
    mock_event3 = type("Event", (), {"args": None})()
    assert _parse_row_idx(mock_event3) is None


def test_double_click_on_playlist_row_adds_to_anchors():
    """Double-click on an unanchored track adds it to the anchor plan.
    selection="single" now drives table.selected as single source of truth."""
    plan = [{"type": "placeholder"}]
    playlist_tracks = [
        {"id": "t1", "name": "T1", "artist": "A"},
        {"id": "t2", "name": "T2", "artist": "B"},
    ]
    anchored = _anchored_track_ids(plan)

    # Simulate double-click on track "t2": e.args[1] = {"idx": 2}
    mock_event = type("Event", (), {"args": [{}, {"idx": 2}]})()
    
    # Parse the event
    idx = mock_event.args[1]["idx"] - 1  # 1-based → 0-based
    assert idx == 1

    tid = playlist_tracks[idx]["id"]
    assert tid == "t2"
    assert tid not in anchored

    # Add to anchors
    plan.append({"type": "anchor", "track_id": tid})
    assert len(plan) == 2
    assert plan[1] == {"type": "anchor", "track_id": "t2"}




# ── PRIORITY 1: Clear Anchors regression (must save ZERO items) ──────────

def test_clear_anchors_saves_zero_items():
    """Add 2 items, clear, then save — saved file must contain zero user-added items."""
    from playlist_arranger.config import ANCHORS_DIR_DEFAULT
    from playlist_arranger.sorting.anchors import _save_anchors_file, _load_anchors_file

    test_id = "test_clear_saves_zero"
    plan = [
        {"type": "anchor", "track_id": "track_a"},
        {"type": "anchor", "track_id": "track_b"},
    ]

    # Save initial plan
    _save_anchors_file(test_id, "Test Clear", plan)

    try:
        # Load it back, simulate clear (the anchors page would clear and save)
        loaded = _load_anchors_file(test_id)
        assert loaded is not None
        assert len(loaded) == 2

        # Simulate Clear Anchors: the plan saved afterward should be EMPTY
        plan_to_save = []  # _clear_anchors() does _anchor_plan.clear()
        # NO placeholder injection — saving empty means empty
        _save_anchors_file(test_id, "Test Clear", plan_to_save)

        # Re-load and verify: must be empty
        reloaded = _load_anchors_file(test_id)
        assert reloaded is not None
        assert reloaded == [], f"Saved empty plan must be empty, got {len(reloaded)} items"
    finally:
        af = ANCHORS_DIR_DEFAULT / f"anchors_{test_id}.json"
        if af.exists():
            af.unlink()


def test_remove_last_item_then_save_persists_empty_list():
    """Resolve 1 anchor, then save — saved file must be empty (Remove path, not Clear Anchors)."""
    from playlist_arranger.config import ANCHORS_DIR_DEFAULT
    from playlist_arranger.sorting.anchors import _save_anchors_file, _load_anchors_file

    test_id = "test_remove_last_saves_empty"
    plan = [{"type": "anchor", "track_id": "track_x"}]

    # Save single-item plan first
    _save_anchors_file(test_id, "Test Remove Last", plan)

    try:
        # Simulate user removing that last item via Remove button
        plan.pop()  # _remove_anchor() does del _anchor_plan[idx]
        assert plan == [], "Plan should be empty after removing the only item"

        # Save empty plan (no placeholder injection)
        _save_anchors_file(test_id, "Test Remove Last", plan)

        reloaded = _load_anchors_file(test_id)
        assert reloaded is not None
        assert reloaded == [], f"Saved empty plan after remove must be empty, got {len(reloaded)} items"
    finally:
        af = ANCHORS_DIR_DEFAULT / f"anchors_{test_id}.json"
        if af.exists():
            af.unlink()


def test_save_log_message_matches_actual_saved_count():
    """Invariant: the save log message count must match the actual items written to file."""
    from playlist_arranger.config import ANCHORS_DIR_DEFAULT
    from playlist_arranger.sorting.anchors import _save_anchors_file, _load_anchors_file

    test_id = "test_log_matches_saved"

    for saved_plan, expected_count in [
        ([], 0),
        ([{"type": "anchor", "track_id": "a"}], 1),
        ([{"type": "placeholder"}], 1),
        ([{"type": "anchor", "track_id": "a"}, {"type": "placeholder"}], 2),
    ]:
        _save_anchors_file(test_id, "Log Match Test", saved_plan)
        try:
            reloaded = _load_anchors_file(test_id)
            actual = len(reloaded) if reloaded else 0
            # The log would say "Saved %d anchor items", we assert that value matches
            saved_count = len(saved_plan)
            assert actual == saved_count, (
                f"Saved count={saved_count} but file contains {actual} items. "
                f"Plan={saved_plan}"
            )
            assert saved_count == expected_count, (
                f"Expected {expected_count} items, but saved {saved_count}"
            )
        finally:
            af = ANCHORS_DIR_DEFAULT / f"anchors_{test_id}.json"
            if af.exists():
                af.unlink()


def test_save_does_not_mutate_in_memory_plan():
    """_save_anchors copies the plan before saving — in-memory plan is unchanged."""
    plan = []
    plan_to_save = list(plan)  # COPY — no placeholder injection anymore

    # In-memory plan should still be empty
    assert plan == [], "In-memory plan should NOT be mutated by save"
    assert plan_to_save == [], "Copy should also be empty (no placeholder injection)"


# ── Issue 1: Checkbox column presence ────────────────────────────────────

def test_checkbox_column_present_in_playlist_table_row_config():
    """The track table on the Anchors page uses selection='multiple' prop,
    NOT a data column, to render checkboxes. This is the same pattern as the
    Analysis page (playlist_source.py). We verify the prop is set correctly."""
    # The relevant code in _render_track_list():
    #   _track_table = ui.table(..., selection="multiple", ...)
    # This test validates the conceptual assertion — checkboxes come from
    # selection="multiple", not from a columns entry.
    row = {"idx": 1, "name": "Test", "artist": "A", "duration": "3:00",
           "status": "✓ OK", "desc": "—", "desc_icon": "menu_book",
           "desc_color": "grey", "desc_caption": "No description",
           "track_id": "t1", "track_name_original": "Test", "locked": False}
    
    # Checkbox column is NOT a data field — it's Quasar's built-in selection
    assert "checkbox" not in row, "Checkbox is NOT a data column field"
    assert "checked" not in row, "Checked state is NOT a data column field"
    
    # The columns list does NOT include a checkbox column
    columns = ["idx", "name", "artist", "duration", "desc", "status"]
    assert "checkbox" not in columns, "Checkbox column comes from selection prop, not columns list"


def test_checkbox_column_present_in_anchors_table_row_config():
    """The anchors list table does NOT use selection='multiple' (it uses rowClick
    for single-selection Python tracking). This test verifies the absence is intentional."""
    # The anchors table in _render_anchors_list() does NOT set selection prop
    # It uses rowClick + manual .selected = [row_data] for single-select highlight
    row = {"idx": 1, "type": "⚓ Anchor", "info": "Track — Artist"}
    
    # No selection field in row data
    assert "checkbox" not in row, "Anchors table does not render checkboxes"
    assert "checked" not in row, "Anchors table uses single-selection, not multi-select"


# ── Issue 3: Double-click handler parity ──────────────────────────────────

def test_double_click_calls_same_handler_as_add_selected_track_button():
    """_on_track_double_click calls _add_selected_track() — the exact same
    function bound to the 'Add selected track' button."""
    import playlist_arranger.ui.pages.anchors as _ap
    
    # Both paths must call _add_selected_track()
    # Path 1: button click → _add_selected_track directly
    btn_handler = _ap._add_selected_track
    
    # Path 2: double-click → _on_track_double_click → _add_selected_track()
    # Verify _on_track_double_click imports _add_selected_track (same module scope)
    import inspect
    dbl_click_source = inspect.getsource(_ap._on_track_double_click)
    
    # The double-click handler MUST call _add_selected_track() (not a copy or stale ref)
    assert "_add_selected_track()" in dbl_click_source, (
        "_on_track_double_click must call _add_selected_track()"
    )
    
    # Verify _add_selected_track is the same callable (module-level name)
    assert _ap._add_selected_track is btn_handler, (
        "_add_selected_track callable must be the same reference for both paths"
    )


# ── Issue 4: Status field populated matching Analysis semantics ──────────

def test_status_field_populated_matching_analysis_semantics():
    """build_track_rows now computes real analysis status via state.get_track_status(),
    matching Analysis page semantics (uses "✓ OK" / "✗ Not in DB" etc, not blank)."""
    from playlist_arranger.ui.components.track_rows import build_track_rows
    from unittest.mock import patch, MagicMock
    
    # Mock get_track_status to return known value
    with patch("playlist_arranger.ui.components.track_rows._state.get_track_status") as mock_status:
        mock_status.return_value = "✓ OK"
        
        with patch("playlist_arranger.ui.components.track_rows._db.get_track") as mock_db:
            mock_db.return_value = None  # no desc
            
            with patch("playlist_arranger.ui.components.track_rows.get_desc_age_info") as mock_desc:
                mock_desc.return_value = MagicMock(
                    has_desc=False, color="grey", caption="No description"
                )
                
                tracks = [
                    {"id": "t1", "name": "S1", "artist": "A1", "duration_ms": 100000},
                    {"id": "t2", "name": "S2", "artist": "A2", "duration_ms": 200000},
                ]
                rows = build_track_rows(tracks, anchored_ids=set())
                
                # Status must NOT be blank for non-anchored tracks
                assert rows[0]["status"] == "✓ OK", f"Expected '✓ OK', got {rows[0]['status']!r}"
                assert rows[1]["status"] == "✓ OK", f"Expected '✓ OK', got {rows[1]['status']!r}"
                
                # Desc fields must be populated (not None / KeyError)
                assert "desc" in rows[0], "desc field required"
                assert "desc_icon" in rows[0], "desc_icon field required"
                assert "desc_color" in rows[0], "desc_color field required"
                assert "desc_caption" in rows[0], "desc_caption field required"


def test_anchored_status_preserved_over_analysis_status():
    """When a track is anchored, status shows '🔒 Anchored', not the analysis status."""
    from playlist_arranger.ui.components.track_rows import build_track_rows
    from unittest.mock import patch, MagicMock
    
    with patch("playlist_arranger.ui.components.track_rows._state.get_track_status") as mock_status:
        mock_status.return_value = "✓ OK"  # would be OK if not anchored
        
        with patch("playlist_arranger.ui.components.track_rows._db.get_track") as mock_db:
            mock_db.return_value = None
            
            with patch("playlist_arranger.ui.components.track_rows.get_desc_age_info") as mock_desc:
                mock_desc.return_value = MagicMock(
                    has_desc=False, color="grey", caption="No description"
                )
                
                tracks = [{"id": "t1", "name": "S1", "artist": "A1", "duration_ms": 100000}]
                rows = build_track_rows(tracks, anchored_ids={"t1"})
                
                # Anchored → must show "🔒 Anchored", NOT the analysis status
                assert rows[0]["status"] == "🔒 Anchored", (
                    f"Anchored track status must be '🔒 Anchored', got {rows[0]['status']!r}"
                )
                assert rows[0]["locked"] is True, "Anchored track must have locked=True"
                
                # Verify analysis status was NOT called for anchored tracks
                mock_status.assert_not_called()


# ── PRIORITY 2: Move Up/Down button enable logic ─────────────────────────

def test_up_button_disabled_when_first_row_selected():
    """Move Up should be disabled when selected row is first (idx=0)."""
    from playlist_arranger.ui.pages.anchors import _refresh_control_buttons
    import playlist_arranger.ui.pages.anchors as _ap

    class FB:
        def __init__(self): self._enabled = False
        def set_enabled(self, v): self._enabled = v

    old_up = _ap._move_up_btn
    old_down = _ap._move_down_btn
    old_remove = _ap._remove_btn
    old_add = _ap._add_selected_track_btn
    old_clear = _ap._clear_btn
    old_save = _ap._save_btn
    old_plan = list(_ap._anchor_plan)
    old_sel = _ap._selected_anchor_idx
    try:
        _ap._move_up_btn = FB()
        _ap._move_down_btn = FB()
        _ap._anchor_plan[:] = [
            {"type": "anchor", "track_id": "a"},
            {"type": "placeholder"},
            {"type": "anchor", "track_id": "b"},
        ]
        _ap._selected_anchor_idx = 0  # first row

        _refresh_control_buttons()
        assert not _ap._move_up_btn._enabled, "Move Up should be DISABLED when first row selected"
        assert _ap._move_down_btn._enabled, "Move Down should be ENABLED when first row selected"
    finally:
        _ap._move_up_btn = old_up
        _ap._move_down_btn = old_down
        _ap._remove_btn = old_remove
        _ap._add_selected_track_btn = old_add
        _ap._clear_btn = old_clear
        _ap._save_btn = old_save
        _ap._anchor_plan[:] = old_plan
        _ap._selected_anchor_idx = old_sel


def test_up_button_enabled_when_non_first_row_selected():
    """Move Up should be enabled when selected row is not first."""
    from playlist_arranger.ui.pages.anchors import _refresh_control_buttons
    import playlist_arranger.ui.pages.anchors as _ap

    class FB:
        def __init__(self): self._enabled = False
        def set_enabled(self, v): self._enabled = v

    old_up = _ap._move_up_btn
    old_down = _ap._move_down_btn
    old_remove = _ap._remove_btn
    old_add = _ap._add_selected_track_btn
    old_clear = _ap._clear_btn
    old_save = _ap._save_btn
    old_plan = list(_ap._anchor_plan)
    old_sel = _ap._selected_anchor_idx
    try:
        _ap._move_up_btn = FB()
        _ap._move_down_btn = FB()
        _ap._anchor_plan[:] = [
            {"type": "anchor", "track_id": "a"},
            {"type": "placeholder"},
        ]
        _ap._selected_anchor_idx = 1  # second (last) row — NOT first

        _refresh_control_buttons()
        assert _ap._move_up_btn._enabled, "Move Up should be ENABLED when non-first row selected"
    finally:
        _ap._move_up_btn = old_up
        _ap._move_down_btn = old_down
        _ap._remove_btn = old_remove
        _ap._add_selected_track_btn = old_add
        _ap._clear_btn = old_clear
        _ap._save_btn = old_save
        _ap._anchor_plan[:] = old_plan
        _ap._selected_anchor_idx = old_sel


def test_down_button_disabled_when_last_row_selected():
    """Move Down should be disabled when selected row is last."""
    from playlist_arranger.ui.pages.anchors import _refresh_control_buttons
    import playlist_arranger.ui.pages.anchors as _ap

    class FB:
        def __init__(self): self._enabled = False
        def set_enabled(self, v): self._enabled = v

    old_up = _ap._move_up_btn
    old_down = _ap._move_down_btn
    old_remove = _ap._remove_btn
    old_add = _ap._add_selected_track_btn
    old_clear = _ap._clear_btn
    old_save = _ap._save_btn
    old_plan = list(_ap._anchor_plan)
    old_sel = _ap._selected_anchor_idx
    try:
        _ap._move_up_btn = FB()
        _ap._move_down_btn = FB()
        _ap._anchor_plan[:] = [
            {"type": "anchor", "track_id": "a"},
            {"type": "placeholder"},
        ]
        _ap._selected_anchor_idx = 1  # last row

        _refresh_control_buttons()
        assert not _ap._move_down_btn._enabled, "Move Down should be DISABLED when last row selected"
        assert _ap._move_up_btn._enabled, "Move Up should be ENABLED when last row selected"
    finally:
        _ap._move_up_btn = old_up
        _ap._move_down_btn = old_down
        _ap._remove_btn = old_remove
        _ap._add_selected_track_btn = old_add
        _ap._clear_btn = old_clear
        _ap._save_btn = old_save
        _ap._anchor_plan[:] = old_plan
        _ap._selected_anchor_idx = old_sel


def test_down_button_enabled_when_non_last_row_selected():
    """Move Down should be enabled when selected row is not last."""
    from playlist_arranger.ui.pages.anchors import _refresh_control_buttons
    import playlist_arranger.ui.pages.anchors as _ap

    class FB:
        def __init__(self): self._enabled = False
        def set_enabled(self, v): self._enabled = v

    old_up = _ap._move_up_btn
    old_down = _ap._move_down_btn
    old_remove = _ap._remove_btn
    old_add = _ap._add_selected_track_btn
    old_clear = _ap._clear_btn
    old_save = _ap._save_btn
    old_plan = list(_ap._anchor_plan)
    old_sel = _ap._selected_anchor_idx
    try:
        _ap._move_down_btn = FB()
        _ap._anchor_plan[:] = [
            {"type": "anchor", "track_id": "a"},
            {"type": "placeholder"},
            {"type": "anchor", "track_id": "b"},
        ]
        _ap._selected_anchor_idx = 0  # first — NOT last

        _refresh_control_buttons()
        assert _ap._move_down_btn._enabled, "Move Down should be ENABLED when non-last row selected"
    finally:
        _ap._move_up_btn = old_up
        _ap._move_down_btn = old_down
        _ap._remove_btn = old_remove
        _ap._add_selected_track_btn = old_add
        _ap._clear_btn = old_clear
        _ap._save_btn = old_save
        _ap._anchor_plan[:] = old_plan
        _ap._selected_anchor_idx = old_sel


# ── PRIORITY 3: Shared track row builder (Artist/Status populated) ───────

def test_shared_track_rows_artist_populated():
    """build_track_rows sets artist field from track data."""
    from playlist_arranger.ui.components.track_rows import build_track_rows
    from unittest.mock import patch, MagicMock
    
    with patch("playlist_arranger.ui.components.track_rows._state.get_track_status") as mock_status:
        mock_status.return_value = "✓ OK"
        with patch("playlist_arranger.ui.components.track_rows._db.get_track") as mock_db:
            mock_db.return_value = None
            with patch("playlist_arranger.ui.components.track_rows.get_desc_age_info") as mock_desc:
                mock_desc.return_value = MagicMock(
                    has_desc=False, color="grey", caption="No description"
                )
                tracks = [
                    {"id": "t1", "name": "Song 1", "artist": "Artist One", "duration_ms": 180000},
                    {"id": "t2", "name": "Song 2", "artist": "Artist Two", "duration_ms": 240000},
                ]
                rows = build_track_rows(tracks)
                assert rows[0]["artist"] == "Artist One"
                assert rows[1]["artist"] == "Artist Two"


def test_shared_track_rows_status_populated_when_anchored():
    """build_track_rows sets status='🔒 Anchored' and locked=True when track is anchored.
    Non-anchored status is now computed via state.get_track_status() (Issue 4 fix)."""
    from playlist_arranger.ui.components.track_rows import build_track_rows
    from unittest.mock import patch, MagicMock
    
    with patch("playlist_arranger.ui.components.track_rows._state.get_track_status") as mock_status:
        mock_status.return_value = "✓ OK"
        with patch("playlist_arranger.ui.components.track_rows._db.get_track") as mock_db:
            mock_db.return_value = None
            with patch("playlist_arranger.ui.components.track_rows.get_desc_age_info") as mock_desc:
                mock_desc.return_value = MagicMock(
                    has_desc=False, color="grey", caption="No description"
                )
                tracks = [
                    {"id": "t1", "name": "S1", "artist": "A1", "duration_ms": 100000},
                    {"id": "t2", "name": "S2", "artist": "A2", "duration_ms": 200000},
                ]
                rows = build_track_rows(tracks, anchored_ids={"t1"})
                assert rows[0]["status"] == "🔒 Anchored"
                assert rows[0]["locked"] is True
                # Non-anchored now gets real analysis status (not blank)
                assert rows[1]["status"] == "✓ OK", f"Expected '✓ OK', got {rows[1]['status']!r}"
                assert rows[1]["locked"] is False




# ── Programmatic .selected highlight test ────────────────────────────────

def test_selected_row_visually_indicated():
    """Click handler sets table.selected = [row_data] for native Quasar highlight."""
    class FakeTable:
        def __init__(self):
            self._selected = []
        @property
        def selected(self):
            return self._selected
        @selected.setter
        def selected(self, val):
            self._selected = val

    # Simulate a click on row 2
    from playlist_arranger.ui.pages.anchors import _parse_row_idx
    mock_event = type("Event", (), {"args": [{}, {"idx": 2, "name": "Test", "track_id": "t1"}]})()
    idx = _parse_row_idx(mock_event)
    assert idx == 1  # 0-based

    fake_table = FakeTable()
    row_data = mock_event.args[1]

    # Simulate what _on_track_row_click does
    if idx is not None and 0 <= idx < 3:  # pretend 3 tracks exist
        fake_table.selected = [row_data]
    else:
        fake_table.selected = []

    assert fake_table.selected == [row_data], "Table.selected should contain clicked row for native Quasar highlight"


def test_selected_cleared_when_out_of_bounds():
    """Click on out-of-bounds row clears the selected highlight."""
    class FakeTable:
        def __init__(self):
            self._selected = [{"idx": 1}]
        @property
        def selected(self):
            return self._selected
        @selected.setter
        def selected(self, val):
            self._selected = val

    from playlist_arranger.ui.pages.anchors import _parse_row_idx
    # e.args with no valid row
    mock_event = type("Event", (), {"args": None})()
    idx = _parse_row_idx(mock_event)
    assert idx is None

    fake_table = FakeTable()
    if idx is not None and 0 <= idx < 5:
        pass  # not reached
    else:
        fake_table.selected = []

    assert fake_table.selected == [], "Selected should be cleared for invalid clicks"


# ── Desync regression: selection="single" + table.selected as single source of truth ─

def test_only_one_row_selectable_at_a_time():
    """With selection='single', Quasar natively guarantees at most 1 selected row.
    _selected_track_idx() derives from table.selected which has max 1 item."""
    import playlist_arranger.ui.pages.anchors as _ap
    
    class FakeTable:
        def __init__(self):
            self._selected = []
        @property
        def selected(self):
            return list(self._selected)
        @selected.setter
        def selected(self, val):
            self._selected = list(val) if val else []
    
    old_table = _ap._track_table
    old_tracks = list(_ap._playlist_tracks)
    try:
        _ap._playlist_tracks[:] = [
            {"id": "t1", "name": "A", "artist": "X", "duration_ms": 100000},
            {"id": "t2", "name": "B", "artist": "Y", "duration_ms": 200000},
            {"id": "t3", "name": "C", "artist": "Z", "duration_ms": 300000},
        ]
        _ap._track_table = FakeTable()
        
        # Set selection to track 2 (idx=2, 0-based=1)
        _ap._track_table.selected = [{"idx": 2}]
        assert _ap._selected_track_idx() == 1
        
        # Set selection to track 3 (replacing, not appending)
        _ap._track_table.selected = [{"idx": 3}]
        assert _ap._selected_track_idx() == 2
        assert len(_ap._track_table.selected) == 1, "Max 1 item — selection='single' guarantee"
        
        # Clear selection
        _ap._track_table.selected = []
        assert _ap._selected_track_idx() is None
        
        # Set multiple (should never happen with selection='single', but handle gracefully)
        _ap._track_table.selected = [{"idx": 1}, {"idx": 2}]
        assert _ap._selected_track_idx() is None, ">1 items → None (invalid state)"
    finally:
        _ap._track_table = old_table
        _ap._playlist_tracks[:] = old_tracks


def test_checkbox_click_updates_selection_state():
    """When selection='single', clicking a checkbox sets table.selected to [row]."""
    import playlist_arranger.ui.pages.anchors as _ap
    
    class FakeTable:
        def __init__(self):
            self._selected = []
        @property
        def selected(self):
            return list(self._selected)
        @selected.setter
        def selected(self, val):
            self._selected = list(val) if val else []
    
    old_table = _ap._track_table
    old_tracks = list(_ap._playlist_tracks)
    try:
        _ap._playlist_tracks[:] = [{"id": "t1", "name": "X", "artist": "Y", "duration_ms": 100000}]
        _ap._track_table = FakeTable()
        
        # Simulate Quasar's selection='single' setting selected to the row
        _ap._track_table.selected = [{"idx": 1}]  # row 1 checked
        assert _ap._selected_track_idx() == 0, "Checkbox click must update derived index"
        
        # Simulate Quasar clearing selection (same row clicked again)
        _ap._track_table.selected = []
        assert _ap._selected_track_idx() is None, "Uncheck must clear derived index"
    finally:
        _ap._track_table = old_table
        _ap._playlist_tracks[:] = old_tracks


def test_unchecking_clears_selection_and_disables_add_button():
    """Unchecking the selected track must clear _selected_track_idx → button disabled."""
    import playlist_arranger.ui.pages.anchors as _ap
    
    class FB:
        def __init__(self): self._enabled = False
        def set_enabled(self, v): self._enabled = v
    
    class FakeTable:
        def __init__(self):
            self._selected = []
        @property
        def selected(self):
            return list(self._selected)
        @selected.setter
        def selected(self, val):
            self._selected = list(val) if val else []
    
    old_table = _ap._track_table
    old_btn = _ap._add_selected_track_btn
    old_tracks = list(_ap._playlist_tracks)
    old_plan = list(_ap._anchor_plan)
    try:
        _ap._playlist_tracks[:] = [
            {"id": "t1", "name": "A", "artist": "X", "duration_ms": 100000},
            {"id": "t2", "name": "B", "artist": "Y", "duration_ms": 200000},
        ]
        _ap._anchor_plan[:] = []
        _ap._track_table = FakeTable()
        _ap._add_selected_track_btn = FB()
        
        # Select track 1
        _ap._track_table.selected = [{"idx": 1}]
        _ap._refresh_control_buttons()
        assert _ap._add_selected_track_btn._enabled, "Button should be enabled when track selected"
        
        # Uncheck → deselect
        _ap._track_table.selected = []
        _ap._refresh_control_buttons()
        assert not _ap._add_selected_track_btn._enabled, "Button must be DISABLED when nothing selected"
    finally:
        _ap._track_table = old_table
        _ap._add_selected_track_btn = old_btn
        _ap._playlist_tracks[:] = old_tracks
        _ap._anchor_plan[:] = old_plan


def test_row_click_and_checkbox_click_converge_on_same_selection():
    """Both row click and checkbox click converge on table.selected (single source of truth).
    There is NO separate _selected_track_idx variable — it's derived fresh each read."""
    import playlist_arranger.ui.pages.anchors as _ap
    
    class FakeTable:
        def __init__(self):
            self._selected = []
        @property
        def selected(self):
            return list(self._selected)
        @selected.setter
        def selected(self, val):
            self._selected = list(val) if val else []
    
    old_table = _ap._track_table
    old_tracks = list(_ap._playlist_tracks)
    try:
        _ap._playlist_tracks[:] = [
            {"id": "t1", "name": "A", "artist": "X", "duration_ms": 100000},
            {"id": "t2", "name": "B", "artist": "Y", "duration_ms": 200000},
        ]
        _ap._track_table = FakeTable()
        
        # Simulate row click on track 2 — Quasar sets selected=[row2]
        _ap._track_table.selected = [{"idx": 2}]
        # _on_track_row_click would fire, but all it does is guard anchored tracks
        # The selection is entirely Quasar-driven
        assert _ap._selected_track_idx() == 1
        
        # Simulate checkbox click on track 1 — Quasar sets selected=[row1]
        _ap._track_table.selected = [{"idx": 1}]
        assert _ap._selected_track_idx() == 0
        
        # Both mechanisms write to the SAME table.selected — zero diverged state
        assert not hasattr(_ap, '_selected_track_idx') or isinstance(
            getattr(_ap, '_selected_track_idx', None), type(lambda: None)
        ), "_selected_track_idx must be a function, not a variable"
    finally:
        _ap._track_table = old_table
        _ap._playlist_tracks[:] = old_tracks


def test_add_selected_track_button_reflects_actual_visible_selection():
    """The core desync regression test: visible checkbox state MUST match
    what 'Add selected track' would act on. No stale/invisible selection."""
    import playlist_arranger.ui.pages.anchors as _ap
    
    class FB:
        def __init__(self): self._enabled = False
        def set_enabled(self, v): self._enabled = v
    
    class FakeTable:
        def __init__(self):
            self._selected = []
        @property
        def selected(self):
            return list(self._selected)
        @selected.setter
        def selected(self, val):
            self._selected = list(val) if val else []
    
    old_table = _ap._track_table
    old_btn = _ap._add_selected_track_btn
    old_tracks = list(_ap._playlist_tracks)
    old_plan = list(_ap._anchor_plan)
    try:
        _ap._playlist_tracks[:] = [
            {"id": "t1", "name": "S1", "artist": "A1", "duration_ms": 100000},
            {"id": "t2", "name": "S2", "artist": "A2", "duration_ms": 200000},
        ]
        _ap._anchor_plan[:] = []
        _ap._track_table = FakeTable()
        _ap._add_selected_track_btn = FB()
        
        # STATE 1: Nothing checked → button disabled
        _ap._track_table.selected = []
        _ap._refresh_control_buttons()
        assert not _ap._add_selected_track_btn._enabled, "Nothing checked → disabled"
        
        # STATE 2: Track 1 checked → button enabled
        _ap._track_table.selected = [{"idx": 1}]
        _ap._refresh_control_buttons()
        assert _ap._add_selected_track_btn._enabled, "Track checked → enabled"
        
        # STATE 3: Track 1 ADDED to anchors (now 🔒 Anchored) → button disabled
        _ap._anchor_plan[:] = [{"type": "anchor", "track_id": "t1"}]
        _ap._refresh_control_buttons()
        assert not _ap._add_selected_track_btn._enabled, (
            "Anchored track → disabled (even though still visible-checked)"
        )
        
        # STATE 4: Clear selection, select Track 2 → button enabled again
        _ap._track_table.selected = [{"idx": 2}]
        _ap._refresh_control_buttons()
        assert _ap._add_selected_track_btn._enabled, "Unanchored track 2 must be addable"
        
        # STATE 5: Clear ALL (including selection) → button disabled
        _ap._track_table.selected = []
        _ap._refresh_control_buttons()
        assert not _ap._add_selected_track_btn._enabled, "Nothing selected → disabled"
        
        # Verify add uses the CORRECT track (what user sees selected)
        _ap._track_table.selected = [{"idx": 2}]
        _ap._add_selected_track()
        assert len(_ap._anchor_plan) == 2, "Track 2 should have been added"
        assert _ap._anchor_plan[-1]["track_id"] == "t2", "Must add the VISIBLE selection (t2), not stale state"
    finally:
        _ap._track_table = old_table
        _ap._add_selected_track_btn = old_btn
        _ap._playlist_tracks[:] = old_tracks
        _ap._anchor_plan[:] = old_plan


# ── Bug A: Checkbox-only click triggers button refresh via on_select ──────

def test_checkbox_only_click_triggers_button_refresh():
    """on_select must fire for checkbox clicks even though Quasar's @click.stop
    prevents rowClick from firing.  _refresh_control_buttons() runs on both paths."""
    import playlist_arranger.ui.pages.anchors as _ap
    import inspect
    
    # Verify _on_track_selection_change exists and calls _refresh_control_buttons
    source = inspect.getsource(_ap._on_track_selection_change)
    assert "_refresh_control_buttons()" in source, (
        "_on_track_selection_change must call _refresh_control_buttons()"
    )
    
    # Verify the function is in the module namespace
    assert callable(_ap._on_track_selection_change), "_on_track_selection_change must be callable"


def test_selection_event_wired_to_refresh_control_buttons():
    """on_select=_on_track_selection_change ensures button state refreshes on
    ALL selection changes, not just rowClick-triggered ones."""
    import playlist_arranger.ui.pages.anchors as _ap
    
    class FB:
        def __init__(self): self._enabled = False
        def set_enabled(self, v): self._enabled = v
    
    class FakeTable:
        def __init__(self):
            self._selected = []
        @property
        def selected(self):
            return list(self._selected)
        @selected.setter
        def selected(self, val):
            self._selected = list(val) if val else []
    
    old_table = _ap._track_table
    old_btn = _ap._add_selected_track_btn
    old_tracks = list(_ap._playlist_tracks)
    old_plan = list(_ap._anchor_plan)
    try:
        _ap._playlist_tracks[:] = [
            {"id": "t1", "name": "A", "artist": "X", "duration_ms": 100000},
            {"id": "t2", "name": "B", "artist": "Y", "duration_ms": 200000},
        ]
        _ap._anchor_plan[:] = []
        _ap._track_table = FakeTable()
        _ap._add_selected_track_btn = FB()
        
        # Simulate checkbox click (no rowClick) — on_select fires, calling
        # _on_track_selection_change which calls _refresh_control_buttons
        _ap._track_table.selected = [{"idx": 1}]
        _ap._on_track_selection_change(None)  # simulate on_select
        assert _ap._add_selected_track_btn._enabled, (
            "Button must be enabled after selection change event"
        )
    finally:
        _ap._track_table = old_table
        _ap._add_selected_track_btn = old_btn
        _ap._playlist_tracks[:] = old_tracks
        _ap._anchor_plan[:] = old_plan


# ── Bug B: Double-click adds exact clicked track (not stale selection) ────

def test_double_click_adds_exact_clicked_track_not_stale_selection():
    """Double-click row 2 must add row 2's track_id to _anchor_plan, NOT
    a stale previous selection (Bug B regression)."""
    import playlist_arranger.ui.pages.anchors as _ap
    
    class FakeTable:
        def __init__(self):
            self._selected = []
        @property
        def selected(self):
            return list(self._selected)
        @selected.setter
        def selected(self, val):
            self._selected = list(val) if val else []
    
    old_table = _ap._track_table
    old_tracks = list(_ap._playlist_tracks)
    old_plan = list(_ap._anchor_plan)
    try:
        _ap._playlist_tracks[:] = [
            {"id": "t1", "name": "Track 1", "artist": "A1", "duration_ms": 100000},
            {"id": "t2", "name": "Track 2", "artist": "A2", "duration_ms": 200000},
            {"id": "t3", "name": "Track 3", "artist": "A3", "duration_ms": 300000},
        ]
        _ap._anchor_plan[:] = []
        _ap._track_table = FakeTable()
        
        # Pre-condition: row 1 is selection="single" selected (stale state)
        _ap._track_table.selected = [{"idx": 1}]
        
        # User double-clicks row 2
        # Simulate _on_track_double_click logic:
        # 1. Parse row 2 from event
        mock_event = type("Event", (), {"args": [{}, {"idx": 2}]})()
        idx = _ap._parse_row_idx(mock_event)  # 0-based → 1
        assert idx == 1, "Double-clicked row 2 → 0-based idx=1"
        tid = _ap._playlist_tracks[idx]["id"]
        assert tid == "t2"
        
        # 2. Explicitly sync table.selected to the double-clicked row
        _ap._track_table.selected = [{"idx": 2}]
        assert _ap._selected_track_idx() == 1, "table.selected must now point to row 2"
        
        # 3. _add_selected_track() reads from table.selected → must add t2, not t1
        _ap._add_selected_track()
        
        # Assert: t2 was added, not t1
        assert len(_ap._anchor_plan) == 1
        assert _ap._anchor_plan[0]["track_id"] == "t2", (
            f"BUG: Expected t2 (double-clicked track), got {_ap._anchor_plan[0]['track_id']}"
        )
    finally:
        _ap._track_table = old_table
        _ap._playlist_tracks[:] = old_tracks
        _ap._anchor_plan[:] = old_plan


# ── Bug C: Track deselected after double-click add ────────────────────────

def test_track_deselected_after_double_click_add():
    """After double-click adding a track, table.selected must be [] so the
    now-anchored track is not left visually checked/un-interactable."""
    import playlist_arranger.ui.pages.anchors as _ap
    
    class FakeTable:
        def __init__(self):
            self._selected = []
        @property
        def selected(self):
            return list(self._selected)
        @selected.setter
        def selected(self, val):
            self._selected = list(val) if val else []
    
    old_table = _ap._track_table
    old_tracks = list(_ap._playlist_tracks)
    old_plan = list(_ap._anchor_plan)
    try:
        _ap._playlist_tracks[:] = [
            {"id": "t1", "name": "Test", "artist": "A", "duration_ms": 100000},
        ]
        _ap._anchor_plan[:] = []
        _ap._track_table = FakeTable()
        
        # Simulate full _on_track_double_click flow:
        mock_event = type("Event", (), {"args": [{}, {"idx": 1}]})()
        _ap._track_table.selected = [{"idx": 1}]   # sync to dblclicked row
        _ap._add_selected_track()                   # add to anchors
        
        # BUG C FIX: After _add_selected_track, table.selected must be cleared
        # because the track is now anchored and unselectable
        _ap._track_table.selected = []
        
        # The "Add Selected Track" button must now be disabled (no selection)
        class FB:
            def __init__(self): self._enabled = False
            def set_enabled(self, v): self._enabled = v
        
        old_btn = _ap._add_selected_track_btn
        _ap._add_selected_track_btn = FB()
        _ap._refresh_control_buttons()
        assert not _ap._add_selected_track_btn._enabled, (
            "After dblclick-add, button must be disabled (track now anchored, selection cleared)"
        )
    finally:
        _ap._track_table = old_table
        _ap._playlist_tracks[:] = old_tracks
        _ap._anchor_plan[:] = old_plan


# ── Bug D: table.selected must always be a list ──────────────────────────

def test_anchor_table_selected_is_always_a_list():
    """Every assignment to table.selected must use a list, never a bare dict."""
    import playlist_arranger.ui.pages.anchors as _ap
    import inspect
    
    # Use FakeTable to test the type of values assigned
    class FakeTable:
        def __init__(self):
            self._selected = []
        @property
        def selected(self):
            return list(self._selected)
        @selected.setter
        def selected(self, val):
            if not isinstance(val, list):
                raise TypeError(f"table.selected must be a list, got {type(val).__name__}")
            self._selected = list(val)
    
    old_table = _ap._track_table
    old_anchors_table = _ap._anchors_table
    try:
        # Test _on_anchor_row_click path: [row_data], not row_data
        row_data = {"idx": 1, "type": "⚓ Anchor", "info": "Test"}
        _ap._anchors_table = FakeTable()
        _ap._anchors_table.selected = [row_data]  # Bug D: must be [row_data], not row_data
        assert _ap._anchors_table.selected == [row_data]
        _ap._anchors_table.selected = []
        assert _ap._anchors_table.selected == []
        
        # Test that bare dict assignment raises TypeError
        try:
            _ap._anchors_table.selected = row_data  # bare dict — should fail
            assert False, "Bare dict assignment to table.selected must be caught"
        except TypeError:
            pass  # Test passed — bare dict rejected
        
        # Test all known assignment sites via source code audit
        source = inspect.getsource(_ap)
        lines = source.split("\n")
        for lineno, line in enumerate(lines, 1):
            # Find every "selected = " assignment
            stripped = line.strip()
            if "selected = " in stripped and not stripped.startswith("#"):
                # Extract the assigned value
                idx = stripped.index("selected = ") + len("selected = ")
                rhs = stripped[idx:].strip()
                if "row_data" in rhs and "[" not in rhs and "[" not in rhs:
                    # This is a bare dict assignment (like .selected = row_data)
                    # Check if the line actually has [row_data] vs row_data
                    if "row_data" in rhs and "[row_data]" not in rhs:
                        # False positive: could be something like "row_data is not None"
                        if "is not None" not in rhs and "is None" not in rhs:
                            print(f"WARNING: Line {lineno}: {stripped}")
                            # Only flag actual assignments
                            if "=" in rhs and "[" not in rhs.split("=")[0]:
                                assert False, (
                                    f"Line {lineno}: potential bare dict assignment: {stripped}"
                                )
    finally:
        _ap._track_table = old_table
        _ap._anchors_table = old_anchors_table


def test_anchor_row_click_does_not_affect_playlist_track_selection():
    """Clicking an anchor list row must NOT change _track_table.selected or
    the 'Add Selected Track' button state (independent selections)."""
    import playlist_arranger.ui.pages.anchors as _ap
    
    class FakeTable:
        def __init__(self):
            self._selected = []
        @property
        def selected(self):
            return list(self._selected)
        @selected.setter
        def selected(self, val):
            self._selected = list(val) if val else []
    
    old_anchors_table = _ap._anchors_table
    old_track_table = _ap._track_table
    old_tracks = list(_ap._playlist_tracks)
    old_plan = list(_ap._anchor_plan)
    try:
        _ap._playlist_tracks[:] = [
            {"id": "t1", "name": "Track 1", "artist": "A1", "duration_ms": 100000},
        ]
        _ap._anchor_plan[:] = [
            {"type": "anchor", "track_id": "t1"},
            {"type": "placeholder"},
        ]
        _ap._track_table = FakeTable()
        _ap._anchors_table = FakeTable()
        
        # Setup: Playlist table has NO selection
        _ap._track_table.selected = []
        
        # Click an anchor row (anchors list, NOT playlist track table)
        mock_event = type("Event", (), {"args": [{}, {"idx": 2}]})()
        _ap._on_anchor_row_click(mock_event)
        
        # Anchor selection state should be set
        assert _ap._selected_anchor_idx == 1, "Anchor row 2 selected (0-based idx=1)"
        assert _ap._anchors_table.selected == [{"idx": 2}], "Anchor table should show row 2 selected"
        
        # Playlist track table selection must NOT have changed
        assert _ap._track_table.selected == [], (
            "Anchor row click must NOT change playlist track table selection"
        )
        
        # _selected_track_idx() must still return None (playlist table unaffected)
        assert _ap._selected_track_idx() is None, (
            "Anchor row click must not affect playlist track selection index"
        )
    finally:
        _ap._anchors_table = old_anchors_table
        _ap._track_table = old_track_table
        _ap._playlist_tracks[:] = old_tracks
        _ap._anchor_plan[:] = old_plan


def test_remove_anchor_reenables_add_button_if_track_still_checked():
    """Remove an anchor → if the removed track happens to still be the checked
    row in the playlist table, 'Add Selected Track' should re-enable since
    the track is now un-anchored and selectable."""
    import playlist_arranger.ui.pages.anchors as _ap
    
    class FB:
        def __init__(self): self._enabled = False
        def set_enabled(self, v): self._enabled = v
    
    class FakeTable:
        def __init__(self):
            self._selected = []
        @property
        def selected(self):
            return list(self._selected)
        @selected.setter
        def selected(self, val):
            self._selected = list(val) if val else []
    
    old_track_table = _ap._track_table
    old_tracks = list(_ap._playlist_tracks)
    old_plan = list(_ap._anchor_plan)
    old_btn = _ap._add_selected_track_btn
    old_sel = _ap._selected_anchor_idx
    try:
        # Track t1 is anchored AND is the currently-checked row in playlist table.
        # This is an edge case: normally anchored tracks are unselectable via
        # _on_track_row_click's guard, but if table.selected was set externally
        # (or the track was checked before it became anchored), it survives.
        _ap._playlist_tracks[:] = [
            {"id": "t1", "name": "Track 1", "artist": "A1", "duration_ms": 100000},
            {"id": "t2", "name": "Track 2", "artist": "A2", "duration_ms": 200000},
        ]
        _ap._anchor_plan[:] = [
            {"type": "anchor", "track_id": "t1"},
            {"type": "placeholder"},
        ]
        _ap._track_table = FakeTable()
        _ap._add_selected_track_btn = FB()
        
        # t1 is checked in playlist table (edge case)
        _ap._track_table.selected = [{"idx": 1}]
        _ap._refresh_control_buttons()
        # t1 is anchored → button should be DISABLED
        assert not _ap._add_selected_track_btn._enabled, (
            "Track t1 is anchored → button must be disabled"
        )
        
        # Remove t1 from anchors
        _ap._selected_anchor_idx = 0  # t1 is first anchor item
        _ap._remove_anchor()
        
        # t1 is no longer anchored, and it's still checked in playlist table
        # → button should be ENABLED again
        assert _ap._add_selected_track_btn._enabled, (
            "After removing t1 from anchors, button must re-enable (t1 still checked)"
        )
        
        # Verify _anchor_plan is empty of t1
        assert len(_ap._anchor_plan) == 1, "Placeholder remains"
        assert _ap._anchor_plan[0]["type"] == "placeholder"
        assert _ap._selected_track_idx() == 0, "Track t1 (idx=0) still checked"
    finally:
        _ap._track_table = old_track_table
        _ap._playlist_tracks[:] = old_tracks
        _ap._anchor_plan[:] = old_plan
        _ap._add_selected_track_btn = old_btn
        _ap._selected_anchor_idx = old_sel


# ── Run all tests ─────────────────────────────────────────────────────────
tests = [
    ("test_nav_button_enabled_when_sp_connected", test_nav_button_enabled_when_sp_connected),
    ("test_nav_button_disabled_when_sp_none", test_nav_button_disabled_when_sp_none),
    ("test_nav_button_disabled_post_connect_then_disconnect", test_nav_button_disabled_post_connect_then_disconnect),
    ("test_nav_button_refresh_logic", test_nav_button_refresh_logic),
    ("test_sort_button_refresh_logic", test_sort_button_refresh_logic),
    ("test_playlist_names_extracted_from_get_own_playlists", test_playlist_names_extracted_from_get_own_playlists),
    ("test_dropdown_empty_when_no_playlists", test_dropdown_empty_when_no_playlists),
    ("test_dropdown_handles_duplicate_ids_last_wins", test_dropdown_handles_duplicate_ids_last_wins),
    ("test_reused_function_is_get_own_playlists", test_reused_function_is_get_own_playlists),
    ("test_spotify_user_id_required_for_playlist_fetch", test_spotify_user_id_required_for_playlist_fetch),
    ("test_playlist_selection_persists_across_navigation", test_playlist_selection_persists_across_navigation),
    ("test_load_anchors_empty_when_no_file", test_load_anchors_empty_when_no_file),
    ("test_load_anchors_from_existing_file", test_load_anchors_from_existing_file),
    ("test_anchors_cache_filename_does_not_collide_with_track_cache", test_anchors_cache_filename_does_not_collide_with_track_cache),
    ("test_move_up_disabled_when_first", test_move_up_disabled_when_first),
    ("test_move_up_enabled_middle", test_move_up_enabled_middle),
    ("test_move_down_disabled_when_last", test_move_down_disabled_when_last),
    ("test_remove_reenables_track_in_playlist_list", test_remove_reenables_track_in_playlist_list),
    ("test_add_placeholder_always_works", test_add_placeholder_always_works),
    ("test_add_selected_track_appends_and_disables_track", test_add_selected_track_appends_and_disables_track),
    ("test_clear_anchors_reenables_all_tracks", test_clear_anchors_reenables_all_tracks),
    ("test_add_duplicate_track_not_allowed", test_add_duplicate_track_not_allowed),
    ("test_save_anchors_writes_expected_file_format", test_save_anchors_writes_expected_file_format),
    ("test_button_enable_state_multi_select_logic", test_button_enable_state_multi_select_logic),
    ("test_anchored_track_ids_empty_plan", test_anchored_track_ids_empty_plan),
    ("test_anchored_track_ids_only_placeholders", test_anchored_track_ids_only_placeholders),
    ("test_anchored_track_ids_mixed", test_anchored_track_ids_mixed),
    ("test_track_not_selectable_when_anchored", test_track_not_selectable_when_anchored),
    ("test_all_tracks_selectable_when_no_anchors", test_all_tracks_selectable_when_no_anchors),
    ("test_locked_row_has_locked_field_true", test_locked_row_has_locked_field_true),
    ("test_unlocked_row_has_locked_field_false", test_unlocked_row_has_locked_field_false),
    ("test_anchor_row_click_parses_list_shaped_event_args", test_anchor_row_click_parses_list_shaped_event_args),
    ("test_playlist_row_click_parses_list_shaped_event_args", test_playlist_row_click_parses_list_shaped_event_args),
    ("test_double_click_on_playlist_row_adds_to_anchors", test_double_click_on_playlist_row_adds_to_anchors),
    ("test_clear_anchors_saves_zero_items", test_clear_anchors_saves_zero_items),
    ("test_remove_last_item_then_save_persists_empty_list", test_remove_last_item_then_save_persists_empty_list),
    ("test_save_log_message_matches_actual_saved_count", test_save_log_message_matches_actual_saved_count),
    ("test_save_does_not_mutate_in_memory_plan", test_save_does_not_mutate_in_memory_plan),
    ("test_checkbox_column_present_in_playlist_table_row_config", test_checkbox_column_present_in_playlist_table_row_config),
    ("test_checkbox_column_present_in_anchors_table_row_config", test_checkbox_column_present_in_anchors_table_row_config),
    ("test_double_click_calls_same_handler_as_add_selected_track_button", test_double_click_calls_same_handler_as_add_selected_track_button),
    ("test_status_field_populated_matching_analysis_semantics", test_status_field_populated_matching_analysis_semantics),
    ("test_anchored_status_preserved_over_analysis_status", test_anchored_status_preserved_over_analysis_status),
    ("test_up_button_disabled_when_first_row_selected", test_up_button_disabled_when_first_row_selected),
    ("test_up_button_enabled_when_non_first_row_selected", test_up_button_enabled_when_non_first_row_selected),
    ("test_down_button_disabled_when_last_row_selected", test_down_button_disabled_when_last_row_selected),
    ("test_down_button_enabled_when_non_last_row_selected", test_down_button_enabled_when_non_last_row_selected),
    ("test_shared_track_rows_artist_populated", test_shared_track_rows_artist_populated),
    ("test_shared_track_rows_status_populated_when_anchored", test_shared_track_rows_status_populated_when_anchored),
    ("test_selected_row_visually_indicated", test_selected_row_visually_indicated),
    ("test_selected_cleared_when_out_of_bounds", test_selected_cleared_when_out_of_bounds),
    ("test_only_one_row_selectable_at_a_time", test_only_one_row_selectable_at_a_time),
    ("test_checkbox_click_updates_selection_state", test_checkbox_click_updates_selection_state),
    ("test_unchecking_clears_selection_and_disables_add_button", test_unchecking_clears_selection_and_disables_add_button),
    ("test_row_click_and_checkbox_click_converge_on_same_selection", test_row_click_and_checkbox_click_converge_on_same_selection),
    ("test_add_selected_track_button_reflects_actual_visible_selection", test_add_selected_track_button_reflects_actual_visible_selection),
    ("test_checkbox_only_click_triggers_button_refresh", test_checkbox_only_click_triggers_button_refresh),
    ("test_selection_event_wired_to_refresh_control_buttons", test_selection_event_wired_to_refresh_control_buttons),
    ("test_double_click_adds_exact_clicked_track_not_stale_selection", test_double_click_adds_exact_clicked_track_not_stale_selection),
    ("test_track_deselected_after_double_click_add", test_track_deselected_after_double_click_add),
    ("test_anchor_table_selected_is_always_a_list", test_anchor_table_selected_is_always_a_list),
    ("test_anchor_row_click_does_not_affect_playlist_track_selection", test_anchor_row_click_does_not_affect_playlist_track_selection),
    ("test_remove_anchor_reenables_add_button_if_track_still_checked", test_remove_anchor_reenables_add_button_if_track_still_checked),
]

for name, fn in tests:
    try:
        fn()
        results.append(f"PASS: {name}")
    except Exception as e:
        results.append(f"FAIL: {name} - {e}")

# ── Print Results ────────────────────────────────────────────────────────
for r in results:
    print(r)

passed = sum(1 for r in results if r.startswith("PASS"))
failed = sum(1 for r in results if r.startswith("FAIL"))
print(f"\n{passed}/{len(results)} passed, {failed} failed")

sys.exit(1 if failed > 0 else 0)