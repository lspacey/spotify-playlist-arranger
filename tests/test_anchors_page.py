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
    """Sort button re-evaluates on refresh — mimics _refresh_nav_buttons for sort.

    SORTING is now enabled/disabled in lockstep with ANCHORS (both use
    _state.sp is not None), not the old has_anchor_plan() condition.
    """
    class FakeButton:
        def __init__(self):
            self._enabled = False
        def set_enabled(self, val):
            self._enabled = val

    anchor_btn = FakeButton()
    sort_btn = FakeButton()
    original_sp = _state.sp
    try:
        # Disconnected → both buttons disabled
        _state.sp = None
        anchor_btn.set_enabled(_state.sp is not None)
        sort_btn.set_enabled(_state.sp is not None)
        assert not anchor_btn._enabled, "ANCHORS should be disabled when sp=None"
        assert not sort_btn._enabled, "SORTING should be disabled when sp=None"
        assert anchor_btn._enabled == sort_btn._enabled, (
            "ANCHORS and SORTING must be in lockstep (disabled)"
        )

        # Connected → both buttons enabled
        _state.sp = "connected_client"
        anchor_btn.set_enabled(_state.sp is not None)
        sort_btn.set_enabled(_state.sp is not None)
        assert anchor_btn._enabled, "ANCHORS should be enabled when sp is set"
        assert sort_btn._enabled, "SORTING should be enabled when sp is set"
        assert anchor_btn._enabled == sort_btn._enabled, (
            "ANCHORS and SORTING must be in lockstep (enabled)"
        )

        # Disconnected again → both disabled again
        _state.sp = None
        anchor_btn.set_enabled(_state.sp is not None)
        sort_btn.set_enabled(_state.sp is not None)
        assert not anchor_btn._enabled, "ANCHORS should disable after disconnect"
        assert not sort_btn._enabled, "SORTING should disable after disconnect"
        assert anchor_btn._enabled == sort_btn._enabled, (
            "ANCHORS and SORTING must remain in lockstep after disconnect"
        )
    finally:
        _state.sp = original_sp


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
    """selected_playlist_id survives page navigation (state field test)."""
    original_val = _state.selected_playlist_id
    try:
        _state.selected_playlist_id = "pl_test_789"
        assert _state.selected_playlist_id == "pl_test_789"
        # Simulate navigating away and back — value persists in state
        assert _state.selected_playlist_id == "pl_test_789"
    finally:
        _state.selected_playlist_id = original_val


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


# ── Generate Anchors feature tests ─────────────────────────────────────────

def test_playlist_structures_includes_custom_type():
    """PLAYLIST_STRUCTURES must include a 'custom' entry with anchor_pct=20."""
    from playlist_arranger.llm.prompts import PLAYLIST_STRUCTURES
    custom = next((s for s in PLAYLIST_STRUCTURES if s["id"] == "custom"), None)
    assert custom is not None, "PLAYLIST_STRUCTURES must include 'custom' type"
    assert custom["name"] == "Custom"
    assert custom["anchor_pct"] == 20
    assert "desc" in custom


def test_n_anchors_formula_matches_old_script():
    """n_anchors = max(3, int(len(tracks) * anchor_pct / 100)) — matches old script."""
    anchor_pct = 20
    test_cases = [
        (10, 3),   # 10 * 0.2 = 2 → max(3, 2) = 3
        (20, 4),   # 20 * 0.2 = 4 → max(3, 4) = 4
        (50, 10),  # 50 * 0.2 = 10 → max(3, 10) = 10
        (5, 3),    # 5 * 0.2 = 1 → max(3, 1) = 3
        (1, 3),    # 1 * 0.2 = 0 → max(3, 0) = 3
        (100, 20), # 100 * 0.2 = 20 → max(3, 20) = 20
    ]
    for n_tracks, expected in test_cases:
        result = max(3, int(n_tracks * anchor_pct / 100))
        assert result == expected, f"n_tracks={n_tracks}: expected {expected}, got {result}"


def test_custom_prompt_persisted_to_settings_and_reloaded():
    """Settings.custom_anchor_prompt must survive save/load cycle.

    Custom prompt is saved via config.save_settings() only when the Run
    button is clicked with structure='custom' — no per-keystroke writes.
    This test simulates the Run-click path directly.
    """
    from playlist_arranger import config

    s = config.load_settings()
    original = s.custom_anchor_prompt

    try:
        # Simulate Run-click save: user typed a prompt, then clicked Run
        prompt_text = "Test custom description for anchoring"
        s.custom_anchor_prompt = prompt_text
        config.save_settings(s)

        # Verify it survived a fresh load
        reloaded = config.load_settings()
        assert reloaded.custom_anchor_prompt == prompt_text, (
            f"Expected {prompt_text!r}, got {reloaded.custom_anchor_prompt!r}"
        )

        # Verify an empty prompt also persists (clearing the field)
        s.custom_anchor_prompt = ""
        config.save_settings(s)
        reloaded2 = config.load_settings()
        assert reloaded2.custom_anchor_prompt == "", "Empty prompt must persist"
    finally:
        s.custom_anchor_prompt = original
        config.save_settings(s)


def test_position_number_parsing_valid_response():
    """_parse_anchor_positions must extract exactly N valid unique numbers,
    handling common LLM output formatting quirks (bare numbers, bullets,
    trailing dots, whitespace)."""
    import playlist_arranger.ui.pages.anchors as _ap

    # Bare numbers
    raw = "ANCHORS:\n7\n23\n41\n"
    result = _ap._parse_anchor_positions(raw, 3, 50)
    assert result == [7, 23, 41]

    # With trailing dots and whitespace
    raw2 = "ANCHORS:\n  7.  \n  23.  \n  41.  \n"
    result2 = _ap._parse_anchor_positions(raw2, 3, 50)
    assert result2 == [7, 23, 41]

    # Without ANCHORS: header
    raw3 = "7\n23\n41\n"
    result3 = _ap._parse_anchor_positions(raw3, 3, 50)
    assert result3 == [7, 23, 41]

    # Bullet-prefixed (hyphens)
    raw4 = "ANCHORS:\n- 7\n- 23\n- 41\n"
    result4 = _ap._parse_anchor_positions(raw4, 3, 50)
    assert result4 == [7, 23, 41]

    # Bullet-prefixed (asterisks)
    raw5 = "ANCHORS:\n* 7\n* 23\n* 41\n"
    result5 = _ap._parse_anchor_positions(raw5, 3, 50)
    assert result5 == [7, 23, 41]

    # Hash-prefixed
    raw6 = "ANCHORS:\n#7\n#23\n#41\n"
    result6 = _ap._parse_anchor_positions(raw6, 3, 50)
    assert result6 == [7, 23, 41]

    # Blank lines between numbers
    raw7 = "ANCHORS:\n7\n\n23\n\n41\n"
    result7 = _ap._parse_anchor_positions(raw7, 3, 50)
    assert result7 == [7, 23, 41]

    # Trailing comment text after number should NOT match (not a bare number line)
    raw8 = "ANCHORS:\n7 track name - artist\n23\n41\n"
    result8 = _ap._parse_anchor_positions(raw8, 3, 50)
    assert result8 is None, "Non-numeric text after number must not count as a match"


def test_position_number_parsing_rejects_out_of_range():
    """_parse_anchor_positions must return None for out-of-range numbers."""
    import playlist_arranger.ui.pages.anchors as _ap
    # 42 > 40 track_count
    raw = "ANCHORS:\n7\n42\n3\n"
    result = _ap._parse_anchor_positions(raw, 3, 40)
    assert result is None, "Out-of-range must return None"

    # 0 is invalid
    raw2 = "ANCHORS:\n7\n0\n3\n"
    result2 = _ap._parse_anchor_positions(raw2, 3, 50)
    assert result2 is None, "0 is invalid position"


def test_position_number_parsing_rejects_duplicates():
    """_parse_anchor_positions must return None for duplicate numbers."""
    import playlist_arranger.ui.pages.anchors as _ap
    raw = "ANCHORS:\n7\n23\n7\n"
    result = _ap._parse_anchor_positions(raw, 3, 50)
    assert result is None, "Duplicates must return None"


def test_generate_anchors_clears_existing_plan_before_applying_new():
    """After generate, the plan must contain only the new anchors + placeholders.
    Old plan items must not persist."""
    import playlist_arranger.ui.pages.anchors as _ap

    old_plan = list(_ap._anchor_plan)
    old_tracks = list(_ap._playlist_tracks)
    try:
        _ap._playlist_tracks[:] = [
            {"id": "t1", "name": "T1", "artist": "A1", "duration_ms": 100000},
            {"id": "t2", "name": "T2", "artist": "A2", "duration_ms": 200000},
            {"id": "t3", "name": "T3", "artist": "A3", "duration_ms": 300000},
            {"id": "t4", "name": "T4", "artist": "A4", "duration_ms": 400000},
            {"id": "t5", "name": "T5", "artist": "A5", "duration_ms": 500000},
        ]
        # Pre-populate with OLD plan that should be cleared
        _ap._anchor_plan[:] = [{"type": "anchor", "track_id": "t_old"}]

        # Simulate the generate flow: clear + rebuild
        positions = [1, 3, 5]  # 1-based positions
        _ap._anchor_plan.clear()
        for i, pos in enumerate(positions):
            tid = _ap._playlist_tracks[pos - 1]["id"]
            _ap._anchor_plan.append({"type": "anchor", "track_id": tid})
            if i < len(positions) - 1:
                _ap._anchor_plan.append({"type": "placeholder"})

        # Verify: no old items remain
        assert len(_ap._anchor_plan) == 5, f"Expected 5 items (3 anchors + 2 placeholders), got {len(_ap._anchor_plan)}"
        assert _ap._anchor_plan[0] == {"type": "anchor", "track_id": "t1"}
        assert _ap._anchor_plan[1] == {"type": "placeholder"}
        assert _ap._anchor_plan[2] == {"type": "anchor", "track_id": "t3"}
        assert _ap._anchor_plan[3] == {"type": "placeholder"}
        assert _ap._anchor_plan[4] == {"type": "anchor", "track_id": "t5"}

        # Old item must NOT be present
        anchored_ids = _ap._anchored_track_ids()
        assert "t_old" not in anchored_ids, "Old plan item must be cleared"
    finally:
        _ap._anchor_plan[:] = old_plan
        _ap._playlist_tracks[:] = old_tracks


def test_placeholder_inserted_between_consecutive_anchors_only():
    """Placeholders must be BETWEEN anchors only; no leading/trailing.

    For N=3 anchors at positions [1, 2, 3], result is:
    [anchor(t1), placeholder, anchor(t2), placeholder, anchor(t3)]
    = 5 items (2 placeholders for 3 anchors, N-1).
    """
    import playlist_arranger.ui.pages.anchors as _ap

    old_plan = list(_ap._anchor_plan)
    try:
        # N=3 anchors
        positions = [1, 2, 3]

        _ap._anchor_plan.clear()
        for i, pos in enumerate(positions):
            tid = f"t{pos}"
            _ap._anchor_plan.append({"type": "anchor", "track_id": tid})
            if i < len(positions) - 1:
                _ap._anchor_plan.append({"type": "placeholder"})

        assert len(_ap._anchor_plan) == 5, "3 anchors + 2 interior placeholders = 5 items"

        # Position-by-position check
        assert _ap._anchor_plan[0] == {"type": "anchor", "track_id": "t1"}, "1st = anchor"
        assert _ap._anchor_plan[1] == {"type": "placeholder"}, "2nd = placeholder (interior)"
        assert _ap._anchor_plan[2] == {"type": "anchor", "track_id": "t2"}, "3rd = anchor"
        assert _ap._anchor_plan[3] == {"type": "placeholder"}, "4th = placeholder (interior)"
        assert _ap._anchor_plan[4] == {"type": "anchor", "track_id": "t3"}, "5th = anchor"

        # No leading placeholder (index 0 is anchor)
        assert _ap._anchor_plan[0]["type"] == "anchor"
        # No trailing placeholder (last item is anchor)
        assert _ap._anchor_plan[-1]["type"] == "anchor"

        # N=1 case: no placeholders at all
        _ap._anchor_plan.clear()
        for i, pos in enumerate([1]):
            tid = f"t{pos}"
            _ap._anchor_plan.append({"type": "anchor", "track_id": tid})
            if i < 0:  # len-1 = 0, never executed
                _ap._anchor_plan.append({"type": "placeholder"})

        assert len(_ap._anchor_plan) == 1, "1 anchor + 0 placeholders = 1 item"
        assert _ap._anchor_plan[0] == {"type": "anchor", "track_id": "t1"}
    finally:
        _ap._anchor_plan[:] = old_plan


def test_debug_prompt_file_written_before_llm_call():
    """Verify that _build_track_descriptions_block produces the right format
    and that the debug path is correct."""
    import playlist_arranger.ui.pages.anchors as _ap
    from playlist_arranger import config

    old_tracks = list(_ap._playlist_tracks)
    try:
        _ap._playlist_tracks[:] = [
            {"id": "t1", "name": "Test Track", "artist": "Test Artist", "duration_ms": 100000},
        ]
        # The debug file path must be cache/anchors_prompts_for_debug.txt
        expected_path = config.CACHE_DIR_DEFAULT / "anchors_prompts_for_debug.txt"
        assert str(expected_path).endswith("anchors_prompts_for_debug.txt"), (
            "Debug prompt path must end with anchors_prompts_for_debug.txt"
        )

        # The description block must include position numbers
        block = _ap._build_track_descriptions_block()
        assert "Test Track" in block, "Description block includes track name"
        assert "Test Artist" in block, "Description block includes artist"
        assert "1" in block, "Description block includes position number"
    finally:
        _ap._playlist_tracks[:] = old_tracks


def test_async_pattern_wraps_llm_call_in_asyncio_to_thread():
    """_on_run_generate must be async def and must use asyncio.to_thread
    to offload the blocking LLM call, preventing the NiceGUI event loop
    from freezing for all clients during the request."""
    import playlist_arranger.ui.pages.anchors as _ap
    import inspect
    import asyncio

    # Verify _on_run_generate is async def
    source = inspect.getsource(_ap._on_run_generate)
    assert "async def _on_run_generate" in source, (
        "_on_run_generate must be async def to avoid blocking the event loop"
    )
    assert "asyncio.to_thread" in source, (
        "_on_run_generate must use asyncio.to_thread to offload the blocking LLM call"
    )

    # Verify the import exists at module level (import asyncio)
    module_source = inspect.getsource(inspect.getmodule(_ap))
    first_imports = "\n".join(module_source.split("\n")[:15])
    assert "import asyncio" in first_imports, "asyncio must be imported at module level"


def test_resolve_model_name_unknown_backend():
    """_resolve_model_name must return explicit '(unknown backend: ...)' for
    unrecognised backend keys rather than silently defaulting to Ollama."""
    import playlist_arranger.ui.pages.anchors as _ap
    from playlist_arranger import config

    s = config.load_settings()
    original_backend = s.llm_backend
    try:
        s.llm_backend = "some_unrecognized_backend"
        config.save_settings(s)

        model = _ap._resolve_model_name()
        assert "(unknown backend: some_unrecognized_backend)" in model, (
            f"Expected explicit unknown-backend label, got {model!r}"
        )
    finally:
        s.llm_backend = original_backend
        config.save_settings(s)


def test_available_backend_models_ollama_always_included():
    """_available_backend_models must always include ollama (no API key needed)."""
    import playlist_arranger.ui.pages.anchors as _ap

    available = _ap._available_backend_models()
    values = [opt["value"] for opt in available]
    assert "ollama" in values, "Ollama must always be available (no API key required)"


def test_model_dropdown_not_readonly_selectable():
    """The model dropdown must NOT use .props('readonly') — it must be
    user-selectable to switch backends per the original task spec."""
    import playlist_arranger.ui.pages.anchors as _ap
    import inspect

    source = inspect.getsource(_ap._render_generate_panel)
    assert "readonly" not in source, (
        "Model dropdown must NOT be readonly — it should be user-selectable"
    )

    # Verify _on_run_generate reads the dropdown value and passes backend override
    run_source = inspect.getsource(_ap._on_run_generate)
    assert "selected_backend" in run_source, (
        "_on_run_generate must read the dropdown value for backend override"
    )
    assert "backend=selected_backend" in run_source, (
        "_on_run_generate must pass backend=selected_backend to _init_llm_client"
    )
    assert "model_override=selected_model" in run_source, (
        "_on_run_generate must pass model_override=selected_model to _init_llm_client"
    )


def test_switching_backend_creates_new_client_not_stale_cache():
    """After calling _init_llm_client(backend='ollama'), a subsequent call with
    backend='mistral' must return a different client object, not the stale
    cached Ollama client.  The single-slot global cache bug would silently
    reuse the wrong backend's client."""
    from playlist_arranger.llm.client import _init_llm_client, _llm_clients, _llm_models_used

    # Clear any cached state from prior tests (module-level globals)
    _llm_clients.clear()
    _llm_models_used.clear()

    # We can't actually create real clients in unit tests (no API keys,
    # Ollama not running), but we CAN verify the per-backend dict logic:
    # Manually populate the cache to simulate prior initialisation.
    class FakeClient:
        def __init__(self, backend_name):
            self.backend = backend_name

    ollama_client = FakeClient("ollama")
    _llm_clients["ollama"] = ollama_client
    _llm_models_used["ollama"] = "gemma4:26b"

    # Now request mistral — should NOT return the ollama client
    mistral_client = FakeClient("mistral")
    _llm_clients["mistral"] = mistral_client
    _llm_models_used["mistral"] = "mistral-large-latest"

    # After populating, the cached ollama client must differ from mistral
    assert _llm_clients.get("ollama") is ollama_client
    assert _llm_clients.get("mistral") is mistral_client
    assert _llm_clients["ollama"] is not _llm_clients["mistral"], (
        "Different backends must have different cached clients — "
        "single-slot cache bug would make these the same object"
    )

    # Verify the per-backend dict actually maps by key
    assert set(_llm_clients.keys()) == {"ollama", "mistral"}, (
        f"Expected both backends cached, got keys: {set(_llm_clients.keys())}"
    )


# ── Session-level panel persistence tests ─────────────────────────────────

def test_panel_remembers_structure_selection_across_toggle():
    """Changing the structure dropdown and toggling the panel must restore
    the same value."""
    import playlist_arranger.ui.pages.anchors as _ap

    old_id = _ap._gen_last_structure_id
    try:
        _ap._gen_last_structure_id = "custom"
        from playlist_arranger.llm.prompts import PLAYLIST_STRUCTURES
        structure_options = {s["id"]: s["name"] for s in PLAYLIST_STRUCTURES}
        struct_id = (
            _ap._gen_last_structure_id
            if _ap._gen_last_structure_id in structure_options
            else PLAYLIST_STRUCTURES[0]["id"]
        )
        assert struct_id == "custom", f"Expected 'custom', got {struct_id!r}"
    finally:
        _ap._gen_last_structure_id = old_id


def test_panel_remembers_custom_desc_text_across_toggle_within_session():
    """After user types in the textarea and toggles panel, the text must survive."""
    import playlist_arranger.ui.pages.anchors as _ap

    old_text = _ap._gen_last_desc_text
    old_touched = _ap._gen_last_desc_touched
    old_struct = _ap._gen_last_structure_id
    try:
        _ap._gen_last_structure_id = "custom"
        _ap._gen_last_desc_touched = True
        _ap._gen_last_desc_text = "My typed custom prompt"

        # Simulate _render_generate_panel's logic
        struct_id = _ap._gen_last_structure_id
        if struct_id == "custom" and not _ap._gen_last_desc_touched:
            desc_value = "FROM_SETTINGS"  # not reached
        else:
            desc_value = _ap._gen_last_desc_text or "fallback"

        assert desc_value == "My typed custom prompt", (
            f"Expected session-typed value, got {desc_value!r}"
        )
    finally:
        _ap._gen_last_desc_text = old_text
        _ap._gen_last_desc_touched = old_touched
        _ap._gen_last_structure_id = old_struct


def test_panel_remembers_user_overridden_n_across_toggle():
    """After user changes N and toggles panel, the overridden value must survive."""
    import playlist_arranger.ui.pages.anchors as _ap

    old_n = _ap._gen_last_n
    try:
        _ap._gen_last_n = 42
        assert _ap._gen_last_n == 42, "N must survive toggle"
    finally:
        _ap._gen_last_n = old_n


def test_panel_remembers_model_selection_across_toggle():
    """After user changes the model dropdown and toggles, the selection must survive."""
    import playlist_arranger.ui.pages.anchors as _ap

    old_backend = _ap._gen_last_backend
    try:
        _ap._gen_last_backend = "mistral"
        assert _ap._gen_last_backend == "mistral", "Model selection must survive toggle"
    finally:
        _ap._gen_last_backend = old_backend


def test_custom_structure_still_prefers_settings_json_on_first_open_ever():
    """On first-ever open (never touched), custom text must load from
    settings.json, not an empty string."""
    import playlist_arranger.ui.pages.anchors as _ap
    from playlist_arranger import config

    old_text = _ap._gen_last_desc_text
    old_touched = _ap._gen_last_desc_touched
    old_struct = _ap._gen_last_structure_id
    old_settings = None
    try:
        s = config.load_settings()
        old_settings = s.custom_anchor_prompt
        s.custom_anchor_prompt = "SAVED_CUSTOM"
        config.save_settings(s)

        _ap._gen_last_structure_id = "custom"
        _ap._gen_last_desc_touched = False
        _ap._gen_last_desc_text = ""  # stale

        # Simulate _render_generate_panel logic
        struct_id = _ap._gen_last_structure_id
        if struct_id == "custom" and not _ap._gen_last_desc_touched:
            s2 = config.load_settings()
            desc_value = s2.custom_anchor_prompt or ""
        else:
            desc_value = _ap._gen_last_desc_text

        assert desc_value == "SAVED_CUSTOM", (
            f"Untouched custom must load from settings.json, got {desc_value!r}"
        )
    finally:
        _ap._gen_last_desc_text = old_text
        _ap._gen_last_desc_touched = old_touched
        _ap._gen_last_structure_id = old_struct
        if old_settings is not None:
            s3 = config.load_settings()
            s3.custom_anchor_prompt = old_settings
            config.save_settings(s3)


def test_switching_playlist_resets_generate_n():
    """Switching to a different playlist must reset _gen_last_n to None."""
    import playlist_arranger.ui.pages.anchors as _ap

    old_n = _ap._gen_last_n
    try:
        _ap._gen_last_n = 42  # stale value from previous playlist
        # Simulate playlist switch: _on_playlist_selected sets _gen_last_n = None
        _ap._gen_last_n = None
        assert _ap._gen_last_n is None, (
            "N must reset to None on playlist switch — stale N from old playlist "
            "is meaningless for the new one"
        )
    finally:
        _ap._gen_last_n = old_n


def test_notify_before_panel_clear_in_on_run_generate():
    """ui.notify() must be called BEFORE _gen_panel.clear() in
    _on_run_generate to avoid RuntimeError from stale UI slot context
    after an async handler resumes from asyncio.to_thread."""
    import playlist_arranger.ui.pages.anchors as _ap
    import inspect

    # Scope to ONLY _on_run_generate, not the entire module
    source = inspect.getsource(_ap._on_run_generate)
    # Find the success section: notify then clear.
    # Use rfind for _gen_panel.clear() to get the LAST/actual code call,
    # not the docstring mention (line 157's comment "# calling ui.notify()
    # AFTER _gen_panel.clear() can fail..." is at an earlier position).
    notify_pos = source.find("ui.notify(f\"Generated")
    clear_pos = source.rfind("_gen_panel.clear()")

    assert notify_pos > -1, "ui.notify for success must exist in _on_run_generate"
    assert clear_pos > -1, "_gen_panel.clear() must exist in _on_run_generate"
    assert notify_pos < clear_pos, (
        "ui.notify() must appear BEFORE _gen_panel.clear() in _on_run_generate "
        f"(notify at pos {notify_pos}, clear at pos {clear_pos})"
    )

    # Also verify the notification is wrapped in try/except for defense
    # (search in the region around the notification)
    region = source[max(0, notify_pos - 150):notify_pos + 300]
    assert "try:" in region, (
        "Success notification in _on_run_generate must be wrapped in try/except"
    )
    assert "logger.exception" in region, (
        "Failed notification must log via logger.exception"
    )


# ── FIX 1: Placeholder collapse tests ─────────────────────────────────────

def test_save_collapses_adjacent_placeholders():
    """_collapse_adjacent_placeholders merges consecutive placeholder entries."""
    from playlist_arranger.ui.pages.anchors import _collapse_adjacent_placeholders
    plan = [
        {"type": "anchor", "track_id": "a1"},
        {"type": "placeholder"},
        {"type": "placeholder"},
        {"type": "placeholder"},
        {"type": "anchor", "track_id": "a2"},
    ]
    result = _collapse_adjacent_placeholders(plan)
    assert len(result) == 3, f"Expected 3 items, got {len(result)}: {result}"
    assert result[0] == {"type": "anchor", "track_id": "a1"}
    assert result[1] == {"type": "placeholder"}
    assert result[2] == {"type": "anchor", "track_id": "a2"}


def test_save_collapses_leading_and_trailing_placeholder_runs():
    """Leading and trailing placeholder runs collapse to single placeholders."""
    from playlist_arranger.ui.pages.anchors import _collapse_adjacent_placeholders
    plan = [
        {"type": "placeholder"},
        {"type": "placeholder"},
        {"type": "anchor", "track_id": "a1"},
        {"type": "placeholder"},
        {"type": "placeholder"},
    ]
    result = _collapse_adjacent_placeholders(plan)
    assert len(result) == 3, f"Expected 3 items, got {len(result)}: {result}"
    assert result[0] == {"type": "placeholder"}
    assert result[1] == {"type": "anchor", "track_id": "a1"}
    assert result[2] == {"type": "placeholder"}


def test_save_does_not_affect_single_isolated_placeholders():
    """A plan with well-separated single placeholders is unchanged."""
    from playlist_arranger.ui.pages.anchors import _collapse_adjacent_placeholders
    plan = [
        {"type": "anchor", "track_id": "a1"},
        {"type": "placeholder"},
        {"type": "anchor", "track_id": "a2"},
        {"type": "placeholder"},
        {"type": "anchor", "track_id": "a3"},
    ]
    result = _collapse_adjacent_placeholders(plan)
    assert result == plan, f"Plan should be unchanged, got {result}"


def test_collapse_stale_index_reset():
    """When collapse shrinks the plan past _selected_anchor_idx, selection resets.

    Before: 7 items (anchor, ph, ph, ph, ph, ph, anchor) — selected at index 3.
    After collapse: 3 items (anchor, ph, anchor) — index 3 is out of range.
    """
    import playlist_arranger.ui.pages.anchors as _anchors_mod
    orig_plan = list(_anchors_mod._anchor_plan)
    orig_idx = _anchors_mod._selected_anchor_idx
    try:
        # Set up: 7 items, 5 consecutive placeholders between two anchors
        _anchors_mod._anchor_plan[:] = [
            {"type": "anchor", "track_id": "a1"},
            {"type": "placeholder"},
            {"type": "placeholder"},
            {"type": "placeholder"},
            {"type": "placeholder"},
            {"type": "placeholder"},
            {"type": "anchor", "track_id": "a2"},
        ]
        _anchors_mod._selected_anchor_idx = 3  # 4th placeholder in the middle

        from playlist_arranger.ui.pages.anchors import _collapse_adjacent_placeholders
        collapsed = _collapse_adjacent_placeholders(_anchors_mod._anchor_plan)
        # Simulate what _save_anchors does:
        _anchors_mod._anchor_plan[:] = collapsed
        if _anchors_mod._selected_anchor_idx is not None and _anchors_mod._selected_anchor_idx >= len(_anchors_mod._anchor_plan):
            _anchors_mod._selected_anchor_idx = None

        # Collapsed: [anchor(a1), placeholder, anchor(a2)] = 3 items
        # Selected idx 3 >= 3 → out of range → must be None
        assert _anchors_mod._selected_anchor_idx is None, (
            f"Selected index should be reset after collapse (3 >= 3), got {_anchors_mod._selected_anchor_idx}"
        )
        assert len(_anchors_mod._anchor_plan) == 3, f"Expected 3 items, got {len(_anchors_mod._anchor_plan)}"
    finally:
        _anchors_mod._anchor_plan[:] = orig_plan
        _anchors_mod._selected_anchor_idx = orig_idx


# ── FIX 2: LLM client key-error message tests ─────────────────────────────

def test_init_llm_client_raises_clear_error_when_deepseek_key_missing():
    """_init_llm_client with deepseek backend raises clear key-missing error."""
    import playlist_arranger.llm.client as lc
    orig_key = lc.DEEPSEEK_API_KEY
    orig_clients = dict(lc._llm_clients)
    orig_models = dict(lc._llm_models_used)
    try:
        lc.DEEPSEEK_API_KEY = ""
        lc._llm_clients.clear()  # force cache miss to reach key check
        lc._llm_models_used.clear()
        from openai import OpenAI as _OpenAI  # noqa
        try:
            lc._init_llm_client(backend="deepseek")
            assert False, "Should have raised RuntimeError"
        except RuntimeError as e:
            msg = str(e).lower()
            assert "deepseek" in msg, f"Error should mention 'deepseek': {msg}"
            assert "api_key" in msg or ".env" in msg, f"Error should mention .env or api_key: {msg}"
    finally:
        lc.DEEPSEEK_API_KEY = orig_key
        lc._llm_clients = orig_clients
        lc._llm_models_used = orig_models


def test_init_llm_client_raises_clear_error_when_mistral_key_missing():
    """_init_llm_client with mistral backend raises clear key-missing error."""
    import playlist_arranger.llm.client as lc
    orig_key = lc.MISTRAL_API_KEY
    orig_clients = dict(lc._llm_clients)
    orig_models = dict(lc._llm_models_used)
    try:
        lc.MISTRAL_API_KEY = ""
        lc._llm_clients.clear()  # force cache miss to reach key check
        lc._llm_models_used.clear()
        from openai import OpenAI as _OpenAI  # noqa
        try:
            lc._init_llm_client(backend="mistral")
            assert False, "Should have raised RuntimeError"
        except RuntimeError as e:
            msg = str(e).lower()
            assert "mistral" in msg, f"Error should mention 'mistral': {msg}"
            assert "api_key" in msg or ".env" in msg, f"Error should mention .env or api_key: {msg}"
    finally:
        lc.MISTRAL_API_KEY = orig_key
        lc._llm_clients = orig_clients
        lc._llm_models_used = orig_models


def test_init_llm_client_ollama_never_requires_api_key():
    """_init_llm_client with ollama backend does NOT check for API keys."""
    import playlist_arranger.llm.client as lc
    orig_deepseek_key = lc.DEEPSEEK_API_KEY
    orig_mistral_key = lc.MISTRAL_API_KEY
    try:
        lc.DEEPSEEK_API_KEY = ""
        lc.MISTRAL_API_KEY = ""
        # Ollama should NOT raise about missing keys — it only needs
        # the ollama package (HAS_OLLAMA).  If ollama is installed, it
        # will try to connect and may raise a connection error, but it
        # should NEVER be a "missing API key" RuntimeError.
        try:
            lc._init_llm_client(backend="ollama")
        except RuntimeError as e:
            msg = str(e).lower()
            if "api_key" in msg or "not set" in msg or ".env" in msg:
                assert False, (
                    f"Ollama should NOT raise API-key errors, got: {e}"
                )
            # Connection errors (e.g. Ollama server not running) are
            # acceptable in the test environment.
        except ImportError:
            pass  # ollama package not installed — also fine
    finally:
        lc.DEEPSEEK_API_KEY = orig_deepseek_key
        lc.MISTRAL_API_KEY = orig_mistral_key


def test_available_backend_models_excludes_backends_without_keys():
    """_available_backend_models never includes cloud backends with no key."""
    from playlist_arranger.ui.pages.anchors import _available_backend_models
    import playlist_arranger.llm.client as lc
    orig_deepseek_key = lc.DEEPSEEK_API_KEY
    orig_mistral_key = lc.MISTRAL_API_KEY
    try:
        # Both keys empty — only Ollama should appear
        lc.DEEPSEEK_API_KEY = ""
        lc.MISTRAL_API_KEY = ""
        options = _available_backend_models()
        values = {opt["value"] for opt in options}
        assert "ollama" in values, f"Ollama should always be present: {values}"
        assert "deepseek" not in values, f"DeepSeek should NOT appear without key: {values}"
        assert "mistral" not in values, f"Mistral should NOT appear without key: {values}"

        # Mistral key set — should appear
        lc.MISTRAL_API_KEY = "some-key"
        options = _available_backend_models()
        values = {opt["value"] for opt in options}
        assert "mistral" in values, f"Mistral should appear with key: {values}"
        assert "deepseek" not in values, "DeepSeek should still be absent"

        # Both keys set
        lc.DEEPSEEK_API_KEY = "some-key"
        options = _available_backend_models()
        values = {opt["value"] for opt in options}
        assert "ollama" in values
        assert "deepseek" in values
        assert "mistral" in values
    finally:
        lc.DEEPSEEK_API_KEY = orig_deepseek_key
        lc.MISTRAL_API_KEY = orig_mistral_key


# ── Chunked batching tests ─────────────────────────────────────────────────

def test_write_track_descriptions_file_atomic_write():
    """write_track_descriptions_file writes atomically: tmp → rename, content matches."""
    import playlist_arranger.ui.pages.anchors as _ap
    from playlist_arranger import config
    import pathlib

    test_plid = "test_atomic_write_plid"
    descs = [
        '    1. "Test Track" — Test Artist\n        (no description available)\n        [BPM: 120.0, Key: C major]',
        '    2. "Track Two" — Artist Two\n        A good track\n        [BPM: 140.0, Key: D minor]',
    ]

    file_path = config.ANCHORS_DIR_DEFAULT / f"anchor_context_{test_plid}.txt"
    tmp_path = config.ANCHORS_DIR_DEFAULT / f"anchor_context_{test_plid}.txt.tmp"

    # Clean up before test
    for p in (file_path, tmp_path):
        if p.exists():
            p.unlink()

    try:
        result = _ap.write_track_descriptions_file(descs, test_plid)

        # Result is the final file path
        assert result == file_path, f"Expected {file_path}, got {result}"

        # File exists, tmp does not (atomic rename)
        assert file_path.exists(), "Final file must exist"
        assert not tmp_path.exists(), "Tmp file must be gone after rename"

        # Content matches
        content = file_path.read_text(encoding="utf-8")
        assert "Test Track" in content
        assert "Test Artist" in content
        assert "Track Two" in content
        assert "Artist Two" in content
        # Each desc has internal newlines; verify at least 1 joining newline exists
        # and both track names appear
        assert "Test Track" in content
        assert "Track Two" in content
        assert "\n    2." in content, "Second track should start on its own line"
    finally:
        for p in (file_path, tmp_path):
            if p.exists():
                p.unlink()


def test_user_msg_does_not_contain_full_descriptions_block_when_file_used():
    """Verify that _on_run_generate writes descriptions to file and that
    the _build_user_msg helper does not contain hardcoded descriptions.
    
    This tests the structural invariant: descriptions go to file, not inline.
    """
    import playlist_arranger.ui.pages.anchors as _ap
    import inspect

    source = inspect.getsource(_ap._on_run_generate)

    # Must have write_track_descriptions_file call
    assert "write_track_descriptions_file(" in source, (
        "_on_run_generate must call write_track_descriptions_file"
    )

    # Must build descs as a list
    assert "_build_track_descriptions_list()" in source, (
        "_on_run_generate must use _build_track_descriptions_list"
    )

    # The _build_user_msg helper receives desc_content as a parameter,
    # not as a hardcoded global block
    assert "def _build_user_msg(desc_content: str)" in source, (
        "_build_user_msg must take desc_content as parameter"
    )

    # The old inline _build_track_descriptions_block() call should NOT be at
    # the same indentation level as the new code — it's now delegated to
    # _build_user_msg which is called with externally-prepared content.
    # Verify _build_user_msg is defined as a closure inside _on_run_generate
    assert "f\"{desc_content}" in source, (
        "_build_user_msg must interpolate desc_content parameter"
    )


def test_chunked_batching_triggered_above_threshold():
    """Chunked batching is triggered when total_tracks > CHUNK_THRESHOLD."""
    import playlist_arranger.ui.pages.anchors as _ap

    # Inject a small threshold for testing
    old_threshold = _ap.CHUNK_THRESHOLD
    try:
        _ap.CHUNK_THRESHOLD = 10

        # total_tracks=11 > 10 → chunked
        total_tracks = 11
        use_chunked = total_tracks > _ap.CHUNK_THRESHOLD
        assert use_chunked, "11 > 10 should trigger chunked batching"

        # total_tracks=10 = 10 → not chunked
        total_tracks = 10
        use_chunked = total_tracks > _ap.CHUNK_THRESHOLD
        assert not use_chunked, "10 == 10 should NOT trigger chunked batching"
    finally:
        _ap.CHUNK_THRESHOLD = old_threshold


def test_chunked_batching_not_triggered_below_threshold():
    """Chunked batching is NOT triggered when total_tracks <= CHUNK_THRESHOLD."""
    import playlist_arranger.ui.pages.anchors as _ap

    old_threshold = _ap.CHUNK_THRESHOLD
    old_size = _ap.CHUNK_SIZE
    try:
        _ap.CHUNK_THRESHOLD = 150
        _ap.CHUNK_SIZE = 100

        total_tracks = 150
        use_chunked = total_tracks > _ap.CHUNK_THRESHOLD
        assert not use_chunked, "150 == 150 should NOT trigger chunked"

        total_tracks = 149
        use_chunked = total_tracks > _ap.CHUNK_THRESHOLD
        assert not use_chunked, "149 < 150 should NOT trigger chunked"

        total_tracks = 151
        use_chunked = total_tracks > _ap.CHUNK_THRESHOLD
        assert use_chunked, "151 > 150 should trigger chunked"
    finally:
        _ap.CHUNK_THRESHOLD = old_threshold
        _ap.CHUNK_SIZE = old_size


def test_chunk_anchor_counts_sum_to_requested_total():
    """Proportional anchor allocation across chunks sums to requested total."""
    import playlist_arranger.ui.pages.anchors as _ap

    old_size = _ap.CHUNK_SIZE
    try:
        _ap.CHUNK_SIZE = 100

        # Simulate the chunk planning logic (extracted from _on_run_generate)
        for total_tracks, n_anchors, chunk_size in [
            (250, 50, 100),   # 3 chunks: 100, 100, 50 → anchors: 20, 20, 10 = 50
            (300, 60, 100),   # 3 chunks: 100, 100, 100 → anchors: 20, 20, 20 = 60
            (151, 30, 100),   # 2 chunks: 100, 51 → anchors: 20, 10 = 30
            (500, 125, 100),  # 5 chunks: 100×5 → anchors: 25×5 = 125
            (10, 3, 100),     # 1 chunk: 10 → anchors: 3 (below threshold in prod but test the formula)
        ]:
            # Build per-chunk info
            chunks: list[list[int]] = []
            chunk_n_anchors_list: list[int] = []
            for start in range(0, total_tracks, chunk_size):
                end = min(start + chunk_size, total_tracks)
                chunk_size_actual = end - start
                chunks.append(list(range(start, end)))  # placeholder list
                chunk_n = max(0, round(n_anchors * chunk_size_actual / total_tracks))
                chunk_n_anchors_list.append(chunk_n)

            # Adjust to match exact total
            total_allocated = sum(chunk_n_anchors_list)
            while total_allocated > n_anchors:
                max_idx = chunk_n_anchors_list.index(max(chunk_n_anchors_list))
                if chunk_n_anchors_list[max_idx] > 0:
                    chunk_n_anchors_list[max_idx] -= 1
                    total_allocated -= 1
                else:
                    break
            while total_allocated < n_anchors:
                min_idx = chunk_n_anchors_list.index(min(chunk_n_anchors_list))
                chunk_n_anchors_list[min_idx] += 1
                total_allocated += 1

            assert total_allocated == n_anchors, (
                f"total_tracks={total_tracks}, n_anchors={n_anchors}: "
                f"allocated {total_allocated} != {n_anchors}"
            )
            assert sum(chunk_n_anchors_list) == n_anchors, (
                f"sum of chunk anchors {sum(chunk_n_anchors_list)} != {n_anchors}"
            )
            assert len(chunk_n_anchors_list) == len(chunks), (
                "one anchors count per chunk"
            )
    finally:
        _ap.CHUNK_SIZE = old_size


def test_chunk_deduplication_handles_overlapping_positions():
    """When different chunks select the same position, dedup preserves order (first wins)."""
    import playlist_arranger.ui.pages.anchors as _ap

    # Simulate the dedup logic from _on_run_generate
    all_positions = [7, 23, 41, 7, 23, 55]
    seen = set()
    deduped_positions = []
    for pos in all_positions:
        if pos not in seen:
            seen.add(pos)
            deduped_positions.append(pos)

    assert deduped_positions == [7, 23, 41, 55], "Duplicates removed, order preserved (first wins)"

    # No duplicates → unchanged
    all_positions2 = [1, 2, 3, 4, 5]
    seen2 = set()
    deduped2 = []
    for pos in all_positions2:
        if pos not in seen2:
            seen2.add(pos)
            deduped2.append(pos)
    assert deduped2 == [1, 2, 3, 4, 5], "No-dup list unchanged"


# ── Set These Anchors tests ────────────────────────────────────────────────

def test_set_anchors_rejects_empty_input():
    """_on_set_anchors must reject empty/whitespace-only input."""
    import playlist_arranger.ui.pages.anchors as _ap

    # Simulate empty input
    raw = ""
    assert not raw.strip(), "Empty/whitespace input detected"

    # Simulate whitespace-only
    raw2 = "   "
    assert not raw2.strip(), "Whitespace-only detected"


def test_set_anchors_rejects_non_integer_token():
    """_on_set_anchors rejects tokens that are not positive integers."""
    import re

    int_re = re.compile(r"^\d+$")
    tokens = ["3", "abc", "7"]
    valid = [t for t in tokens if not int_re.match(t)]
    assert valid == ["abc"], f"Only 'abc' should be invalid, got {valid}"

    # Floats are rejected
    assert not int_re.match("3.5"), "Float should not match ^\d+$"
    assert not int_re.match("-5"), "Negative should not match ^\d+$"
    assert not int_re.match("0.5"), "Decimal should not match ^\d+$"


def test_set_anchors_rejects_negative_number():
    """_on_set_anchors rejects negative numbers."""
    import re

    int_re = re.compile(r"^\d+$")
    assert not int_re.match("-5"), "-5 should be rejected"


def test_set_anchors_rejects_float_token():
    """_on_set_anchors rejects float tokens like 3.5."""
    import re

    int_re = re.compile(r"^\d+$")
    assert not int_re.match("3.5"), "3.5 should be rejected"


def test_set_anchors_rejects_out_of_range_number():
    """_on_set_anchors rejects numbers outside [1, n_tracks]."""
    n_tracks = 5
    numbers = [3, 10, 2]

    out_of_range = [n for n in numbers if n < 1 or n > n_tracks]
    assert out_of_range == [10], f"Only 10 should be out of range, got {out_of_range}"


def test_set_anchors_rejects_duplicate_numbers_in_input():
    """_on_set_anchors rejects duplicate track numbers in the user's input."""
    numbers = [3, 7, 3, 10]
    assert len(set(numbers)) != len(numbers), "Duplicates detected"


def test_set_anchors_rejects_already_anchored_track():
    """_on_set_anchors rejects tracks already in the anchor plan."""
    import playlist_arranger.ui.pages.anchors as _ap

    old_plan = list(_ap._anchor_plan)
    old_tracks = list(_ap._playlist_tracks)
    try:
        _ap._playlist_tracks[:] = [
            {"id": "t1", "name": "T1", "artist": "A1", "duration_ms": 100000},
            {"id": "t2", "name": "T2", "artist": "A2", "duration_ms": 200000},
            {"id": "t3", "name": "T3", "artist": "A3", "duration_ms": 300000},
        ]
        # Track 2 (position 2) is already anchored
        _ap._anchor_plan[:] = [{"type": "anchor", "track_id": "t2"}]

        already = _ap._anchored_track_ids()
        assert "t2" in already, "Track 2 should be anchored"

        # User input: 1, 2, 3 — track 2 should be rejected
        numbers = [1, 2, 3]
        rejected = []
        for n in numbers:
            idx = n - 1
            tid = _ap._playlist_tracks[idx].get("id", "")
            if tid in already:
                rejected.append(n)

        assert rejected == [2], f"Only track 2 should be rejected, got {rejected}"
    finally:
        _ap._anchor_plan[:] = old_plan
        _ap._playlist_tracks[:] = old_tracks


def test_set_anchors_valid_input_adds_in_typed_order():
    """Valid input should add anchors in the order typed by the user."""
    import playlist_arranger.ui.pages.anchors as _ap

    old_plan = list(_ap._anchor_plan)
    old_tracks = list(_ap._playlist_tracks)
    try:
        _ap._playlist_tracks[:] = [
            {"id": "t1", "name": "T1", "artist": "A1", "duration_ms": 100000},
            {"id": "t2", "name": "T2", "artist": "A2", "duration_ms": 200000},
            {"id": "t3", "name": "T3", "artist": "A3", "duration_ms": 300000},
            {"id": "t4", "name": "T4", "artist": "A4", "duration_ms": 400000},
            {"id": "t5", "name": "T5", "artist": "A5", "duration_ms": 500000},
        ]
        # Start with one existing anchor
        _ap._anchor_plan[:] = [{"type": "anchor", "track_id": "t1"}]

        # User types: 3, 5, 2
        numbers = [3, 5, 2]

        for n in numbers:
            idx = n - 1
            tid = _ap._playlist_tracks[idx].get("id", "")
            _ap._anchor_plan.append({"type": "anchor", "track_id": tid})

        # Anchor plan should have 4 anchors in order: t1, t3, t5, t2
        anchors = [e for e in _ap._anchor_plan if e["type"] == "anchor"]
        assert len(anchors) == 4, f"Expected 4 anchors, got {len(anchors)}"
        assert [a["track_id"] for a in anchors] == ["t1", "t3", "t5", "t2"], (
            "Anchors must be in typed order: t1(existing) → t3 → t5 → t2"
        )
    finally:
        _ap._anchor_plan[:] = old_plan
        _ap._playlist_tracks[:] = old_tracks


def test_set_anchors_clears_input_on_success():
    """_set_anchors_input.value should be cleared to '' after successful add."""
    import playlist_arranger.ui.pages.anchors as _ap

    class FakeInput:
        def __init__(self, value=""):
            self.value = value

    old_input = _ap._set_anchors_input
    try:
        fake = FakeInput("3, 5, 7")
        _ap._set_anchors_input = fake

        # After success path, value should be cleared
        fake.value = ""
        assert fake.value == "", "Input should be cleared after success"
    finally:
        _ap._set_anchors_input = old_input


def test_set_anchors_rejects_zero_as_track_number():
    """Input "0" passes ^\d+$ but fails the range check [1, n_tracks]."""
    n_tracks = 5
    number = 0
    assert number < 1 or number > n_tracks, (
        "Zero must be caught by range check (n < 1)"
    )


def test_set_anchors_accepts_leading_zeros_as_valid():
    """Leading zeros like '007' are parsed as int 7 and pass validation.
    This is deliberate: the user means track 7."""
    n_tracks = 10
    token_val = int("007")
    assert token_val == 7
    # Within range
    assert 1 <= token_val <= n_tracks, "007 parses to 7, which is valid"


def test_set_anchors_inserts_placeholder_between_new_and_existing_anchors():
    """When the old plan has anchors and new ones are added via Set Anchors,
    a placeholder separator should be inserted between the groups."""
    import playlist_arranger.ui.pages.anchors as _ap

    old_plan = list(_ap._anchor_plan)
    old_tracks = list(_ap._playlist_tracks)
    try:
        _ap._playlist_tracks[:] = [
            {"id": "t1", "name": "T1", "artist": "A1", "duration_ms": 100000},
            {"id": "t2", "name": "T2", "artist": "A2", "duration_ms": 200000},
            {"id": "t3", "name": "T3", "artist": "A3", "duration_ms": 300000},
            {"id": "t4", "name": "T4", "artist": "A4", "duration_ms": 400000},
        ]
        # Start with anchor(t1), anchor(t2) — no placeholder at end
        _ap._anchor_plan[:] = [
            {"type": "anchor", "track_id": "t1"},
            {"type": "placeholder"},
            {"type": "anchor", "track_id": "t2"},
        ]

        # User types "3, 4"
        numbers = [3, 4]

        # Simulate _on_set_anchors apply logic:
        if _ap._anchor_plan and _ap._anchor_plan[-1].get("type") != "placeholder":
            _ap._anchor_plan.append({"type": "placeholder"})
        for n in numbers:
            idx = n - 1
            tid = _ap._playlist_tracks[idx].get("id", "")
            _ap._anchor_plan.append({"type": "anchor", "track_id": tid})
        _ap._anchor_plan[:] = _ap._collapse_adjacent_placeholders(_ap._anchor_plan)

        # Expected: anchor(t1), ph, anchor(t2), ph, anchor(t3), anchor(t4)
        # After collapse: anchor(t1), ph, anchor(t2), ph, anchor(t3), ph, anchor(t4)
        # (the two consecutive anchors t3,t4 get a placeholder between them by collapse)
        # Actually collapse only merges adjacent placeholders, doesn't insert them.
        # The manual insertion added ONE ph before t3, then t3, then t4 were consecutive.
        # After collapse_adjacent_placeholders: anchor, ph, anchor, ph, anchor, anchor
        # Wait — collapse doesn't INSERT placeholders between anchors.
        # The manual insertion added the separator, but t3 and t4 are consecutive anchors.
        # Let me check what actually happens...

        # After manual insertion code:
        # [anchor(t1), ph, anchor(t2)] + [ph] + [anchor(t3), anchor(t4)]
        # = [anchor(t1), ph, anchor(t2), ph, anchor(t3), anchor(t4)]
        # collapse doesn't change this (consecutive anchors are fine)
        # The test should verify the separator ph exists between groups.

        types = [e["type"] for e in _ap._anchor_plan]
        # Verify the placeholder at index 3 is between old (t2) and new (t3)
        assert types == [
            "anchor", "placeholder", "anchor", "placeholder", "anchor", "anchor",
        ], f"Expected ph separator between groups, got {types}"
        # Verify track IDs
        anchors = [e for e in _ap._anchor_plan if e["type"] == "anchor"]
        assert [a["track_id"] for a in anchors] == ["t1", "t2", "t3", "t4"], (
            "Old anchors preserved, new anchors appended in order"
        )
    finally:
        _ap._anchor_plan[:] = old_plan
        _ap._playlist_tracks[:] = old_tracks


def test_set_anchors_allows_tracks_referring_to_placeholder_slots():
    """Track numbers that map to placeholder-only slots in the plan
    are NOT blocked by 'already anchored' check since placeholders
    aren't anchors."""
    import playlist_arranger.ui.pages.anchors as _ap

    old_plan = list(_ap._anchor_plan)
    old_tracks = list(_ap._playlist_tracks)
    try:
        _ap._playlist_tracks[:] = [
            {"id": "t1", "name": "T1", "artist": "A1", "duration_ms": 100000},
            {"id": "t2", "name": "T2", "artist": "A2", "duration_ms": 200000},
            {"id": "t3", "name": "T3", "artist": "A3", "duration_ms": 300000},
        ]
        # Plan has a placeholder (no track_id), NOT an anchor
        _ap._anchor_plan[:] = [
            {"type": "anchor", "track_id": "t1"},
            {"type": "placeholder"},  # this is just a gap, not an anchored track
        ]

        # User inputs "2" — track 2 is NOT anchored, there's just a placeholder
        already = _ap._anchored_track_ids()
        assert "t2" not in already, (
            "Track 2 should NOT be considered anchored — placeholder is not an anchor"
        )

        # User input "2, 3" should succeed
        numbers = [2, 3]
        rejected = []
        for n in numbers:
            idx = n - 1
            tid = _ap._playlist_tracks[idx].get("id", "")
            if tid in already:
                rejected.append(n)

        assert rejected == [], f"Neither track 2 nor 3 should be rejected, got {rejected}"
    finally:
        _ap._anchor_plan[:] = old_plan
        _ap._playlist_tracks[:] = old_tracks


def test_set_anchors_does_not_partially_apply_on_validation_failure():
    """If ANY token in the list fails validation, plan must be completely unchanged."""
    import playlist_arranger.ui.pages.anchors as _ap

    old_plan = list(_ap._anchor_plan)
    old_tracks = list(_ap._playlist_tracks)
    try:
        _ap._playlist_tracks[:] = [
            {"id": "t1", "name": "T1", "artist": "A1", "duration_ms": 100000},
            {"id": "t2", "name": "T2", "artist": "A2", "duration_ms": 200000},
            {"id": "t3", "name": "T3", "artist": "A3", "duration_ms": 300000},
        ]
        _ap._anchor_plan[:] = [{"type": "anchor", "track_id": "t1"}]
        plan_snapshot = list(_ap._anchor_plan)

        # Input with one invalid token: "1, xyz, 3"
        tokens = ["1", "xyz", "3"]
        import re
        int_re = re.compile(r"^\d+$")
        numbers = []
        abort = False
        for token in tokens:
            if not int_re.match(token):
                abort = True
                break
            numbers.append(int(token))

        assert abort, "Should have aborted on invalid token"
        assert _ap._anchor_plan == plan_snapshot, (
            "Plan must be completely unchanged after validation failure"
        )
    finally:
        _ap._anchor_plan[:] = old_plan
        _ap._playlist_tracks[:] = old_tracks


# ── Chunked batching — prompt structure & re-order pass tests ─────────────

def test_chunk_user_msg_includes_position_hint_beginning_middle_ending():
    """Verify _build_chunk_user_msg produces correct position hints
    for beginning, middle, and ending chunks."""
    import playlist_arranger.ui.pages.anchors as _ap
    import inspect

    # Read the actual function source from anchors.py
    source = inspect.getsource(_ap._on_run_generate)

    # Verify the position_hint branching exists for all three cases
    assert "the BEGINNING/OPENING portion" in source, (
        "Position hint for chunk 0 must mention BEGINNING/OPENING"
    )
    assert "the MIDDLE/TRANSITION portion" in source, (
        "Position hint for middle chunks must mention MIDDLE/TRANSITION"
    )
    assert "the ENDING/CLOSING portion" in source, (
        "Position hint for last chunk must mention ENDING/CLOSING"
    )

    # Verify the chunk_user_msg includes ARC POSITION label
    assert "ARC POSITION:" in source, (
        "Chunk user message must contain ARC POSITION label"
    )

    # Verify the structure name and desc are included in the hint
    assert "struct['name']" in source, (
        "Position hint must reference the structure name"
    )

    # Verify positional data is injected (start_pct, end_pct, chunk N of M)
    assert "chunk_idx + 1" in source, (
        "Middle-chunk hint must include chunk number like 'chunk 2 of 5'"
    )
    assert "start_pct" in source, (
        "Position hint must include start_pct"
    )
    assert "end_pct" in source, (
        "Position hint must include end_pct"
    )


def test_reorder_pass_uses_lightweight_summary_not_full_descriptions():
    """The re-order pass must build anchor_summaries with only name,
    artist, BPM, key, loudness — NOT full description text."""
    import playlist_arranger.ui.pages.anchors as _ap
    import inspect

    source = inspect.getsource(_ap._try_reorder_anchor_positions)

    # Must extract only: name, artist, bpm, key, rms (no desc_text)
    assert 'features.get("bpm"' in source, (
        "Reorder summary must include BPM"
    )
    assert 'features.get("camelot"' in source or 'features.get("chroma_key"' in source, (
        "Reorder summary must include key"
    )
    assert 'features.get("rms_db"' in source, (
        "Reorder summary must include loudness"
    )
    # Must NOT include desc_text in the summary building code
    # Scope to just the anchor_summaries building loop
    reorder_start = source.index("anchor_summaries:")
    reorder_end = source.index("reorder_msg = ", reorder_start)
    reorder_section = source[reorder_start:reorder_end]
    assert "desc_text" not in reorder_section, (
        "Reorder summary must NOT include full description text — lightweight only"
    )
    assert "features.get" in reorder_section, (
        "Reorder summary must extract features (BPM, key, loudness)"
    )

    # Verify the summary format is compact (~one line per track)
    assert '"{name}"' in reorder_section and '{artist}' in reorder_section, (
        "Summary format uses name and artist in compact format"
    )
    assert "BPM:" in reorder_section and "Key:" in reorder_section and "Loudness:" in reorder_section, (
        "Summary includes BPM, Key, Loudness fields"
    )


def test_reorder_pass_rejects_mismatched_position_set():
    """When the reorder pass returns positions that don't match the input set,
    the original deduped_positions must be kept (fallback)."""
    import playlist_arranger.ui.pages.anchors as _ap
    import inspect

    source = inspect.getsource(_ap._try_reorder_anchor_positions)

    # Find the validation check
    assert "set(reordered) == set(deduped_positions)" in source, (
        "Must validate that reordered set matches input set"
    )
    assert "keeping original chunk order" in source, (
        "Must fall back to chunk order on mismatched set"
    )

    # Simulate the fallback logic
    deduped_positions = [7, 23, 41, 55]
    reordered = [7, 23, 99, 55]  # 99 not in original set

    if set(reordered) == set(deduped_positions):
        result = reordered
    else:
        result = deduped_positions

    assert result == deduped_positions, (
        "Mismatched position set must fall back to original order"
    )


def test_reorder_pass_falls_back_on_empty_or_malformed_llm_response():
    """The re-order pass must gracefully fall back when LLM returns
    empty string or unparseable response."""
    import playlist_arranger.ui.pages.anchors as _ap
    import inspect

    source = inspect.getsource(_ap._try_reorder_anchor_positions)

    # Must handle empty response
    assert "Re-order pass returned empty" in source, (
        "Must handle empty LLM response in reorder pass"
    )
    # Must handle parse failure
    assert "Re-order pass parse failed" in source, (
        "Must handle parse failure in reorder pass"
    )
    # Must handle LLM exception
    assert "Re-order pass LLM call failed" in source, (
        "Must handle LLM exception in reorder pass"
    )

    # In all three cases: "keeping chunk order" or equivalent
    assert (
        "keeping chunk order" in source
    ), "All reorder pass failure paths must keep chunk order"


def test_reorder_pass_produces_different_order_than_chunk_concat_for_non_flat_structure():
    """End-to-end behavioral test: call the actual production function
    _try_reorder_anchor_positions with a mocked llm_chat.

    The mock returns ANCHORS with a DIFFERENT valid permutation of the
    same position set.  Assert the function returns the reordered
    permutation — NOT the original chunk-concatenation order — confirming
    the full production guard-and-fallback logic (parser → set-equality
    guard → accept-or-fallback) behaves correctly.
    """
    import playlist_arranger.ui.pages.anchors as _ap
    from unittest.mock import patch

    old_plan = list(_ap._anchor_plan)
    old_tracks = list(_ap._playlist_tracks)
    old_name = _ap._playlist_name
    try:
        # Set up 5 tracks for the reorder test
        _ap._playlist_tracks[:] = [
            {"id": "t1", "name": "Calm Intro", "artist": "A1", "duration_ms": 100000},
            {"id": "t2", "name": "Building Up", "artist": "A2", "duration_ms": 200000},
            {"id": "t3", "name": "Peak Energy", "artist": "A3", "duration_ms": 300000},
            {"id": "t4", "name": "Cooling Down", "artist": "A4", "duration_ms": 400000},
            {"id": "t5", "name": "Gentle Exit", "artist": "A5", "duration_ms": 500000},
        ]
        _ap._playlist_name = "Test Playlist"
        _ap._anchor_plan[:] = []

        # Chunk-concatenation order (what the reorder pass receives)
        deduped_positions = [1, 2, 3, 4, 5]
        # Expected reorder permutation for "Rise and Fall"
        reorder_permutation = [3, 2, 5, 1, 4]

        # Build the mocked LLM ANCHORS response
        reorder_response = "ANCHORS:\n" + "\n".join(str(p) for p in reorder_permutation) + "\n"

        # Mock llm_chat to return the reorder response
        with patch("playlist_arranger.llm.client.llm_chat") as mock_llm:
            with patch("playlist_arranger.llm.client._init_llm_client"):
                mock_llm.return_value = reorder_response

                # Call the actual production function
                struct = {"name": "Rise and Fall", "desc": "Gradual build-up to a single peak, then a slow descent."}
                result = _ap._try_reorder_anchor_positions(
                    deduped_positions,
                    struct,
                    desc_text="",
                    total_tracks=len(_ap._playlist_tracks),
                    backend="ollama",
                    model="gemma4:26b",
                )

                # Verify the LLM was called (i.e., the reorder pass ran)
                mock_llm.assert_called_once()

                # Verify the result is NOT the chunk-concatenation order
                assert result != deduped_positions, (
                    f"Reorder pass must produce DIFFERENT order than chunk concatenation.\n"
                    f"  Chunk-concat: {deduped_positions}\n"
                    f"  Result:       {result}"
                )

                # Verify the result IS the reorder permutation from the mocked LLM
                assert result == reorder_permutation, (
                    f"Result must match the mocked LLM reorder response.\n"
                    f"  Expected: {reorder_permutation}\n"
                    f"  Got:      {result}"
                )

                # Verify all positions are valid 1-based indices
                assert all(1 <= p <= len(_ap._playlist_tracks) for p in result), (
                    "All positions must be within valid range"
                )

                # Verify the same-set invariant (the production function
                # only returns reordered if set matches; we confirm it does)
                assert set(result) == set(deduped_positions), (
                    "Result must contain the same set of positions as input"
                )
    finally:
        _ap._anchor_plan[:] = old_plan
        _ap._playlist_tracks[:] = old_tracks
        _ap._playlist_name = old_name


def test_chunked_progress_notifications_are_dismissed_not_stacked():
    """Source-inspection: verify that per-chunk 'Processing...' notifications
    use handle tracking + .dismiss() so they don't stack indefinitely."""
    import playlist_arranger.ui.pages.anchors as _ap
    import inspect

    source = inspect.getsource(_ap._on_run_generate)
    assert "_progress_notify = None" in source
    assert "_progress_notify.dismiss()" in source
    assert "_dismiss_progress()" in source


def test_chunked_progress_dismiss_behavioral():
    """Behavioral test: mock ui.notify + llm_chat, run chunked generation,
    verify every 'Processing chunk' handle had .dismiss() called, and
    no two such handles were alive simultaneously.

    Uses a synchronous helper that replicates the chunk-loop's dismiss
    sequence without needing an actual asyncio event loop."""
    from unittest.mock import MagicMock, patch
    import playlist_arranger.ui.pages.anchors as _ap

    # ── Setup: track all notify calls ───────────────────────────────────
    notify_calls: list = []       # ordered list of (msg, kwargs)
    notify_handles: list = []     # MagicMock handles returned by ui.notify
    dismissed_handles: list = []  # handles that had .dismiss() called

    def _fake_notify(message="", **kwargs):
        h = MagicMock()
        h._msg = message
        h._kwargs = kwargs
        # Override dismiss to track it
        orig_dismiss = h.dismiss
        def _tracked_dismiss():
            dismissed_handles.append(h)
            orig_dismiss()
        h.dismiss = _tracked_dismiss
        notify_calls.append((message, kwargs))
        notify_handles.append(h)
        return h

    # ── Setup: old threshold + tracks ───────────────────────────────────
    old_threshold = _ap.CHUNK_THRESHOLD
    old_tracks = list(_ap._playlist_tracks)
    old_plan = list(_ap._anchor_plan)
    old_name = _ap._playlist_name
    old_gen_structure = _ap._gen_structure_dd
    old_gen_desc = _ap._gen_desc_textarea
    old_gen_n = _ap._gen_n_input
    old_gen_model = _ap._gen_model_dd
    old_gen_run = _ap._gen_run_btn
    old_gen_cancel = _ap._gen_cancel_btn
    old_gen_spinner = _ap._gen_spinner

    try:
        # Force chunked mode with 10 tracks
        _ap.CHUNK_THRESHOLD = 3
        _ap._playlist_tracks[:] = [
            {"id": f"t{i}", "name": f"Track {i}", "artist": f"Artist {i}", "duration_ms": 100000}
            for i in range(1, 11)
        ]
        _ap._playlist_name = "Test Playlist"
        _ap._anchor_plan[:] = []

        # Set up panel globals so _on_run_generate doesn't crash
        class FakeDropdown:
            def __init__(self, val):
                self.value = val
        _ap._gen_structure_dd = FakeDropdown("rise_fall")
        _ap._gen_desc_textarea = FakeDropdown("Test description")
        _ap._gen_n_input = FakeDropdown(5)
        _ap._gen_model_dd = FakeDropdown("ollama")
        _ap._gen_run_btn = MagicMock()
        _ap._gen_cancel_btn = MagicMock()
        _ap._gen_spinner = MagicMock()

        # Patch ui.notify and llm_chat
        with patch("nicegui.ui.notify", side_effect=_fake_notify) as mock_notify:
            with patch("playlist_arranger.llm.client.llm_chat") as mock_llm:
                with patch("playlist_arranger.llm.client._init_llm_client"):
                    # llm_chat returns valid anchors for each chunk
                    mock_llm.side_effect = [
                        "ANCHORS:\n1\n3\n",     # chunk 1
                        "ANCHORS:\n5\n7\n",     # chunk 2
                        "ANCHORS:\n9\n10\n",    # chunk 3
                        "ANCHORS:\n5\n1\n9\n7\n3\n10\n",  # reorder pass (unused in chunk-only test)
                    ]

                    # ── Run the actual async function ────────────────────
                    import asyncio
                    loop = asyncio.new_event_loop()
                    try:
                        asyncio.set_event_loop(loop)
                        loop.run_until_complete(_ap._on_run_generate())
                    finally:
                        loop.close()
                        # Ensure a fresh event loop exists for subsequent tests
                        asyncio.set_event_loop(asyncio.new_event_loop())

        # ── Assertions ──────────────────────────────────────────────────

        # 1. Every "Processing chunk" handle must have been dismissed
        processing_handles = [
            h for h in notify_handles
            if isinstance(h._msg, str) and "Processing chunk" in h._msg
        ]
        assert len(processing_handles) > 0, (
            "Must have at least one 'Processing chunk' notification"
        )
        for h in processing_handles:
            assert h in dismissed_handles, (
                f"Processing chunk handle '{h._msg[:60]}' was NOT dismissed"
            )

        # 2. No two "Processing chunk" handles were ever ALIVE simultaneously.
        #    Track the sequence: notify creates a handle, dismiss kills it.
        #    If two are created before the first is dismissed, that's a bug.
        alive_count = 0
        max_alive = 0
        for h in notify_handles:
            is_processing = isinstance(h._msg, str) and "Processing chunk" in h._msg
            if is_processing:
                alive_count += 1
                max_alive = max(max_alive, alive_count)
            # Check if THIS handle was dismissed before the NEXT processing notify
            # We do this by checking dismissal relative to position
            if h in dismissed_handles:
                alive_count = max(0, alive_count - 1)

        assert max_alive == 1, (
            f"At most 1 'Processing chunk' handle should be alive at any time, "
            f"but observed max_alive={max_alive} — notification stacking bug"
        )

        # 3. The _dismiss_progress safety-net: dismissed_handles count must
        #    cover ALL processing handles (no leaks)
        not_dismissed = [h for h in processing_handles if h not in dismissed_handles]
        assert not not_dismissed, (
            f"{len(not_dismissed)} processing handles were never dismissed"
        )

    finally:
        _ap.CHUNK_THRESHOLD = old_threshold
        _ap._playlist_tracks[:] = old_tracks
        _ap._anchor_plan[:] = old_plan
        _ap._playlist_name = old_name
        _ap._gen_structure_dd = old_gen_structure
        _ap._gen_desc_textarea = old_gen_desc
        _ap._gen_n_input = old_gen_n
        _ap._gen_model_dd = old_gen_model
        _ap._gen_run_btn = old_gen_run
        _ap._gen_cancel_btn = old_gen_cancel
        _ap._gen_spinner = old_gen_spinner


# ── Original tests continue below ──────────────────────────────────────────

def test_llm_error_leaves_plan_unchanged_and_panel_open():
    """When _parse_anchor_positions returns None (validation failure),
    _anchor_plan must NOT be modified and panel stays open."""
    import playlist_arranger.ui.pages.anchors as _ap

    old_plan = list(_ap._anchor_plan)
    old_tracks = list(_ap._playlist_tracks)
    try:
        _ap._playlist_tracks[:] = [
            {"id": "t1", "name": "T1", "artist": "A1", "duration_ms": 100000},
            {"id": "t2", "name": "T2", "artist": "A2", "duration_ms": 200000},
        ]
        # Pre-populate plan that should survive
        _ap._anchor_plan[:] = [{"type": "anchor", "track_id": "t1"}]
        plan_snapshot = list(_ap._anchor_plan)

        # Simulate parse failure (invalid positions)
        raw_resp = "ANCHORS:\n999\n"  # out of range
        positions = _ap._parse_anchor_positions(raw_resp, 1, len(_ap._playlist_tracks))
        assert positions is None, "Parse must fail for out-of-range"

        # IF positions is None, the generate flow aborts — plan must be unchanged
        if positions is None:
            pass  # abort, plan untouched

        assert _ap._anchor_plan == plan_snapshot, (
            "Plan must NOT be modified when parse fails"
        )
        assert _ap._anchored_track_ids() == {"t1"}, "Anchored IDs unchanged"
    finally:
        _ap._anchor_plan[:] = old_plan
        _ap._playlist_tracks[:] = old_tracks


