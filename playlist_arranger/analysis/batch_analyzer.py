"""Batch analysis sequencer: state globals, sequencing logic, watchdog, and UI drain.

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
    global _batch_watchdog_fired_by_track_id, _batch_btn

    was_processing = False
    with _batch_lock:
        if _batch_processing:
            was_processing = True
            _batch_processing = False
        _batch_current_track_id = None
        _batch_expected_track_id = None
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
    global _batch_track_start_time, _batch_expected_track_id, _batch_watchdog_fired_by_track_id

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
        _batch_current_track_duration_ms = next_track.get("duration_ms", 0)
        _batch_track_start_time = time.time()
        _batch_watchdog_fired_by_track_id = None
        _batch_expected_track_id = tid

        if _state.sp and _state.spotify_device_id:
            try:
                uri = f"spotify:track:{tid}"
                _state.sp.start_playback(device_id=_state.spotify_device_id, uris=[uri])
                logger.info("Batch: started playback of '%s' (id=%s, %d tracks remaining)",
                            next_track.get("name", "?")[:40], tid[:8] if tid else "?",
                            len(queue_snapshot))
            except Exception as e:
                err_msg = str(e)
                is_404 = "404" in err_msg or "device not found" in err_msg.lower()
                is_network = False
                try:
                    import requests
                    if isinstance(e, requests.exceptions.RequestException):
                        is_network = True
                except ImportError:
                    pass
                if is_404:
                    logger.error(
                        "Batch: playback device not found (404) for track '%s' (id=%s) — "
                        "device may have gone offline. Stopping batch analysis.",
                        next_track.get("name", "?")[:40], tid[:8] if tid else "?",
                    )
                    with _ui_pending_lock:
                        _ui_pending_queue.append({
                            "type": "notify",
                            "msg": "Spotify playback device not found — batch analysis stopped. Select a device and restart manually.",
                            "color": "negative",
                        })
                elif is_network:
                    logger.error(
                        "Batch: network error during start_playback for '%s' (id=%s): %s. "
                        "Stopping batch analysis.",
                        next_track.get("name", "?")[:40], tid[:8] if tid else "?", e,
                    )
                    with _ui_pending_lock:
                        _ui_pending_queue.append({
                            "type": "notify",
                            "msg": "Spotify connection lost — batch analysis stopped. Reconnect and restart manually.",
                            "color": "negative",
                        })
                else:
                    logger.error(
                        "Batch: failed to start playback for '%s' (id=%s): %s. "
                        "Stopping batch analysis.",
                        next_track.get("name", "?")[:40], tid[:8] if tid else "?", e,
                    )
                    with _ui_pending_lock:
                        _ui_pending_queue.append({
                            "type": "notify",
                            "msg": "Batch analysis stopped due to playback error. Check logs and restart manually.",
                            "color": "negative",
                        })
                _stop_batch_analysis()
                return

    # Queue UI refresh for main asyncio drain (don't touch UI from background thread)
    with _ui_pending_lock:
        _ui_pending_queue.append({"type": "batch_advance_ui_sync"})


__all__ = [
    "_batch_lock",
    "_batch_processing",
    "_batch_current_track_id",
    "_batch_current_track_duration_ms",
    "_batch_track_start_time",
    "_batch_expected_track_id",
    "_batch_watchdog_fired_by_track_id",
    "_batch_btn",
    "configure",
    "_batch_advance_to_next",
    "_stop_batch_analysis",
]