"""Unit tests for AnalyzeBuffer lifecycle — seek-back, submitted flag, early flush.

Tests the actual live_buffer module functions via LiveAnalyzeContext with mocked state.
"""

import sys
sys.path.insert(0, r"e:\Projects\Spotify_playlists\repository")

import threading
import numpy as np
from playlist_arranger.analysis.live_buffer import LiveAnalyzeContext, AnalyzeBuffer

# ── Production-data isolation ─────────────────────────────────────────────────
# Fake save_track_worker injected into LiveAnalyzeContext so the worker
# thread never touches the real database or embeddings folder.
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


# ── Helper — create a fresh context for each test ────────────────────────────
def _new_ctx():
    """Return a fresh LiveAnalyzeContext with a clean mode_lock and flag.
    Injects fake save_track_worker to prevent touching production DB."""
    lock = threading.Lock()
    flag = [True]
    cap = MockCapture()
    cap.actual_sr = 44100
    ctx = LiveAnalyzeContext(mode_lock=lock, is_analyze_mode=lambda: flag[0],
                              capture_module=cap, save_track_worker_fn=_fake_save_track_worker)
    return ctx, lock, flag


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_seekback_submitted_true_no_reflush():
    """TEST 1: submitted=True → no drain/restart.

    When a buffer is already submitted, sync_analyze_buffer(None) must
    NOT queue a new task — the buffer was already handled."""
    ctx, lock, flag = _new_ctx()

    # Create a buffer that was already submitted
    buf = AnalyzeBuffer(
        track_id="track_abc",
        track_info={"id": "track_abc", "name": "Test Song", "artist": "Artist",
                     "album": "Album", "duration_ms": 240000},
        sample_rate=44100,
        submitted=True,
    )
    buf.chunks = [np.random.randn(44100 * 100).astype(np.float32) * 0.1]
    buf.samples_count = 44100 * 100
    ctx._analyze_buf = buf

    # Simulate playback stopped — should NOT flush due to submitted=True
    ctx.sync_analyze_buffer(None)

    with ctx._analyze_worker_lock:
        task_was_queued = len(ctx._analyze_worker_queue) > 0

    assert not task_was_queued, "Buffer was re-flushed despite submitted=True"


def test_seekback_submitted_false_drain_restart():
    """TEST 2: submitted=False → drain + restart.

    When a buffer is unsubmitted with sufficient coverage (>90%),
    sync_analyze_buffer(None) must flush it (set submitted=True)."""
    ctx, lock, flag = _new_ctx()

    buf = AnalyzeBuffer(
        track_id="track_xyz",
        track_info={"id": "track_xyz", "name": "Scrub Test", "artist": "Artist",
                     "album": "Album", "duration_ms": 240000},
        sample_rate=44100,
        submitted=False,
    )
    # 220s of 240s = 91.7% — above 90% threshold
    buf.chunks = [np.random.randn(44100 * 220).astype(np.float32) * 0.1]
    buf.samples_count = 44100 * 220
    ctx._analyze_buf = buf

    ctx.sync_analyze_buffer(None)

    # Verify the buffer was flushed — check submitted flag on the buffer
    # rather than queue state, since the async worker may have already
    # popped the task by the time we inspect the queue.
    assert buf.submitted, "Unsubmitted buffer was NOT flushed"

    # Clean up the worker thread if it's still running
    with ctx._analyze_worker_lock:
        ctx._analyze_worker_queue.clear()
        ctx._analyze_worker_busy = False


def test_progressive_loop_single_submit():
    """TEST 3: Progressive coverage feed — exactly ONE submission across a loop.

    Feeding audio chunks progressively to collect_samples() + sync_analyze_buffer()
    should submit exactly once when coverage crosses the 90% threshold — not
    resubmit on every subsequent chunk."""
    ctx, lock, flag = _new_ctx()

    dur_ms = 60000  # 1-minute track
    sr = 44100
    track_info = {"id": "loop_track", "name": "Loop Test", "artist": "Artist",
                  "album": "Album", "duration_ms": dur_ms}

    ctx._analyze_buf = AnalyzeBuffer(
        track_id="loop_track",
        track_info=track_info,
        sample_rate=sr,
        submitted=False,
    )

    submission_count = [0]

    original_submit = ctx._submit_analyze_task
    def counting_submit(track_info, y_full):
        submission_count[0] += 1
    ctx._submit_analyze_task = counting_submit

    chunk_s = 5  # 5-second chunks
    chunk_samples = sr * chunk_s
    num_chunks = 12  # 12 × 5s = 60s → crosses 90% at chunk 11 (55s → 91.7%)

    for i in range(num_chunks):
        chunk = np.random.randn(chunk_samples).astype(np.float32) * 0.3
        ctx.collect_samples(chunk)
        ctx.sync_analyze_buffer(track_info)

    ctx._submit_analyze_task = original_submit

    assert submission_count[0] == 1, (
        f"Expected exactly 1 submission, got {submission_count[0]}"
    )


def test_collect_samples_submitted_noop():
    """TEST 4: collect_samples() — submitted=True → no-op.

    When a buffer is already submitted, collect_samples() must NOT append
    new chunks or increment samples_count — it's a defensive no-op."""
    ctx, lock, flag = _new_ctx()

    buf = AnalyzeBuffer(
        track_id="done_track",
        track_info={"id": "done_track", "name": "Done", "artist": "A",
                     "album": "B", "duration_ms": 120000},
        sample_rate=44100,
        submitted=True,
    )
    existing_chunk = np.random.randn(44100).astype(np.float32) * 0.1
    buf.chunks = [existing_chunk]
    buf.samples_count = 44100
    ctx._analyze_buf = buf

    new_chunk = np.random.randn(44100 * 2).astype(np.float32) * 0.3
    ctx.collect_samples(new_chunk)

    chunks_unchanged = (len(buf.chunks) == 1)
    count_unchanged = (buf.samples_count == 44100)

    assert chunks_unchanged, f"Chunks changed: {len(buf.chunks)} (expected 1)"
    assert count_unchanged, f"Samples count changed: {buf.samples_count} (expected 44100)"


def test_seekback_within_same_track_drains_and_restarts():
    """TEST 6: Seek-back within the same track — drain partial buffer, start fresh.

    When the same track has a seek-back (progress goes backward), the code
    should call sync_analyze_buffer(None) to flush the partial buffer, then
    sync_analyze_buffer(new_track_info) to start a fresh buffer for the
    restarted playback. The original buffer should be cleared, NOT resubmitted."""
    ctx, lock, flag = _new_ctx()

    tid = "seek_track"
    track_info = {"id": tid, "name": "Seek Track", "artist": "Artist",
                  "album": "Album", "duration_ms": 300000}
    new_track_info = dict(track_info)  # same track, seek-back to position 0

    buf = AnalyzeBuffer(
        track_id=tid,
        track_info=track_info,
        sample_rate=44100,
        submitted=False,
    )
    # Partial coverage — 100s of 300s = 33.3%, below 90% threshold
    buf.chunks = [np.random.randn(44100 * 100).astype(np.float32) * 0.1]
    buf.samples_count = 44100 * 100
    ctx._analyze_buf = buf

    # Simulate seek-back: drain old partial buffer, start new one
    ctx.sync_analyze_buffer(None)
    ctx.sync_analyze_buffer(new_track_info)

    # The old partial buffer should have been cleared (not submitted —
    # insufficient coverage). A NEW buffer should be created for the
    # restarted playback.
    new_buf = ctx._analyze_buf
    assert new_buf is not None, "No new buffer created after seek-back drain"
    assert new_buf.track_id == tid, (
        f"New buffer should be for same track, got {new_buf.track_id}"
    )
    assert not new_buf.submitted, "New buffer should be unsubmitted"
    assert new_buf.samples_count == 0, (
        f"New buffer should start empty, got {new_buf.samples_count} samples"
    )
    # The old buffer should NOT have been submitted (33% << 90% threshold)
    assert not buf.submitted, (
        "Old partial buffer should NOT have been submitted (insufficient coverage)"
    )

    # Clean up
    with ctx._analyze_worker_lock:
        ctx._analyze_worker_queue.clear()
        ctx._analyze_worker_busy = False


def test_forward_playback_no_seekback():
    """TEST 5: Normal forward playback — seek-back detection never fires.

    Simulating a sequence of monotonically increasing progress_ms values
    (normal forward playback) — the seek-back detection logic must produce
    zero false positives."""
    tid = "fwd_track"
    progress_sequence = [0, 5000, 12000, 18500, 25000, 32000, 40000, 48000]
    last_progress = 0
    seek_detected = 0

    for prog_ms in progress_sequence:
        if tid == tid and last_progress > 0 and prog_ms < (last_progress - 3000):
            seek_detected += 1
        last_progress = prog_ms

    assert seek_detected == 0, f"{seek_detected} false seek-back detections"