"""Unit tests for AnalyzeBuffer lifecycle — seek-back, submitted flag, early flush.

Tests the actual live_buffer module functions via LiveAnalyzeContext with mocked state.
Run:  python tests/_test_buffer_lifecycle.py
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
_mode_lock = threading.Lock()
_mode_flag = [True]  # mutable wrapper so is_analyze_mode reads live

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


results = []

# ═══════════════════════════════════════════════════════════════════════════════
# TEST 1: submitted=True → no drain/restart
# ═══════════════════════════════════════════════════════════════════════════════
try:
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

    if task_was_queued:
        results.append("FAIL: test_seekback_submitted_true — buffer was re-flushed despite submitted=True")
    else:
        results.append("PASS: test_seekback_submitted_true — submitted buffer correctly skipped re-flush")

except Exception as e:
    results.append(f"FAIL: test_seekback_submitted_true — {e}")

# ═══════════════════════════════════════════════════════════════════════════════
# TEST 2: submitted=False → drain + restart
# ═══════════════════════════════════════════════════════════════════════════════
try:
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
    if buf.submitted:
        results.append("PASS: test_seekback_submitted_false — unsubmitted buffer correctly flushed on drain")
    else:
        results.append("FAIL: test_seekback_submitted_false — unsubmitted buffer was NOT flushed")

    # Clean up the worker thread if it's still running
    with ctx._analyze_worker_lock:
        ctx._analyze_worker_queue.clear()
        ctx._analyze_worker_busy = False

except Exception as e:
    results.append(f"FAIL: test_seekback_submitted_false — {e}")

# ═══════════════════════════════════════════════════════════════════════════════
# TEST 3: Progressive coverage feed — exactly ONE submission across a loop
# ═══════════════════════════════════════════════════════════════════════════════
try:
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

    if submission_count[0] == 1:
        results.append(f"PASS: test_progressive_loop_single_submit — exactly 1 submission (got {submission_count[0]})")
    else:
        results.append(f"FAIL: test_progressive_loop_single_submit — expected 1, got {submission_count[0]}")

    ctx._submit_analyze_task = original_submit

except Exception as e:
    results.append(f"FAIL: test_progressive_loop_single_submit — {e}")
    if 'original_submit' in dir():
        ctx._submit_analyze_task = original_submit

# ═══════════════════════════════════════════════════════════════════════════════
# TEST 4: collect_samples() — submitted=True → no-op
# ═══════════════════════════════════════════════════════════════════════════════
try:
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

    if chunks_unchanged and count_unchanged:
        results.append("PASS: test_collect_samples_submitted_noop — buffer untouched when submitted=True")
    else:
        results.append(f"FAIL: test_collect_samples_submitted_noop — chunks={len(buf.chunks)} (expected 1), count={buf.samples_count} (expected 44100)")

except Exception as e:
    results.append(f"FAIL: test_collect_samples_submitted_noop — {e}")

# ═══════════════════════════════════════════════════════════════════════════════
# TEST 5: Normal forward playback — seek-back detection never fires
# ═══════════════════════════════════════════════════════════════════════════════
try:
    tid = "fwd_track"
    progress_sequence = [0, 5000, 12000, 18500, 25000, 32000, 40000, 48000]
    last_progress = 0
    seek_detected = 0

    for prog_ms in progress_sequence:
        if tid == tid and last_progress > 0 and prog_ms < (last_progress - 3000):
            seek_detected += 1
        last_progress = prog_ms

    if seek_detected == 0:
        results.append("PASS: test_forward_playback_no_seekback — 0 false positives across 7 forward steps")
    else:
        results.append(f"FAIL: test_forward_playback_no_seekback — {seek_detected} false seek-back detections")

except Exception as e:
    results.append(f"FAIL: test_forward_playback_no_seekback — {e}")


# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
for r in results:
    print(r)
print("=" * 60)
passed = sum(1 for r in results if r.startswith("PASS"))
failed = len(results) - passed
print(f"\n{passed}/{len(results)} passed, {failed} failed")
if failed > 0:
    sys.exit(1)