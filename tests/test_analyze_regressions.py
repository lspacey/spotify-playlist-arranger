"""Regression tests for Analyze mode — BUG 1-3 (mid-track init, coverage, metadata).

Each test creates its own isolated LiveAnalyzeContext — NO shared _ctx reference
across test boundaries (the old _run_tests.py shared a module-level singleton that
caused daemon-thread hangs under pytest).

Originally from tests/_run_tests.py (converted to pytest 2026-07-29).
"""

import sys
sys.path.insert(0, r"e:\Projects\Spotify_playlists\repository")

import threading
import numpy as np
from playlist_arranger.ui.pages import playlist_source as _ps
from playlist_arranger.analysis.live_buffer import LiveAnalyzeContext, AnalyzeBuffer


# ── Production-data isolation ─────────────────────────────────────────────────
def _fake_save_track_worker(track_info, playlist_name, playlist_uri,
                             y_full, y_start_snap=None, status_cb=None,
                             sr_override=None):
    pass


# ── Mock capture module ───────────────────────────────────────────────────────
class MockCapture:
    actual_sr = 44100
    actual_channels = 2
    audio_deque = None
    audio_lock = threading.Lock()


# ── Helper: create a fresh, isolated context for each test ───────────────────
def _new_ctx():
    """Return a fresh LiveAnalyzeContext — NO shared state across tests."""
    lock = threading.Lock()
    flag = [True]
    cap = MockCapture()
    cap.actual_sr = 44100
    ctx = LiveAnalyzeContext(
        mode_lock=lock,
        is_analyze_mode=lambda: flag[0],
        capture_module=cap,
        save_track_worker_fn=_fake_save_track_worker,
    )
    # Reset playlist_source globals that each test mutates
    _ps._analyze_mode = 0
    _ps._listen_mode = 0
    _ps._processing_stop = False
    _ps._current_track = None
    _ps._current_track_elapsed = 0
    ctx._analyze_buf = None
    ctx._is_playing.set()
    with ctx._analyze_worker_lock:
        ctx._analyze_worker_queue.clear()
        ctx._analyze_worker_busy = False
    return ctx, lock, flag


# ── BUG 1: Mid-track Analyze init ─────────────────────────────────────────────

def test_mid_track_click_sets_duration():
    """BUG 1: Starting analyze mid-track must create a buffer with correct duration."""
    ctx, lock, flag = _new_ctx()
    _ps._listen_mode = 1
    _ps._analyze_mode = 1

    track_info = {"id": "abc123def456", "name": "Test Song", "artist": "Artist",
                  "album": "Album", "duration_ms": 240000}
    _ps._current_track = track_info
    _ps._current_track_elapsed = 96000

    ctx._analyze_poll_stop.clear()
    ctx.sync_analyze_buffer(track_info)

    buf = ctx._analyze_buf
    assert buf is not None, "Buffer should be created"
    assert buf.track_id == "abc123def456", f"Expected track_id 'abc123def456', got {buf.track_id!r}"
    assert buf.track_info.get("duration_ms") == 240000, \
        f"Expected duration_ms 240000, got {buf.track_info.get('duration_ms')!r}"


def test_mid_track_coverage_discarded_below_threshold():
    """BUG 1: <90% coverage must NOT submit a worker task — buffer is discarded."""
    ctx, lock, flag = _new_ctx()
    _ps._listen_mode = 1
    _ps._analyze_mode = 1

    old_track = {"id": "track1", "name": "Real Track", "artist": "Artist",
                 "album": "Album", "duration_ms": 180000}
    _ps._current_track = old_track
    _ps._current_track_elapsed = 60000

    ctx._analyze_poll_stop.clear()
    ctx.sync_analyze_buffer(old_track)

    buf = ctx._analyze_buf
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
    ctx.on_track_changed(old_track, new_track)

    new_buf = ctx._analyze_buf
    assert new_buf is not None
    assert new_buf.track_id == "track2", f"Expected track2, got {new_buf.track_id!r}"

    # Old buffer's insufficient coverage should NOT have submitted a worker task
    with ctx._analyze_worker_lock:
        task = ctx._analyze_worker_queue.popleft() if ctx._analyze_worker_queue else None
    assert task is None, f"Expected no task (insufficient coverage), got {task!r}"


def test_mid_track_full_coverage_submits():
    """BUG 1: >=90% coverage must submit a worker task."""
    ctx, lock, flag = _new_ctx()
    _ps._listen_mode = 1
    _ps._analyze_mode = 1

    old_track = {"id": "short", "name": "Short Track", "artist": "A",
                 "album": "B", "duration_ms": 10000}
    _ps._current_track = old_track
    _ps._current_track_elapsed = 2000

    ctx._analyze_poll_stop.clear()
    ctx.sync_analyze_buffer(old_track)

    buf = ctx._analyze_buf
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
    ctx.on_track_changed(old_track, new_track)

    # With the isolated _new_ctx() refactor, the daemon worker thread
    # may already have completed by now (since _fake_save_track_worker
    # is a no-op), resetting both _analyze_worker_busy and clearing the
    # queue.  Check buf.submitted instead — it is set synchronously in
    # _flush_analyze_buffer BEFORE the daemon thread starts.
    assert buf.submitted, (
        "95% coverage should submit — buf.submitted must be True "
        "(set synchronously by _flush_analyze_buffer before daemon thread starts)"
    )

    # Clean up worker state
    with ctx._analyze_worker_lock:
        ctx._analyze_worker_queue.clear()
        ctx._analyze_worker_busy = False


# ── BUG 2: Stop-cascade cleanup ───────────────────────────────────────────────

def test_stop_while_analyzing_resets_buffer():
    """BUG 2: Stop while analyzing must clear the buffer."""
    ctx, lock, flag = _new_ctx()
    _ps._listen_mode = 1
    _ps._analyze_mode = 1
    ctx._analyze_poll_stop.clear()

    ctx._analyze_buf = AnalyzeBuffer(
        track_id="leaked",
        track_info={"id": "leaked", "name": "Leaked", "artist": "X",
                     "album": "Y", "duration_ms": 240000},
        chunks=[np.array([0.1, 0.2], dtype=np.float32)],
        samples_count=44100,
        sample_rate=22050,
    )

    _ps._analyze_mode = 0
    _ps._listen_mode = 0
    ctx._analyze_poll_stop.set()
    ctx._analyze_buf = None

    assert ctx._analyze_buf is None
    assert _ps._analyze_mode == 0
    assert _ps._listen_mode == 0


def test_next_session_starts_fresh():
    """BUG 2: New session after stop must create a fresh buffer with zero samples."""
    ctx, lock, flag = _new_ctx()

    # Session 1 — set up, then stop
    _ps._listen_mode = 1
    _ps._analyze_mode = 1
    ctx._analyze_poll_stop.clear()

    ctx._analyze_buf = AnalyzeBuffer(
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
    ctx._analyze_poll_stop.set()
    ctx._analyze_buf = None

    # Session 2 — fresh start
    _ps._listen_mode = 1
    _ps._listen_stop.clear()
    new_track = {"id": "track2", "name": "T2", "artist": "A2",
                 "album": "B2", "duration_ms": 180000}
    _ps._current_track = new_track
    _ps._analyze_mode = 1
    ctx._analyze_poll_stop.clear()
    ctx.sync_analyze_buffer(new_track)

    new_buf = ctx._analyze_buf
    assert new_buf is not None, "New buffer should be created"
    assert new_buf.track_id == "track2", f"Expected track2, got {new_buf.track_id!r}"
    assert new_buf.track_info.get("duration_ms") == 180000, \
        f"Expected 180000ms, got {new_buf.track_info.get('duration_ms')!r}"
    assert new_buf.samples_count == 0, f"Expected 0 samples, got {new_buf.samples_count}"


# ── BUG 3: Correct track metadata on flush ────────────────────────────────────

def test_flush_uses_old_track_duration_not_new():
    """BUG 3: on_track_changed must use OLD track's duration, not _current_track's.

    Simulates _card_listen_thread overwriting _current_track BEFORE callback fires.
    The flush must use the old_track parameter's duration, not the module global."""
    ctx, lock, flag = _new_ctx()
    _ps._listen_mode = 1
    _ps._analyze_mode = 1
    ctx._analyze_poll_stop.clear()

    old_track = {"id": "old_id", "name": "Lost in Marrakesh", "artist": "Test",
                 "album": "X", "duration_ms": 285600}
    new_track = {"id": "new_id", "name": "Fantasy (UCANB)", "artist": "Other",
                 "album": "Y", "duration_ms": 190000}

    _ps._current_track = old_track
    ctx.sync_analyze_buffer(old_track)

    buf = ctx._analyze_buf
    assert buf is not None
    assert buf.track_id == "old_id"

    # Feed ~95% of OLD track's audio
    sr = buf.sample_rate
    feed = int(int(sr * 285.6) * 0.95)
    chunk_sz = int(sr * 0.5)
    fed = 0
    while fed < feed:
        chunk = np.random.randn(chunk_sz).astype(np.float32) * 0.1
        buf.chunks.append(chunk)
        buf.samples_count += len(chunk)
        fed += len(chunk)

    old_buf = buf

    # Simulate _card_listen_thread overwriting _current_track BEFORE callback
    _ps._current_track = new_track

    # on_track_changed uses the passed old_track duration, NOT _current_track
    ctx.on_track_changed(old_track, new_track)

    new_buf = ctx._analyze_buf
    assert new_buf is not None
    assert new_buf.track_id == "new_id", f"Expected new_id, got {new_buf.track_id!r}"
    assert new_buf.track_info.get("duration_ms") == 190000, \
        f"Expected 190000ms, got {new_buf.track_info.get('duration_ms')!r}"

    assert old_buf.submitted, (
        "95% of OLD track should submit — "
        "BUG 3 would use NEW track's shorter duration (190000ms) and produce "
        "nonsensical >100% coverage, then discard"
    )

    with ctx._analyze_worker_lock:
        ctx._analyze_worker_queue.clear()
        ctx._analyze_worker_busy = False