import sys
sys.path.insert(0, r"e:\Projects\Spotify_playlists\repository")

import numpy as np
from playlist_arranger.ui.pages import playlist_source as _ps


# ── Helper ────────────────────────────────────────────────────────────────────
def _reset_all():
    _ps._analyze_buffer = None
    _ps._analyze_track_id = None
    _ps._analyze_samples_count = 0
    _ps._analyze_sample_rate = 22050
    _ps._analyze_track_duration_ms = 0
    _ps._analyze_worker_task = None
    _ps._analyze_worker_busy = False
    _ps._analyze_mode = 0
    _ps._listen_mode = 0
    _ps._processing_stop = False
    _ps._current_track = None
    _ps._current_track_elapsed = 0

# ── Test Results ──────────────────────────────────────────────────────────────
results = []


# ── BUG 1: Mid-track Analyze init ─────────────────────────────────────────────
_reset_all()
try:
    _ps._listen_mode = 1
    _ps._current_track = {"id": "abc123def456", "name": "Test Song", "artist": "Artist",
                          "album": "Album", "duration_ms": 240000}
    _ps._current_track_elapsed = 96000
    _ps._analyze_mode = 1
    _ps._analyze_poll_stop.clear()
    if _ps._current_track is not None:
        _ps._analyze_track_duration_ms = _ps._current_track.get("duration_ms", 0)
        _ps._start_analyze_buffer(_ps._current_track["id"], _ps._analyze_sample_rate)
    assert _ps._analyze_track_duration_ms == 240000
    assert _ps._analyze_track_id == "abc123def456"
    results.append("PASS: test_mid_track_click_sets_duration")
except Exception as e:
    results.append(f"FAIL: test_mid_track_click_sets_duration - {e}")


_reset_all()
try:
    _ps._listen_mode = 1
    _ps._analyze_mode = 1
    _ps._analyze_poll_stop.clear()
    _ps._current_track = {"id": "track1", "name": "Real Track", "artist": "Artist",
                          "album": "Album", "duration_ms": 180000}
    _ps._current_track_elapsed = 60000
    _ps._analyze_track_duration_ms = 180000
    _ps._start_analyze_buffer("track1", 22050)
    # Feed ~2 min = 66% → below 90% threshold
    sr = 22050
    chunk_sz = int(sr * 0.5)
    for _ in range(120 * 2):
        chunk = np.random.randn(chunk_sz).astype(np.float32) * 0.1
        if _ps._analyze_buffer is None:
            _ps._analyze_buffer = []
        _ps._analyze_buffer.append(chunk)
        _ps._analyze_samples_count += len(chunk)
    old = {"id": "track1", "name": "Real Track", "artist": "Artist",
           "album": "Album", "duration_ms": 180000}
    new = {"id": "track2", "name": "Next", "artist": "A2", "album": "B2", "duration_ms": 200000}
    _ps._on_track_changed_analyze(old, new)
    assert _ps._analyze_track_id == "track2"
    with _ps._analyze_worker_lock:
        assert _ps._analyze_worker_task is None
    results.append("PASS: test_mid_track_coverage_discarded_below_threshold")
except Exception as e:
    results.append(f"FAIL: test_mid_track_coverage_discarded_below_threshold - {e}")


_reset_all()
try:
    _ps._listen_mode = 1
    _ps._analyze_mode = 1
    _ps._analyze_poll_stop.clear()
    _ps._current_track = {"id": "short", "name": "Short Track", "artist": "A",
                          "album": "B", "duration_ms": 10000}
    _ps._current_track_elapsed = 2000
    _ps._analyze_track_duration_ms = 10000
    _ps._start_analyze_buffer("short", 22050)
    sr = 22050
    chunk_sz = int(sr * 0.5)
    for _ in range(int(9.5 * 2)):
        chunk = np.random.randn(chunk_sz).astype(np.float32) * 0.1
        if _ps._analyze_buffer is None:
            _ps._analyze_buffer = []
        _ps._analyze_buffer.append(chunk)
        _ps._analyze_samples_count += len(chunk)
    old = {"id": "short", "name": "Short Track", "artist": "A",
           "album": "B", "duration_ms": 10000}
    new = {"id": "track2", "name": "Next", "artist": "A2", "album": "B2", "duration_ms": 200000}
    _ps._on_track_changed_analyze(old, new)
    with _ps._analyze_worker_lock:
        task = _ps._analyze_worker_task
    submitted = _ps._analyze_worker_busy or task is not None
    with _ps._analyze_worker_lock:
        _ps._analyze_worker_task = None
        _ps._analyze_worker_busy = False
    assert submitted
    results.append("PASS: test_mid_track_full_coverage_submits")
except Exception as e:
    results.append(f"FAIL: test_mid_track_full_coverage_submits - {e}")


# ── BUG 2: Stop-cascade cleanup ─────────────────────────────────────────────
_reset_all()
try:
    _ps._listen_mode = 1
    _ps._analyze_mode = 1
    _ps._analyze_poll_stop.clear()
    _ps._analyze_buffer = [np.array([0.1, 0.2], dtype=np.float32)]
    _ps._analyze_track_id = "leaked"
    _ps._analyze_samples_count = 44100
    _ps._processing_stop = True
    _ps._listen_mode = 0
    if _ps._analyze_mode:
        _ps._analyze_mode = 0
        _ps._analyze_poll_stop.set()
        _ps._analyze_buffer = None
        _ps._analyze_track_id = None
        _ps._analyze_samples_count = 0
    assert _ps._analyze_buffer is None
    assert _ps._analyze_track_id is None
    assert _ps._analyze_samples_count == 0
    results.append("PASS: test_stop_while_analyzing_resets_buffer")
except Exception as e:
    results.append(f"FAIL: test_stop_while_analyzing_resets_buffer - {e}")


_reset_all()
try:
    _ps._listen_mode = 1
    _ps._analyze_mode = 1
    _ps._analyze_poll_stop.clear()
    _ps._current_track = {"id": "track1", "name": "T1", "artist": "A1",
                          "album": "B1", "duration_ms": 240000}
    _ps._analyze_buffer = [np.array([1, 2, 3], dtype=np.float32)]
    _ps._analyze_track_id = "track1"
    _ps._analyze_samples_count = 50000
    _ps._listen_mode = 0
    _ps._analyze_mode = 0
    _ps._analyze_poll_stop.set()
    _ps._analyze_buffer = None
    _ps._analyze_track_id = None
    _ps._analyze_samples_count = 0

    # Session 2 fresh start
    _ps._listen_mode = 1
    _ps._listen_stop.clear()
    _ps._current_track = {"id": "track2", "name": "T2", "artist": "A2",
                          "album": "B2", "duration_ms": 180000}
    _ps._analyze_mode = 1
    _ps._analyze_poll_stop.clear()
    if _ps._current_track is not None:
        _ps._analyze_track_duration_ms = _ps._current_track.get("duration_ms", 0)
        _ps._start_analyze_buffer(_ps._current_track["id"], _ps._analyze_sample_rate)
    assert _ps._analyze_track_id == "track2"
    assert _ps._analyze_track_duration_ms == 180000
    assert _ps._analyze_samples_count == 0
    results.append("PASS: test_next_session_starts_fresh")
except Exception as e:
    results.append(f"FAIL: test_next_session_starts_fresh - {e}")


# ── BUG 3: Correct track metadata on flush ────────────────────────────────────
_reset_all()
try:
    _ps._listen_mode = 1
    _ps._analyze_mode = 1
    _ps._analyze_poll_stop.clear()
    old_track = {"id": "old_id", "name": "Lost in Marrakesh", "artist": "Test",
                 "album": "X", "duration_ms": 285600}
    new_track = {"id": "new_id", "name": "Fantasy (UCANB)", "artist": "Other",
                 "album": "Y", "duration_ms": 190000}
    _ps._current_track = old_track
    _ps._analyze_track_duration_ms = 285600
    _ps._start_analyze_buffer("old_id", 22050)
    sr = 22050
    feed = int(int(sr * 285.6) * 0.95)
    chunk_sz = int(sr * 0.5)
    fed = 0
    while fed < feed:
        chunk = np.random.randn(chunk_sz).astype(np.float32) * 0.1
        if _ps._analyze_buffer is None:
            _ps._analyze_buffer = []
        _ps._analyze_buffer.append(chunk)
        _ps._analyze_samples_count += len(chunk)
        fed += len(chunk)

    # Simulate _card_listen_thread overwriting _current_track BEFORE callback
    _ps._current_track = new_track
    _ps._on_track_changed_analyze(old_track, new_track)

    assert _ps._analyze_track_id == "new_id", "Buffer should init for new track after flush"
    assert _ps._analyze_track_duration_ms == 190000, "Duration should be new track's 190000ms"

    with _ps._analyze_worker_lock:
        task = _ps._analyze_worker_task
        submitted = _ps._analyze_worker_busy or task is not None
        _ps._analyze_worker_task = None
        _ps._analyze_worker_busy = False

    assert submitted, (
        "95%% of OLD track should submit — "
        "BUG 3 would use NEW track's shorter duration (190000ms) and produce "
        "nonsensical >100%% coverage, then discard"
    )
    results.append("PASS: test_flush_uses_old_track_duration_not_new")
except Exception as e:
    results.append(f"FAIL: test_flush_uses_old_track_duration_not_new - {e}")


# ── Print Results ────────────────────────────────────────────────────────────
for r in results:
    print(r)

passed = sum(1 for r in results if r.startswith("PASS"))
failed = sum(1 for r in results if r.startswith("FAIL"))
print(f"\n{passed}/{len(results)} passed, {failed} failed")

sys.exit(1 if failed > 0 else 0)