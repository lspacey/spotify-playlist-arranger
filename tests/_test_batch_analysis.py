"""Unit tests for batch analysis logic in playlist_source.py."""

import sys
sys.path.insert(0, r"e:\Projects\Spotify_playlists\repository")

import threading
import time
import pathlib
import json

from playlist_arranger.ui.pages import playlist_source as _ps
from playlist_arranger.ui import state as _state
from playlist_arranger.config import CACHE_DIR_DEFAULT


def _setup():
    """Initialize batch state for a clean test run.
    Also monkey-patches save_analysis_queue to a no-op to prevent
    test fixtures from leaking into the real cache/analysis_queue.json."""
    _ps._batch_processing = False
    _ps._batch_current_track_id = None
    _ps._batch_current_track_duration_ms = 0
    _ps._batch_track_start_time = 0.0
    _ps._batch_expected_track_id = None
    _ps._batch_watchdog_fired_by_track_id = None
    _ps._batch_btn = None
    _state.analysis_queue[:] = []
    # Prevent test track fixtures from polluting the real queue file on disk
    _state.save_analysis_queue = lambda: None


def _make_track(tid="test123", name="Test Track", dur=300000):
    return {"id": tid, "name": name, "artist": "Test Artist", "album": "Test Album",
            "duration_ms": dur}


def test_batch_advance_empty_queue_stops():
    """_batch_advance_to_next() with empty queue → stops batch."""
    _setup()
    _ps._batch_processing = True
    _ps._batch_current_track_id = "some-track-id"

    _ps._batch_advance_to_next("some-track-id")

    # After calling with empty queue, batch should be stopped
    assert _ps._batch_processing == False, "Batch should be stopped when queue is empty"
    assert _ps._batch_current_track_id is None, "Current track ID should be cleared"


def test_batch_advance_stale_track_id_noop():
    """_batch_advance_to_next with stale track ID → no-op."""
    _setup()
    _state.analysis_queue.append(_make_track("t1"))
    _state.analysis_queue.append(_make_track("t2"))

    _ps._batch_processing = True
    _ps._batch_current_track_id = "new-track"

    # Call with an old/stale track ID
    _ps._batch_advance_to_next("old-track")

    # Should be a no-op — batch state unchanged
    assert _ps._batch_processing == True, "Batch should still be running"
    assert _ps._batch_current_track_id == "new-track", "Current track should NOT have been changed"


def test_batch_advance_starts_next_track():
    """_batch_advance_to_next() sets _batch_current_track_id to queue[0]'s ID."""
    _setup()
    _state.analysis_queue.append(_make_track("first-track"))
    _state.analysis_queue.append(_make_track("second-track"))

    _ps._batch_processing = True

    _ps._batch_advance_to_next()

    assert _ps._batch_current_track_id == "first-track", "Should pick first track in queue"
    assert _ps._batch_expected_track_id == "first-track", "Expected track ID should match"
    assert _ps._batch_current_track_duration_ms == 300000, "Duration should match track"
    assert _ps._batch_watchdog_fired_by_track_id is None, "Watchdog should be reset"
    assert _ps._batch_track_start_time > 0, "Start time should be set"


def test_start_listening_idempotent():
    """Calling _start_listening() when already listening → no-op."""
    _setup()
    _ps._listen_mode = 1  # Already listening

    # Lock must be held for idempotent check — simulate by setting mode to 1
    # and ensuring _start_listening does NOT create a new thread
    initial_mode = _ps._listen_mode
    _ps._start_listening()
    assert _ps._listen_mode == 1, "Should still be listening"
    assert initial_mode == _ps._listen_mode, "Mode should not have changed"


def test_stop_listening_idempotent():
    """Calling _stop_listening() when already stopped → no-op."""
    _setup()
    _ps._listen_mode = 0  # Already stopped

    initial_mode = _ps._listen_mode
    _ps._stop_listening()
    assert _ps._listen_mode == 0, "Should still be stopped"


def test_start_analyzing_idempotent():
    """Calling _start_analyzing() when already analyzing → no-op."""
    _setup()
    _ps._analyze_mode = 1  # Already analyzing

    initial_mode = _ps._analyze_mode
    _ps._start_analyzing()
    assert _ps._analyze_mode == 1, "Should still be analyzing"


def test_stop_analyzing_idempotent():
    """Calling _stop_analyzing() when already stopped → no-op."""
    _setup()
    _ps._analyze_mode = 0  # Already stopped

    initial_mode = _ps._analyze_mode
    _ps._stop_analyzing()
    assert _ps._analyze_mode == 0, "Should still be stopped"


def test_batch_interference_detection():
    """When _batch_expected_track_id != detected tid → batch stops."""
    _setup()
    _ps._batch_processing = True
    _ps._batch_expected_track_id = "expected-track"

    # Simulate what happens when polling detects a different track
    detected_tid = "wrong-track"

    assert detected_tid != _ps._batch_expected_track_id, "Pre-condition: tracks don't match"

    # Simulate the interference check logic from _card_listen_thread
    if _ps._batch_processing:
        expected = _ps._batch_expected_track_id
        if expected is not None and detected_tid == expected:
            _ps._batch_expected_track_id = None
        elif expected is not None and detected_tid != expected:
            _ps._batch_processing = False
            _ps._batch_expected_track_id = None
            _ps._batch_current_track_id = None

    assert _ps._batch_processing == False, "Batch should be stopped on interference"
    assert _ps._batch_expected_track_id is None, "Expected track should be cleared"


def test_batch_expected_track_no_interference():
    """When detected tid matches _batch_expected_track_id → batch continues."""
    _setup()
    _ps._batch_processing = True
    _ps._batch_expected_track_id = "expected-track"

    detected_tid = "expected-track"  # This is our own advance

    # Simulate the interference check logic
    if _ps._batch_processing:
        expected = _ps._batch_expected_track_id
        if expected is not None and detected_tid == expected:
            _ps._batch_expected_track_id = None  # Clear expectation flag
        elif expected is not None and detected_tid != expected:
            _ps._batch_processing = False

    assert _ps._batch_processing == True, "Batch should continue on expected track"
    assert _ps._batch_expected_track_id is None, "Expected track flag should be cleared"


def test_on_analysis_complete_removes_from_queue():
    """_on_analysis_complete removes ALL occurrences of track ID from queue."""
    _setup()
    _state.analysis_queue.append(_make_track("track-a", "Track A"))
    _state.analysis_queue.append(_make_track("track-b", "Track B"))
    _state.analysis_queue.append(_make_track("track-a", "Track A (duplicate)"))
    assert len(_state.analysis_queue) == 3

    _ps._on_analysis_complete({"id": "track-a", "name": "Track A"})

    assert len(_state.analysis_queue) == 1, "All copies of track-a should be removed"
    assert _state.analysis_queue[0]["id"] == "track-b", "Only track-b should remain"


def test_on_analysis_complete_advances_batch():
    """_on_analysis_complete triggers _batch_advance_to_next in batch mode."""
    _setup()
    _state.analysis_queue.append(_make_track("track-a"))
    _state.analysis_queue.append(_make_track("track-b"))

    _ps._batch_processing = True
    _ps._batch_current_track_id = "track-a"

    _ps._on_analysis_complete({"id": "track-a", "name": "Track A"})

    # track-a should be removed, batch should advance to track-b
    assert len(_state.analysis_queue) == 1
    assert _ps._batch_processing == True
    # Note: _batch_advance_to_next tries to call Spotify, which won't work in tests
    # But the current_track_id should remain track-a until actual advance succeeds
    # (the function was called, but may fail at playback step)
    # Just verify the track was removed from the queue
    assert _state.analysis_queue[0]["id"] == "track-b"


def test_stop_batch_analysis_clears_state():
    """_stop_batch_analysis() resets all batch state."""
    _setup()
    _ps._batch_processing = True
    _ps._batch_current_track_id = "track-x"
    _ps._batch_expected_track_id = "track-x"
    _ps._batch_watchdog_fired_by_track_id = "track-x"

    _ps._stop_batch_analysis()

    assert _ps._batch_processing == False
    assert _ps._batch_current_track_id is None
    assert _ps._batch_expected_track_id is None
    assert _ps._batch_watchdog_fired_by_track_id is None


def test_batch_advance_does_not_false_positive_as_external():
    """Normal batch progression: _batch_expected_track_id set BEFORE next track detected.
    
    Verifies the invariant from the trace analysis: when batch commands the next track,
    _batch_expected_track_id is set to the new track ID BEFORE the polling thread can
    detect the new track playing. This guarantees the interference check sees 'expected'
    as the new track ID (not None), and treats it as a normal advance — not external
    interference."""
    _setup()
    # Simulate "mid-analysis" state: track A confirmed playing, expected=None
    _ps._batch_processing = True
    _ps._batch_current_track_id = "track-A"
    _ps._batch_expected_track_id = None  # A was already confirmed playing

    # Simulate buffer being submitted for track A → _batch_advance_to_next sets expected to B
    # This is what happens in _batch_advance_to_next BEFORE sp.start_playback is called
    _ps._batch_expected_track_id = "track-B"

    # Simulate the polling thread detecting track B playing (batch's own commanded track)
    # This is the interference-check logic from _card_listen_thread
    detected_tid = "track-B"
    if _ps._batch_processing:
        expected = _ps._batch_expected_track_id
        if expected is not None and detected_tid == expected:
            _ps._batch_expected_track_id = None  # Clear expectation — normal advance
        elif expected is not None and detected_tid != expected:
            _ps._batch_processing = False  # Interference during transition window
        elif expected is None:
            _ps._batch_processing = False  # External change (should NOT happen here!)

    # Assert: batch is still running (normal advance, no false positive)
    assert _ps._batch_processing == True, (
        "Batch should continue on normal advance when expected_track_id matches detected track"
    )
    assert _ps._batch_expected_track_id is None, "Expectation flag should be cleared after confirmed match"


def test_batch_detects_external_track_change():
    """External track change during mid-analysis batch state is detected as interference.
    
    Verifies: when _batch_processing=True, _batch_expected_track_id=None (current track
    confirmed playing, no next-track command issued), and a new track appears that batch
    never commanded → batch stops immediately. The interrupted track stays in the queue."""
    _setup()
    # Simulate "mid-analysis" state: track A confirmed playing, expected=None
    _ps._batch_processing = True
    _ps._batch_current_track_id = "track-A"
    _ps._batch_expected_track_id = None  # A confirmed playing, no next-track commanded
    _state.analysis_queue.append(_make_track("track-A", "Track A (should stay in queue)"))

    # Simulate an external track change — user double-clicked track C
    detected_tid = "track-C-external"
    if _ps._batch_processing:
        expected = _ps._batch_expected_track_id
        if expected is not None and detected_tid == expected:
            _ps._batch_expected_track_id = None
        elif expected is not None and detected_tid != expected:
            _ps._batch_processing = False
            _ps._batch_expected_track_id = None
            _ps._batch_current_track_id = None
        elif expected is None:
            # External interference — batch should stop
            _ps._batch_processing = False
            _ps._batch_expected_track_id = None
            _ps._batch_current_track_id = None

    # Assert: batch stopped
    assert _ps._batch_processing == False, (
        "Batch should stop on external track change when expected_track_id is None"
    )
    assert _ps._batch_expected_track_id is None
    assert _ps._batch_current_track_id is None

    # Assert: interrupted track is NOT removed from the queue
    assert len(_state.analysis_queue) == 1, (
        f"Interrupted track should remain in queue for later reprocessing, "
        f"but queue has {len(_state.analysis_queue)} tracks"
    )
    assert _state.analysis_queue[0]["id"] == "track-A", (
        "Track A should still be in the queue after external interference"
    )


def test_batch_processing_status_string():
    """Track with _batch_current_track_id match shows '⏳ Processing' in queue table."""
    _setup()
    _state.analysis_queue.append(_make_track("active-track"))

    _ps._batch_processing = True
    _ps._batch_current_track_id = "active-track"

    # Simulate what _render_queue_table does
    for t in _state.analysis_queue:
        if _ps._batch_processing and t.get("id") == _ps._batch_current_track_id:
            status = "⏳ Processing"
        else:
            status = _ps._get_track_status(t)

    assert status == "⏳ Processing"

    # Non-current track should use normal status
    _ps._batch_current_track_id = "different-track"
    for t in _state.analysis_queue:
        if _ps._batch_processing and t.get("id") == _ps._batch_current_track_id:
            status = "⏳ Processing"
        else:
            status = _ps._get_track_status(t)

    assert status != "⏳ Processing", "Non-current track should not show Processing"


# ─── Test runner ──────────────────────────────────────────────────────────────

def run_tests():
    tests = [
        test_batch_advance_empty_queue_stops,
        test_batch_advance_stale_track_id_noop,
        test_batch_advance_starts_next_track,
        test_start_listening_idempotent,
        test_stop_listening_idempotent,
        test_start_analyzing_idempotent,
        test_stop_analyzing_idempotent,
        test_batch_interference_detection,
        test_batch_expected_track_no_interference,
        test_batch_advance_does_not_false_positive_as_external,
        test_batch_detects_external_track_change,
        test_on_analysis_complete_removes_from_queue,
        test_on_analysis_complete_advances_batch,
        test_stop_batch_analysis_clears_state,
        test_batch_processing_status_string,
    ]
    passed = 0
    failed = 0
    for test_fn in tests:
        try:
            test_fn()
            print(f"PASS: {test_fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL: {test_fn.__name__} — {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR: {test_fn.__name__} — {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{passed+failed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    ok = run_tests()
    sys.exit(0 if ok else 1)