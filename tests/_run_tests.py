"""Regression tests for Analyze mode — BUG 1-3, updated for LiveAnalyzeContext refactor.

NOTE (2026-07-13): All buffer/worker/poll state was extracted from
playlist_source module globals into LiveAnalyzeContext in
playlist_arranger/analysis/live_buffer.py.  Tests now access
everything via _ps._live_ctx.<attr> instead of the removed
_ps._analyze_<attr> globals.
"""

import sys
sys.path.insert(0, r"e:\Projects\Spotify_playlists\repository")

import numpy as np
import dataclasses
from playlist_arranger.ui.pages import playlist_source as _ps
from playlist_arranger.analysis.live_buffer import AnalyzeBuffer

# ── Helper ────────────────────────────────────────────────────────────────────
_ctx = _ps._live_ctx


def _reset_all():
    """Reset both playlist_source globals and live context state."""
    # Module-level globals (still on _ps)
    _ps._analyze_mode = 0
    _ps._listen_mode = 0
    _ps._processing_stop = False
    _ps._current_track = None
    _ps._current_track_elapsed = 0

    # LiveAnalyzeContext state
    _ctx._analyze_buf = None
    _ctx._is_playing.set()  # default: playing
    with _ctx._analyze_worker_lock:
        _ctx._analyze_worker_task = None
        _ctx._analyze_worker_busy = False


# ── Test Results ──────────────────────────────────────────────────────────────
results = []


# ── BUG 1: Mid-track Analyze init ─────────────────────────────────────────────
def test_mid_track_click_sets_duration():
    _reset_all()
    _ps._listen_mode = 1
    _ps._analyze_mode = 1

    track_info = {"id": "abc123def456", "name": "Test Song", "artist": "Artist",
                  "album": "Album", "duration_ms": 240000}
    _ps._current_track = track_info
    _ps._current_track_elapsed = 96000

    # Simulate what on_analyze_click → start_poll_thread + sync does:
    _ctx._analyze_poll_stop.clear()
    _ctx.sync_analyze_buffer(track_info)

    buf = _ctx._analyze_buf
    assert buf is not None, "Buffer should be created"
    assert buf.track_id == "abc123def456", f"Expected track_id 'abc123def456', got {buf.track_id!r}"
    assert buf.track_info.get("duration_ms") == 240000, \
        f"Expected duration_ms 240000, got {buf.track_info.get('duration_ms')!r}"


def test_mid_track_coverage_discarded_below_threshold():
    _reset_all()
    _ps._listen_mode = 1
    _ps._analyze_mode = 1

    old_track = {"id": "track1", "name": "Real Track", "artist": "Artist",
                 "album": "Album", "duration_ms": 180000}
    _ps._current_track = old_track
    _ps._current_track_elapsed = 60000

    _ctx._analyze_poll_stop.clear()
    _ctx.sync_analyze_buffer(old_track)

    buf = _ctx._analyze_buf
    assert buf is not None

    # Feed ~2 min of audio → 66% coverage (below 90% threshold)
    sr = buf.sample_rate
    chunk_sz = int(sr * 0.5)
    for _ in range(120 * 2):
        chunk = np.random.randn(chunk_sz).astype(np.float32) * 0.1
        buf.chunks.append(chunk)
        buf.samples_count += len(chunk)

    new_track = {"id": "track2", "name": "Next", "artist": "A2",
                 "album": "B2", "duration_ms": 200000}
    _ctx.on_track_changed(old_track, new_track)

    # After track change → old buffer flushed, new buffer started for new track
    new_buf = _ctx._analyze_buf
    assert new_buf is not None
    assert new_buf.track_id == "track2", f"Expected track2, got {new_buf.track_id!r}"

    # Old buffer's insufficient coverage should NOT have submitted a worker task
    with _ctx._analyze_worker_lock:
        task = _ctx._analyze_worker_task
    assert task is None, f"Expected no task (insufficient coverage), got {task!r}"


def test_mid_track_full_coverage_submits():
    _reset_all()
    _ps._listen_mode = 1
    _ps._analyze_mode = 1

    old_track = {"id": "short", "name": "Short Track", "artist": "A",
                 "album": "B", "duration_ms": 10000}
    _ps._current_track = old_track
    _ps._current_track_elapsed = 2000

    _ctx._analyze_poll_stop.clear()
    _ctx.sync_analyze_buffer(old_track)

    buf = _ctx._analyze_buf
    assert buf is not None

    # Feed ~9.5s of audio → 95% coverage (above 90% threshold)
    sr = buf.sample_rate
    chunk_sz = int(sr * 0.5)
    for _ in range(int(9.5 * 2)):
        chunk = np.random.randn(chunk_sz).astype(np.float32) * 0.1
        buf.chunks.append(chunk)
        buf.samples_count += len(chunk)

    new_track = {"id": "track2", "name": "Next", "artist": "A2",
                 "album": "B2", "duration_ms": 200000}
    _ctx.on_track_changed(old_track, new_track)

    # 95% coverage should have submitted a worker task
    with _ctx._analyze_worker_lock:
        task = _ctx._analyze_worker_task
        submitted = _ctx._analyze_worker_busy or task is not None
        # Cleanup
        _ctx._analyze_worker_task = None
        _ctx._analyze_worker_busy = False

    assert submitted, "95% coverage should submit — got no worker task"


# ── BUG 2: Stop-cascade cleanup ─────────────────────────────────────────────
def test_stop_while_analyzing_resets_buffer():
    _reset_all()
    _ps._listen_mode = 1
    _ps._analyze_mode = 1
    _ctx._analyze_poll_stop.clear()

    # Create a buffer with some audio
    _ctx._analyze_buf = AnalyzeBuffer(
        track_id="leaked",
        track_info={"id": "leaked", "name": "Leaked", "artist": "X",
                     "album": "Y", "duration_ms": 240000},
        chunks=[np.array([0.1, 0.2], dtype=np.float32)],
        samples_count=44100,
        sample_rate=22050,
    )

    # Simulate stop: mode off, poll stop, buffer cleared
    _ps._analyze_mode = 0
    _ps._listen_mode = 0
    _ctx._analyze_poll_stop.set()
    _ctx._analyze_buf = None

    assert _ctx._analyze_buf is None
    assert _ps._analyze_mode == 0
    assert _ps._listen_mode == 0


def test_next_session_starts_fresh():
    _reset_all()

    # Session 1 — set up, then stop
    _ps._listen_mode = 1
    _ps._analyze_mode = 1
    _ctx._analyze_poll_stop.clear()

    _ctx._analyze_buf = AnalyzeBuffer(
        track_id="track1",
        track_info={"id": "track1", "name": "T1", "artist": "A1",
                     "album": "B1", "duration_ms": 240000},
        chunks=[np.array([1, 2, 3], dtype=np.float32)],
        samples_count=50000,
        sample_rate=22050,
    )
    _ps._current_track = {"id": "track1", "name": "T1", "artist": "A1",
                          "album": "B1", "duration_ms": 240000}

    # Stop session 1
    _ps._listen_mode = 0
    _ps._analyze_mode = 0
    _ctx._analyze_poll_stop.set()
    _ctx._analyze_buf = None

    # Session 2 — fresh start
    _ps._listen_mode = 1
    _ps._listen_stop.clear()
    new_track = {"id": "track2", "name": "T2", "artist": "A2",
                 "album": "B2", "duration_ms": 180000}
    _ps._current_track = new_track
    _ps._analyze_mode = 1
    _ctx._analyze_poll_stop.clear()
    _ctx.sync_analyze_buffer(new_track)

    new_buf = _ctx._analyze_buf
    assert new_buf is not None, "New buffer should be created"
    assert new_buf.track_id == "track2", f"Expected track2, got {new_buf.track_id!r}"
    assert new_buf.track_info.get("duration_ms") == 180000, \
        f"Expected 180000ms, got {new_buf.track_info.get('duration_ms')!r}"
    assert new_buf.samples_count == 0, f"Expected 0 samples, got {new_buf.samples_count}"


# ── BUG 3: Correct track metadata on flush ────────────────────────────────────
def test_flush_uses_old_track_duration_not_new():
    _reset_all()
    _ps._listen_mode = 1
    _ps._analyze_mode = 1
    _ctx._analyze_poll_stop.clear()

    old_track = {"id": "old_id", "name": "Lost in Marrakesh", "artist": "Test",
                 "album": "X", "duration_ms": 285600}
    new_track = {"id": "new_id", "name": "Fantasy (UCANB)", "artist": "Other",
                 "album": "Y", "duration_ms": 190000}

    _ps._current_track = old_track
    _ctx.sync_analyze_buffer(old_track)

    buf = _ctx._analyze_buf
    assert buf is not None
    assert buf.track_id == "old_id"

    # Feed ~95% of OLD track's audio (271320ms worth)
    sr = buf.sample_rate
    feed = int(int(sr * 285.6) * 0.95)
    chunk_sz = int(sr * 0.5)
    fed = 0
    while fed < feed:
        chunk = np.random.randn(chunk_sz).astype(np.float32) * 0.1
        buf.chunks.append(chunk)
        buf.samples_count += len(chunk)
        fed += len(chunk)

    # Simulate _card_listen_thread overwriting _current_track BEFORE callback
    _ps._current_track = new_track

    # on_track_changed uses the passed old_track duration, NOT _current_track
    _ctx.on_track_changed(old_track, new_track)

    # Buffer should now be for the new track
    new_buf = _ctx._analyze_buf
    assert new_buf is not None
    assert new_buf.track_id == "new_id", \
        f"Expected new_id, got {new_buf.track_id!r}"
    assert new_buf.track_info.get("duration_ms") == 190000, \
        f"Expected 190000ms, got {new_buf.track_info.get('duration_ms')!r}"

    # The old buffer (95% of 285600ms) should have submitted
    with _ctx._analyze_worker_lock:
        task = _ctx._analyze_worker_task
        submitted = _ctx._analyze_worker_busy or task is not None
        # Cleanup
        _ctx._analyze_worker_task = None
        _ctx._analyze_worker_busy = False

    assert submitted, (
        "95% of OLD track should submit — "
        "BUG 3 would use NEW track's shorter duration (190000ms) and produce "
        "nonsensical >100% coverage, then discard"
    )


# ── Run all tests ─────────────────────────────────────────────────────────────
tests = [
    ("test_mid_track_click_sets_duration", test_mid_track_click_sets_duration),
    ("test_mid_track_coverage_discarded_below_threshold", test_mid_track_coverage_discarded_below_threshold),
    ("test_mid_track_full_coverage_submits", test_mid_track_full_coverage_submits),
    ("test_stop_while_analyzing_resets_buffer", test_stop_while_analyzing_resets_buffer),
    ("test_next_session_starts_fresh", test_next_session_starts_fresh),
    ("test_flush_uses_old_track_duration_not_new", test_flush_uses_old_track_duration_not_new),
]

for name, fn in tests:
    try:
        fn()
        results.append(f"PASS: {name}")
    except Exception as e:
        results.append(f"FAIL: {name} - {e}")

# ── Print Results ────────────────────────────────────────────────────────────
for r in results:
    print(r)

passed = sum(1 for r in results if r.startswith("PASS"))
failed = sum(1 for r in results if r.startswith("FAIL"))
print(f"\n{passed}/{len(results)} passed, {failed} failed")

sys.exit(1 if failed > 0 else 0)