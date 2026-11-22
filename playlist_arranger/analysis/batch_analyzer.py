"""Batch analysis sequencer: state globals and sequencing logic.

Extracted from playlist_source.py -- pure move, no behavior changes.

Dependency injection: call configure() once from playlist_source.py at module load time
to set callbacks for functions that live in playlist_source (avoiding circular imports).
"""

import threading
import time
import logging

from playlist_arranger.ui.state import analysis_queue_lock as _queue_lock
from playlist_arranger.ui import state as _state

logger = logging.getLogger(__name__)

# --- Batch analysis state globals -----------------------------------------------

_batch_lock = threading.RLock()              # protects ALL _batch_* globals below (reentrant: _batch_advance_to_next -> _stop_batch_analysis/_rebuild_queue_ui)
_batch_processing = False               # True when batch analysis is running
_batch_current_track_id: str | None = None  # track ID currently being played in batch
_batch_current_track_duration_ms = 0     # expected duration of current batch track
_batch_track_start_time = 0.0            # time.time() when batch started current track
_batch_expected_track_id: str | None = None  # track ID we told Spotify to play (interference detection)
_batch_play_issued_at: float = 0.0       # time.time() when the play command was issued (0.0 = not pending)
_batch_watchdog_fired_by_track_id: str | None = None  # prevents duplicate watchdog fires
_batch_btn = None                        # reference to the Start/Stop Batch Analysis button
# NOTE: No frozen snapshot -- batch sequencer operates on the LIVE
# state.analysis_queue directly. Tracks added mid-batch are discovered
# when _batch_advance_to_next() re-checks the queue.

# --- Dependency injection: callbacks set by playlist_source --------------------

_stop_analyzing_fn = None
_stop_listening_fn = None
_safe_pause_playback_fn = None
_rebuild_queue_ui_fn = None
_ui_pending_lock = None
_ui_pending_queue = None


def configure(
    stop_analyzing_fn=None,
    stop_listening_fn=None,
    safe_pause_playback_fn=None,
    rebuild_queue_ui_fn=None,
    ui_pending_lock=None,
    ui_pending_queue=None,
):
    """Set callbacks that batch_analyzer needs from playlist_source (avoids circular import)."""
    global _stop_analyzing_fn, _stop_listening_fn, _safe_pause_playback_fn
    global _rebuild_queue_ui_fn, _ui_pending_lock, _ui_pending_queue
    _stop_analyzing_fn = stop_analyzing_fn
    _stop_listening_fn = stop_listening_fn
    _safe_pause_playback_fn = safe_pause_playback_fn
    _rebuild_queue_ui_fn = rebuild_queue_ui_fn
    _ui_pending_lock = ui_pending_lock
    _ui_pending_queue = ui_pending_queue


# --- Batch sequencer logic -----------------------------------------------------

def _stop_batch_analysis():
    """Stop batch mode, pause playback, stop listen+analyze. Idempotent.

    Safe to call from any thread at any time -- all sub-operations are idempotent.
    Sets _batch_processing=False only if it was True (prevents double button reset
    when called from user click AND drain simultaneously)."""
    global _batch_processing, _batch_current_track_id, _batch_expected_track_id
    global _batch_play_issued_at, _batch_watchdog_fired_by_track_id, _batch_btn

    was_processing = False
    with _batch_lock:
        if _batch_processing:
            was_processing = True
            _batch_processing = False
        _batch_current_track_id = None
        _batch_expected_track_id = None
        _batch_play_issued_at = 0.0
        _batch_watchdog_fired_by_track_id = None

    if _safe_pause_playback_fn:
        _safe_pause_playback_fn()

    if _stop_analyzing_fn:
        _stop_analyzing_fn()
    if _stop_listening_fn:
        _stop_listening_fn()
    _state.analysis_current_track_id = None

    if _batch_btn is not None and was_processing:
        _batch_btn.set_text("Start Batch Analysis")
        _batch_btn.props('color=green')

    logger.info("Batch analysis stopped")
    if _rebuild_queue_ui_fn:
        _rebuild_queue_ui_fn()


def _batch_advance_to_next(expected_track_id: str | None = None):
    """Advance batch sequencer to the NEXT track in the LIVE analysis queue.

    Operates on state.analysis_queue directly (not a frozen snapshot).
    Tracks added to the queue mid-batch become eligible for processing
    in the SAME batch run. _on_analysis_complete() removes completed
    tracks from the queue, so state.analysis_queue[0] naturally points
    to the next unprocessed track after each completion.

    Triggered by the polling thread (_card_listen_thread) when it detects
    that the current batch track has stopped playing (natural end via
    Spotify stopping playback after a single-track queue)."""
    global _batch_processing, _batch_current_track_id, _batch_current_track_duration_ms
    global _batch_track_start_time, _batch_expected_track_id, _batch_play_issued_at
    global _batch_watchdog_fired_by_track_id

    with _batch_lock:
        if not _batch_processing:
            return
        if expected_track_id is not None and expected_track_id != _batch_current_track_id:
            logger.debug("_batch_advance_to_next: stale signal (expected=%s, current=%s) -- ignored",
                         expected_track_id[:8] if expected_track_id else "?",
                         _batch_current_track_id[:8] if _batch_current_track_id else "?")
            return

        # Snapshot the live queue under lock (avoids concurrent mutation races).
        with _queue_lock:
            queue_snapshot = list(_state.analysis_queue)

        # Check if the live queue is empty -- batch complete
        if not queue_snapshot:
            logger.info("Batch: analysis queue is empty -- batch analysis complete")
            _batch_processing = False
            _batch_current_track_id = None
            _batch_expected_track_id = None
            _batch_play_issued_at = 0.0
            _batch_watchdog_fired_by_track_id = None
            # Push to pending queue -- _update_np_ui() drain runs on main thread
            # and calls _stop_batch_analysis() safely (never ui.timer from bg thread).
            with _ui_pending_lock:
                _ui_pending_queue.append({"type": "batch_complete"})
            return

        # Find the next track: first entry in the live queue that isn't the
        # one already mid-processing (which won't be removed until worker finishes).
        next_track = None
        active_track = _batch_current_track_id
        for t in queue_snapshot:
            tid = t.get("id", "")
            if not tid:
                continue
            if tid == active_track and _batch_expected_track_id is None:
                # This is the track we're waiting for the worker to finish on --
                # skip it (it's still in the queue until on_analysis_complete removes it)
                continue
            next_track = t
            break

        if next_track is None:
            logger.info("Batch: no more unprocessed tracks in live queue -- batch analysis complete")
            _batch_processing = False
            _batch_current_track_id = None
            _batch_expected_track_id = None
            _batch_play_issued_at = 0.0
            _batch_watchdog_fired_by_track_id = None
            # Push to pending queue -- _update_np_ui() drain runs on main thread
            # and calls _stop_batch_analysis() safely (never ui.timer from bg thread).
            with _ui_pending_lock:
                _ui_pending_queue.append({"type": "batch_complete"})
            return

        tid = next_track.get("id", "")
        if not tid:
            logger.warning("Batch: queue entry has no ID -- skipping")
            _batch_advance_to_next()
            return

        _batch_current_track_id = tid
        _batch_expected_track_id = tid
        _batch_play_issued_at = time.time()

        if _state.sp and _state.spotify_device_id:
            try:
                from playlist_arranger.sources.spotify_source import play_track_on_device

                uri = f"spotify:track:{tid}"
                path, snapshot = play_track_on_device(_state.sp, uri, _state.spotify_device_id)
                logger.info(
                    "Batch: started playback of '%s' (id=%s, %d tracks remaining, path=%s, snapshot=%r)",
                    next_track.get("name", "?")[:40], tid[:8] if tid else "?",
                    len(queue_snapshot), path, snapshot,
                )
            except Exception as e:
                from playlist_arranger.sources.spotify_source import (
                    spotify_error_details,
                    classify_spotify_error,
                )

                status, reason, msg = spotify_error_details(e)
                category = classify_spotify_error(status, msg, reason=reason)
                logger.error(
                    "Batch: playback failed for '%s' (id=%s) — status=%s, reason=%s, msg=%s. "
                    "Stopping batch analysis.",
                    next_track.get("name", "?")[:40], tid[:8] if tid else "?",
                    status, reason, msg,
                )

                notify_msgs = {
                    "premium_required": "Spotify Premium is required to drive playback — batch analysis stopped.",
                    "device_restricted": "The selected Spotify device rejects remote playback — choose another device and restart batch analysis.",
                    "forbidden": "Spotify returned 403 for the playback request — batch analysis stopped. See log for details.",
                    "not_found": "Spotify playback device not found — batch analysis stopped. Select a device and restart manually.",
                    "network_error": "Spotify connection lost — batch analysis stopped. Reconnect and restart manually.",
                }
                notify_msg = notify_msgs.get(
                    category,
                    "Batch analysis stopped due to playback error. Check logs and restart manually.",
                )
                with _ui_pending_lock:
                    _ui_pending_queue.append({
                        "type": "notify",
                        "msg": notify_msg,
                        "color": "negative",
                    })
                _stop_batch_analysis()
                return

        # Playback command accepted and confirmed — start the per-track clock now.
        _batch_current_track_duration_ms = next_track.get("duration_ms", 0)
        _batch_track_start_time = time.time()
        _batch_watchdog_fired_by_track_id = None

    # Queue UI refresh for main asyncio drain (don't touch UI from background thread)
    with _ui_pending_lock:
        _ui_pending_queue.append({"type": "batch_advance_ui_sync"})


def batch_check_start_timeout() -> bool:
    """Give up if Spotify accepted the play command but never started playing.

    Returns True when the batch was stopped due to a start-confirmation timeout,
    False otherwise.  Safe to call from the polling thread; all UI mutation goes
    through the pending-queue drain.
    """
    from playlist_arranger import config as _cfg

    with _batch_lock:
        if not _batch_processing:
            return False
        if _batch_expected_track_id is None:
            return False
        if _batch_play_issued_at <= 0:
            return False

        elapsed = time.time() - _batch_play_issued_at
        if elapsed <= _cfg.BATCH_START_CONFIRM_TIMEOUT_S:
            return False
        expected = _batch_expected_track_id

    # Cheap best-effort snapshot for the log (never blocks the poll thread long).
    latest_snapshot = None
    try:
        if _state.sp is not None:
            from playlist_arranger.sources.spotify_source import playback_snapshot

            latest_snapshot = playback_snapshot(_state.sp)
    except Exception:
        latest_snapshot = None

    logger.error(
        "Batch: Spotify accepted the play command for track '%s' but never started "
        "playing it after %.1fs — giving up. latest_snapshot=%r",
        expected, elapsed, latest_snapshot,
    )

    with _ui_pending_lock:
        _ui_pending_queue.append({
            "type": "notify",
            "msg": "Spotify accepted the play command but never started playing — batch analysis stopped. Make the Spotify desktop client the active device (press Play in it once) and try again.",
            "color": "negative",
        })

    _stop_batch_analysis()
    return True


__all__ = [
    "_batch_lock",
    "_batch_processing",
    "_batch_current_track_id",
    "_batch_current_track_duration_ms",
    "_batch_track_start_time",
    "_batch_expected_track_id",
    "_batch_play_issued_at",
    "_batch_watchdog_fired_by_track_id",
    "_batch_btn",
    "configure",
    "_batch_advance_to_next",
    "_stop_batch_analysis",
    "batch_check_start_timeout",
]