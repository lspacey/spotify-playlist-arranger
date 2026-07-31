"""Playlist source selection: Spotify / Local files."""

import collections
import contextlib
import dataclasses
import pathlib
import asyncio
import json
import glob as _glob
import threading
import time
import logging

from nicegui import ui, app

from playlist_arranger.ui import state as _state
from playlist_arranger.cache.store import backup_exists as _backup_exists
from playlist_arranger.database import db as _db
from playlist_arranger.config import load_settings, CACHE_DIR_DEFAULT

from playlist_arranger.analysis import batch_analyzer as _ba
from playlist_arranger.analysis.desc_status import get_desc_age_info
from playlist_arranger.analysis import desc_generator as _desc_gen
from playlist_arranger.ui.desc_dialog import show_desc_dialog
from playlist_arranger.ui import audio_viz as _viz
from playlist_arranger.ui import playlist_highlight as _ph
from playlist_arranger.sources import playlist_cache as _pc
from playlist_arranger.ui import desc_status as _ds
from playlist_arranger.ui import local_section as _ls
from playlist_arranger.ui import analysis_queue as _aq
from playlist_arranger.ui import track_table as _tt
# ---- Wire dependency injection (circular-import avoidance) ----
logger = logging.getLogger(__name__)

# ─── Description icon click handler (wired per-table via $parent.$emit) ───────

def _on_desc_icon_click(e):
    """Delegate to ``playlist_arranger.ui.track_table.on_desc_icon_click()``."""
    _tt.on_desc_icon_click(e)


# ─── "Now Playing" card mode state ────────────────────────────────────────────
_listen_mode = 0        # 0 = idle, 1 = listening
_analyze_mode = 0       # 0 = idle, 1 = analyzing
_listen_thread = None   # background daemon thread
_listen_stop = threading.Event()
_mode_lock = threading.Lock()

# Track being listened to / analyzed
_current_track = None       # {"id", "name", "artist", "duration_ms", "album"}
_current_track_elapsed = 0  # ms
_current_track_status = ""  # human-readable status line

# Stop-button processing state
_processing_stop = False    # True between stop-click and thread cleanup finish

# NOTE: Batch analysis state globals now live in playlist_arranger.analysis.batch_analyzer.
# Imported as _ba at top of file.  All code below must reference _ba._batch_* — the
# explicit module prefix prevents accidental shadowing by local/global variable scoping.
# No frozen snapshot — batch sequencer operates on the LIVE state.analysis_queue
# directly.  Tracks added mid-batch are discovered when _ba._batch_advance_to_next()
# re-checks the queue.
# Queue-based UI updates from background threads → main asyncio timer drain
_ui_pending_queue = collections.deque()
_ui_pending_lock = threading.Lock()
_ui_context_lock = threading.Lock()          # serialises remaining with _page_client: from deferred bg-thread sites (_notify_playing_track, _on_analysis_complete)
_needs_queue_highlight = False               # flag: set by batch_advance_ui_sync drain, consumed after rebuild


# _ph._sync_now_playing_row_highlight() moved to playlist_arranger/ui/playlist_highlight.py.
# Imported as _ph.  All calls below use _ph._sync_now_playing_row_highlight(...).


# ─── Live analyze context (moved to playlist_arranger/analysis/live_buffer.py) ──
from playlist_arranger.analysis.live_buffer import LiveAnalyzeContext
from playlist_arranger.audio import capture as _cap  # live module ref

# Shared lock — used by both playlist_source.py UI/poll threads and
# LiveAnalyzeContext collect_samples() + sync_analyze_buffer() internally.
_live_ctx = LiveAnalyzeContext(
    mode_lock=_mode_lock,
    is_analyze_mode=lambda: _analyze_mode == 1,
    capture_module=_cap,
)

# Track change callback — set below after _on_track_changed_cb is defined
_on_track_changed_cb = None  # callable(old_track_info, new_track_info) or None
_live_ctx.on_track_changed_cb = _on_track_changed_cb

# Analysis-complete callback — auto-removes track from queue after successful save
def _on_analysis_complete(track_info: dict):
    """Called from analyze worker thread after save_track_worker succeeds.
    Removes ALL occurrences of the tracked track ID from the analysis queue."""
    tid = track_info.get("id", "") if isinstance(track_info, dict) else ""
    if not tid:
        return
    with _state.analysis_queue_lock:
        before = len(_state.analysis_queue)
        _state.analysis_queue[:] = [t for t in _state.analysis_queue if t.get("id") != tid]
        removed = before - len(_state.analysis_queue)
    # Lock released — all following is outside critical section.
    if removed == 0:
        # Track already removed by user (batch advance is decoupled — polling
        # thread handles track-changed detection independently).
        return
    _state.save_analysis_queue()
    if _state.analysis_current_track_id == tid:
        _state.analysis_current_track_id = None
    logger.info("Auto-removed %d occurrence(s) of '%s' from analysis queue (after successful analysis)",
                removed, track_info.get("name", "?")[:40])
    # _page_client now accessed via _ph._page_client
    if _ph._page_client is not None and getattr(_ph._page_client, 'has_socket_connection', False):
        try:
            with _ui_context_lock, _ph._page_client:
                _update_queue_label()
                _rebuild_queue_ui()
                ui.notify(f"'{track_info.get('name', '?')[:30]}' removed from queue (analysis complete)",
                          type="positive")
        except RuntimeError:
            logger.debug("_on_analysis_complete: client disconnected mid-callback")
        except Exception:
            logger.exception("Failed to rebuild queue UI from analysis-complete callback")

# Buffer-submitted callback: set processing status ONLY when worker starts
# after coverage check passes (not when playback begins).
def _on_buffer_submitted(track_info: dict):
    tid = track_info.get("id", "") if isinstance(track_info, dict) else ""
    if tid:
        _state.analysis_current_track_id = tid
        logger.debug("Worker started processing: %s", track_info.get("name", "?")[:30])

def _on_buffer_discarded(track_info: dict):
    tid = track_info.get("id", "") if isinstance(track_info, dict) else ""
    if tid and _state.analysis_current_track_id == tid:
        _state.analysis_current_track_id = None
        logger.debug("Worker discarded (insufficient coverage): %s",
                     track_info.get("name", "?")[:30] if track_info else "?")

_live_ctx.on_buffer_submitted_cb = _on_buffer_submitted
_live_ctx.on_buffer_discarded_cb = _on_buffer_discarded
_live_ctx.on_analysis_complete_cb = _on_analysis_complete

# ── Description generated callback (worker thread → UI refresh) ─────────────
def _on_desc_generated(track_id: str):
    """Called from desc_generator worker after a description is written to DB.
    Triggers table refresh for both playlist and queue tables, and live-updates
    the description dialog if it's still open for this track."""
    if not track_id:
        return
    logger.debug("Description generated for track_id=%s", track_id[:8] if track_id else "?")
    if _ph._page_client is not None and getattr(_ph._page_client, 'has_socket_connection', False):
        try:
            with _ui_context_lock, _ph._page_client:
                # Refresh queue table if visible
                _rebuild_queue_ui()
                # Refresh currently-displayed playlist table (desc icon + data)
                for pid, rows_cache in _ph._playlist_rows_cache.items():
                    try:
                        table_ref = _ph._playlist_tables.get(pid)
                        if table_ref is None:
                            continue
                        # Update desc_age info for matching row IN PLACE, then
                        # call .update() (cheaper than full .rows reassignment).
                        updated = False
                        for row in rows_cache:
                            try:
                                if row.get("track_id") == track_id:
                                    entry = _db.get_track(track_id)
                                    desc_info = get_desc_age_info(
                                        entry.get("desc_text") if entry else None,
                                        entry.get("desc_generated_at") if entry else None,
                                    )
                                    row["desc_icon"] = "auto_stories" if desc_info.has_desc else "menu_book"
                                    row["desc_color"] = desc_info.color
                                    row["desc_caption"] = desc_info.caption
                                    row["desc"] = "✓" if desc_info.has_desc else "—"
                                    updated = True
                                    break
                            except Exception:
                                logger.debug("Failed to update desc row in playlist %s for track %s",
                                             pid[:8] if pid else "?", track_id[:8] if track_id else "?")
                        if updated:
                            try:
                                table_ref.update()
                            except Exception:
                                logger.debug("table_ref.update() failed for playlist %s after desc generation",
                                             pid[:8] if pid else "?")
                    except Exception:
                        logger.debug("Failed to refresh playlist table %s after desc generation", pid[:8] if pid else "?")

                # ── Live-update: push new description into open dialog ────
                _push_desc_to_open_dialog(track_id)

                ui.notify("Description generated and saved", type="positive")
        except Exception:
            logger.exception("_on_desc_generated callback failed for %s", track_id[:8] if track_id else "?")


def _push_desc_to_open_dialog(track_id: str):
    """If the desc dialog for *track_id* is currently open, push its new
    description text from the DB into the live textarea element.

    Guards against stale/closed dialogs: only acts when
    ``desc_dialog._current_open_track_id == track_id`` and the stored
    textarea reference is still valid (non-None, has a ``.value`` attribute).
    """
    try:
        from playlist_arranger.ui import desc_dialog as _dd
    except ImportError:
        return

    if _dd._current_open_track_id != track_id:
        return  # dialog is closed or showing a DIFFERENT track — don't overwrite

    textarea = getattr(_dd, "_current_textarea", None)
    if textarea is None:
        return  # dialog was closed between check and access

    # Read fresh description from DB
    entry = _db.get_track(track_id)
    desc_text = entry.get("desc_text") if isinstance(entry, dict) else None
    if not desc_text:
        return  # nothing to push

    # Update the textarea value (NiceGUI reactive binding)
    try:
        textarea.value = desc_text
        logger.debug("Pushed new description into open dialog for track_id=%s",
                     track_id[:8] if track_id else "?")
    except Exception:
        logger.exception("Failed to push description into open dialog for %s",
                         track_id[:8] if track_id else "?")

    # ── Also update the "Generated: ..." timestamp label ────────────────
    generated_label = getattr(_dd, "_current_generated_label", None)
    if generated_label is not None:
        desc_generated_at = entry.get("desc_generated_at") if isinstance(entry, dict) else None
        if desc_generated_at and isinstance(desc_generated_at, str) and desc_generated_at.strip():
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(desc_generated_at)
                if dt.tzinfo is None:
                    generated_str = dt.strftime("%Y-%m-%d %H:%M")
                else:
                    generated_str = dt.astimezone().strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                generated_str = desc_generated_at
        else:
            generated_str = "Never generated"
        try:
            generated_label.set_text(f"Generated: {generated_str}")
        except Exception:
            logger.exception("Failed to update generated-at label for %s",
                             track_id[:8] if track_id else "?")

_desc_gen.set_on_desc_generated(_on_desc_generated)
# Batch advance is NOT coupled to buffer submission/discard callbacks.
# Instead, batch advance is triggered by the polling thread (_card_listen_thread)
# when it detects that the current batch track has stopped playing (natural end).
# The worker (_on_analysis_complete) handles queue removal and status updates independently.

# ─── Idempotent listen/analyze helpers ───────────────────────────────────────
def _start_listening():
    """Start the listen polling thread. Idempotent."""
    global _listen_mode, _listen_thread, _listen_stop
    with _mode_lock:
        if _listen_mode == 1:
            return
        _listen_mode = 1
        _listen_stop.clear()
        _listen_thread = threading.Thread(target=_card_listen_thread, daemon=True)
        _listen_thread.start()
        logger.info("Now Playing: listening started")


def _stop_listening():
    """Stop the listen polling thread. Idempotent."""
    global _listen_mode, _listen_stop, _processing_stop, _analyze_mode
    was_analyzing = False
    with _mode_lock:
        if _listen_mode == 0:
            return
        _processing_stop = True
        _listen_mode = 0
        if _analyze_mode:
            _analyze_mode = 0
            was_analyzing = True
        if _btn_listen is not None:
            _btn_listen.props('color=orange')
            _btn_listen.set_text("Stopping...")
            _btn_listen.set_enabled(False)
        _listen_stop.set()
    # Lock released BEFORE calling flush/stop — avoids deadlock:
    # flush_before_stop() internally acquires self.mode_lock (same object as
    # _mode_lock).  If _mode_lock is a non-reentrant threading.Lock, a
    # nested acquire from the same thread blocks indefinitely.
    if was_analyzing:
        _live_ctx.flush_before_stop()
        _live_ctx.stop_poll_thread()
        logger.info("Analyze mode stopped: Listen stopped")


def _start_analyzing():
    """Start the analyze poll thread. Idempotent."""
    global _analyze_mode
    with _mode_lock:
        if _listen_mode != 1:
            return
        if _analyze_mode == 1:
            return
        _analyze_mode = 1
        _live_ctx.start_poll_thread()
        logger.info("Now Playing: analysis started")


def _stop_analyzing():
    """Stop the analyze poll thread. Idempotent.
    
    Flushes the current buffer BEFORE stopping the poll thread to ensure
    collected audio data is not discarded."""
    global _analyze_mode
    was_analyzing = False
    with _mode_lock:
        if _analyze_mode == 0:
            return
        _analyze_mode = 0
        was_analyzing = True
    # Lock released BEFORE calling flush/stop — avoids deadlock:
    # flush_before_stop() internally acquires self.mode_lock (same object as
    # _mode_lock).  If _mode_lock is a non-reentrant threading.Lock, a
    # nested acquire from the same thread blocks indefinitely.
    if was_analyzing:
        _live_ctx.flush_before_stop()
        _live_ctx.stop_poll_thread()
        # Clear simple-analyze queue highlight (batch highlight is separate)
        _aq._queue_now_playing_row_keys.clear()
        if _aq._queue_table_ref is not None and not _ba._batch_processing:
            _aq._queue_table_ref.selected = []
        logger.info("Now Playing: analysis stopped")


# UI element references (updated by polling thread)
_track_name_label = None
_track_progress_label = None
_track_status_label = None
_btn_listen = None
_btn_analyze = None
_np_card_container = None
_api_counter_label = None

# Viz-related global state (moved to playlist_arranger/ui/audio_viz.py).
# Imported as _viz at top of file. All code below must reference _viz._viz_*
# or _viz._update_viz() — no bare _viz_* references should remain.

# _playlist_expansions and _ph._auto_expanded_playlist_ids moved to
# playlist_arranger/ui/playlist_highlight.py.  Access via _ph._playlist_expansions etc.

# _page_client and _last_notified_playlist_id moved to
# playlist_arranger/ui/playlist_highlight.py.  Access via _ph._page_client etc.

# _playlist_tables, _playlist_rows_cache, _now_playing_row_keys moved to
# playlist_arranger/ui/playlist_highlight.py.  Access via _ph._playlist_tables etc.


# _auto_expand_playlist() and _collapse_playlist() moved to
# playlist_arranger/ui/playlist_highlight.py.  Use _ph._auto_expand_playlist() etc.


# _ph._notify_playing_track() moved to playlist_arranger/ui/playlist_highlight.py.
# Use _ph._notify_playing_track(...).

# ─── Card logic: background polling thread (+ batch interference detection) ───
def _card_listen_thread():
    """Background thread: polls Spotify API for track info + highlight only."""
    global _current_track, _current_track_elapsed, _current_track_status
    global _listen_mode, _analyze_mode, _listen_stop, _processing_stop
    # _page_client now accessed via _ph._page_client
    from playlist_arranger.config import POLL_FAST, POLL_NORMAL

    last_track_id = None
    last_progress_ms = 0
    last_playing_time = 0.0
    _last_highlighted_id = None
    _last_highlighted_pl = None

    while not _listen_stop.is_set():
        try:
            with _mode_lock:
                lm = _listen_mode

            if lm == 0:
                time.sleep(1.0)
                _current_track = None
                _current_track_elapsed = 0
                _current_track_status = ""
                continue

            try:
                cp = _state.sp.current_playback() if _state.sp else None
            except Exception as e:
                logger.warning("Spotify poll failed: %s", e)
                time.sleep(POLL_FAST)
                continue

            if not cp or not cp.get("is_playing"):
                _live_ctx.set_playing(False)
                _live_ctx.sync_analyze_buffer(None)

                # ── Batch sequencer: track stopped → advance to next ─────
                with _ba._batch_lock:
                    bp = _ba._batch_processing
                    ctid = _ba._batch_current_track_id
                    bexpected = _ba._batch_expected_track_id
                # Only advance if current track was confirmed playing
                # (bexpected=None means track started and was confirmed).
                # If bexpected is still set, Spotify hasn't started yet — don't skip.
                if bp and ctid is not None and bexpected is None:
                    logger.debug("Batch sequencer: track stopped, advancing (id=%s)",
                                 ctid[:8] if ctid else "?")
                    _batch_advance_to_next(ctid)

                _current_track = None
                _current_track_elapsed = 0
                _current_track_status = "No track playing"
                last_track_id = None
                if last_playing_time > 0 and (time.time() - last_playing_time) > 60:
                    logger.info("Now Playing: auto-stopping after 60s of silence")
                    with _mode_lock:
                        _listen_mode = 0
                        _analyze_mode = 0
                    _listen_stop.set()
                    break
                time.sleep(POLL_FAST)
                continue

            item = cp.get("item") or {}
            tid = item.get("id")
            progress_ms = cp.get("progress_ms", 0)
            dur_ms = item.get("duration_ms", 0)

            if not tid or not dur_ms:
                time.sleep(POLL_FAST)
                continue

            _live_ctx.set_playing(True)

            last_playing_time = time.time()
            previous_track_info = _current_track

            new_track_info = {
                "id": tid,
                "name": item.get("name", "?"),
                "artist": ", ".join(a.get("name", "") for a in (item.get("artists") or [])),
                "album": (item.get("album") or {}).get("name", ""),
                "duration_ms": dur_ms,
            }
            _current_track_elapsed = progress_ms
            _current_track = new_track_info

            context = cp.get("context") or {}
            context_uri = context.get("uri", "")
            pl_id_from_context = ""
            if context_uri.startswith("spotify:playlist:"):
                pl_id_from_context = context_uri.split(":")[-1]

            will_notify = pl_id_from_context != _last_highlighted_pl or tid != _last_highlighted_id
            if will_notify:
                _last_highlighted_pl = pl_id_from_context
                _last_highlighted_id = tid
                try:
                    _ph._notify_playing_track(pl_id_from_context, tid)
                    # Queue-row now-playing highlight (simple analyze mode only —
                    # batch has its own per-track highlight via _needs_queue_highlight).
                    if _analyze_mode == 1 and not _ba._batch_processing and tid:
                        with _ui_context_lock, _ph._page_client:
                            _ph._sync_row_highlight(
                                _aq._queue_table_ref,
                                _aq._queue_rows_cache,
                                _state.analysis_queue,
                                tid,
                                _aq._queue_now_playing_row_keys,
                                "queue",
                            )
                except Exception:
                    logger.exception("_notify_playing_track() crashed in polling thread")

            if tid != last_track_id:
                dur_s = dur_ms / 1000.0 if dur_ms else 0
                name = item.get("name", "?")
                artist = ", ".join(a.get("name", "") for a in (item.get("artists") or []))
                logger.info("Track started: %s - %s (id=%s, duration=%.0fs)", artist, name, tid[:8] if tid else "?", dur_s)

                # ── Batch interference detection ─────────────────────────────
                with _ba._batch_lock:
                    bp = _ba._batch_processing
                    expected = _ba._batch_expected_track_id
                    batch_btn_ref = _ba._batch_btn
                if bp:
                    if expected is not None and tid == expected:
                        with _ba._batch_lock:
                            _ba._batch_expected_track_id = None
                        logger.info("Batch: expected track %s confirmed playing", tid[:8] if tid else "?")
                    elif expected is not None and tid != expected:
                        with _ba._batch_lock:
                            _ba._batch_processing = False
                            _ba._batch_expected_track_id = None
                            _ba._batch_current_track_id = None
                        logger.warning("Batch stopped: user changed track manually (expected=%s, got=%s)",
                                       expected[:8] if expected else "?", tid[:8] if tid else "?")
                        with _ui_pending_lock:
                            _ui_pending_queue.append({"type": "batch_stopped_interference"})
                    elif expected is None:
                        with _ba._batch_lock:
                            _ba._batch_processing = False
                            _ba._batch_expected_track_id = None
                            _ba._batch_current_track_id = None
                        logger.warning("Batch stopped: external track change detected during active batch (track=%s)",
                                       tid[:8] if tid else "?")
                        with _ui_pending_lock:
                            _ui_pending_queue.append({"type": "batch_stopped_external"})

                _live_ctx.sync_analyze_buffer(new_track_info)
                cb = _on_track_changed_cb
                if cb:
                    try:
                        cb(previous_track_info, new_track_info)
                    except Exception:
                        logger.exception("_on_track_changed_cb crashed")
                last_track_id = tid
                last_progress_ms = 0

            if tid == last_track_id and last_progress_ms > 0 and progress_ms < (last_progress_ms - 3000):
                with _mode_lock:
                    buf = _live_ctx.analyze_buf
                    already_submitted = (
                        buf is not None
                        and buf.track_id == tid
                        and buf.submitted
                    )
                if already_submitted:
                    logger.debug(
                        "Seek-back/loop detected for already-submitted track %s — ignoring, no re-collection",
                        tid[:8] if tid else "?",
                    )
                else:
                    logger.info(
                        "Seek-back/loop detected for track %s (not yet submitted) — draining partial buffer and restarting",
                        tid[:8] if tid else "?",
                    )
                    _live_ctx.sync_analyze_buffer(None)
                    _live_ctx.sync_analyze_buffer(new_track_info)
                last_progress_ms = progress_ms

            last_progress_ms = progress_ms

            dm, ds = divmod(dur_ms // 1000, 60)
            em, es = divmod(progress_ms // 1000, 60)
            pct = min(progress_ms / max(dur_ms, 1) * 100, 100)
            _current_track_status = f"{em}:{es:02d} / {dm}:{ds:02d}  ({pct:.0f}%)"

            if progress_ms < dur_ms * 0.10 or progress_ms > dur_ms * 0.90:
                interval = POLL_FAST
            else:
                interval = POLL_NORMAL

            slept = 0.0
            while slept < interval and not _listen_stop.is_set():
                time.sleep(0.5)
                slept += 0.5
                if _listen_mode == 0:
                    break
        except Exception:
            logger.exception("polling thread body crashed — sleeping POLL_FAST and retrying")
            time.sleep(POLL_FAST)

    logger.debug("Listen thread cleanup starting")
    _reset_now_playing_state()
    _processing_stop = False
    logger.debug("Background thread cleanup complete, _processing_stop cleared")


def _reset_now_playing_state():
    """Reset global Now Playing track state so UI shows idle after stop."""
    global _current_track, _current_track_elapsed, _current_track_status
    _current_track = None
    _current_track_elapsed = 0
    _current_track_status = ""
    logger.debug("Now Playing state cleared after stop")


# ─── Queue drain helper (called from _update_np_ui on the main asyncio loop) ──
def _drain_pending_ui_item(item: dict) -> bool:
    """Unpack and execute a pending UI queue item.
    Called from _update_np_ui() on the main asyncio timer — no background thread.
    Returns True if a queue-table rebuild is needed (batched by the caller).
    """
    typ = item.get("type", "")
    try:
        if typ == "notify":
            ui.notify(item["msg"], type=item.get("color", "info"))
        elif typ == "batch_stopped_external":
            # Atomic: toggle button AND show notification in one drain pass
            if _ba._batch_btn is not None:
                _ba._batch_btn.set_text("Start Batch Analysis")
                _ba._batch_btn.props('color=green')
            ui.notify("Batch analysis stopped — playback was changed externally", type="warning")
        elif typ == "batch_stopped_interference":
            if _ba._batch_btn is not None:
                _ba._batch_btn.set_text("Start Batch Analysis")
                _ba._batch_btn.props('color=green')
            ui.notify("Batch stopped — track changed manually", type="warning")
        elif typ == "batch_complete":
            # Run full cleanup: stop listen+analyze, pause playback, reset button.
            # Called safely on the main thread via _update_np_ui() drain —
            # NEVER directly from a background thread.
            # _stop_batch_analysis() already calls _rebuild_queue_ui() internally,
            # so we return False to avoid a redundant second rebuild from the caller.
            _stop_batch_analysis()
            ui.notify("Batch analysis complete", type="positive")
            return False
        elif typ == "watchdog_skip":
            ui.notify("Batch: track skipped (insufficient coverage)", type="warning")
        elif typ == "rebuild_queue":
            return True  # rebuild needed
        elif typ == "batch_advance_ui_sync":
            # These two must never be separated — a flag left True without its
            # accompanying True return could get silently absorbed by
            # _needs_queue_highlight surviving into a drain pass that has no
            # rebuild, causing a stale highlight write against not-yet-rebuilt rows.
            global _needs_queue_highlight
            _needs_queue_highlight = True
            return True  # rebuild + highlight needed (highlight after rebuild)
        else:
            logger.warning("Unknown UI queue item type: %s", typ)
    except Exception:
        logger.exception("_drain_pending_ui_item() failed for item type=%s", typ)
    return False


# ─── UI updater tick (+ batch watchdog) ──────────────────────────────────────
def _update_np_ui():
    global _np_card_container, _api_counter_label

    # ── Drain pending UI updates from background threads ──
    # Pop items under lock, process OUTSIDE lock — avoids AB-BA deadlock
    # with _batch_lock: _batch_advance_to_next() on bg thread does
    # batch_lock → ui_pending_lock, while _drain_pending_ui_item can
    # callback into batch_lock (via _stop_batch_analysis). Releasing
    # ui_pending_lock before processing breaks the cycle.
    to_drain = []
    with _ui_pending_lock:
        while _ui_pending_queue:
            to_drain.append(_ui_pending_queue.popleft())
    needs_rebuild = False
    for item in to_drain:
        if _drain_pending_ui_item(item):
            needs_rebuild = True

    global _needs_queue_highlight
    if needs_rebuild:
        _rebuild_queue_ui()
        if _needs_queue_highlight:
            # Apply selection highlight on the fresh rows (batch advance only)
            # Find the current batch track's row by ID — don't assume row [0]
            # queue globals now in _aq module
            if _aq._queue_table_ref is not None and _aq._queue_rows_cache:
                ctid = _ba._batch_current_track_id
                if ctid:
                    # _queue_rows_cache maps to queue positions, not track IDs directly.
                    # Build a local index: find which queue entry has this track ID.
                    target_row = None
                    for i, t in enumerate(_state.analysis_queue):
                        if t.get("id") == ctid and i < len(_aq._queue_rows_cache):
                            target_row = _aq._queue_rows_cache[i]
                            break
                if target_row is None and _aq._queue_rows_cache:
                    target_row = _aq._queue_rows_cache[0]  # fallback: first row
                if target_row is not None:
                    _aq._queue_table_ref.selected = [target_row]
        _needs_queue_highlight = False

    try:
        if _np_card_container is None:
            return
        _np_card_container.clear()
        with _np_card_container:
            _render_np_inner()
    except Exception:
        logger.exception("_update_np_ui() failed — timer callback crashed")

    # ── Refresh desc generator status row ─────────────────────────────────
    try:
        global _desc_status_container
        if _desc_status_container is not None:
            _desc_status_container.clear()
            with _desc_status_container:
                _render_desc_status()
    except Exception:
        logger.exception("_desc_status_container refresh failed")

    try:
        if _api_counter_label is not None and _state.sp is not None:
            count = getattr(_state, 'api_calls', 0)
            _api_counter_label.set_text(f"🔄 {count} calls")
    except Exception:
        logger.exception("API counter update failed")

    try:
        if _current_track is not None:
            tid = _current_track.get("id", "")
            plid = _state.current_playlist_id
            _ph._sync_now_playing_row_highlight(plid, tid)
            # Also highlight the queue row when analyzing (not batch — batch
            # has its own per-track highlight via _needs_queue_highlight above).
            if _analyze_mode == 1 and not _ba._batch_processing and tid:
                _ph._sync_row_highlight(
                    _aq._queue_table_ref,
                    _aq._queue_rows_cache,
                    _state.analysis_queue,
                    tid,
                    _aq._queue_now_playing_row_keys,
                    "queue",
                )
    except Exception:
        logger.exception("Selection re-sync check failed")

    # ── Batch analysis watchdog ────────────────────────────────────────────
    # The watchdog does NOT mutate state.analysis_queue directly. Instead it
    # just forces the sequencer to advance past a stuck track. The normal
    # Listen/Analyze buffer-flush path handles coverage checks and queue
    # removal independently — per the worker/sequencer separation design.
    try:
        with _ba._batch_lock:
            bp = _ba._batch_processing
            ctid = _ba._batch_current_track_id
            ctd = _ba._batch_current_track_duration_ms
            ts = _ba._batch_track_start_time
            wf = _ba._batch_watchdog_fired_by_track_id
        if bp and ctid:
            track_dur_s = ctd / 1000.0 if ctd else 0
            if track_dur_s > 0:
                elapsed = time.time() - ts
                if elapsed > track_dur_s + 50:
                    if wf != ctid:
                        with _ba._batch_lock:
                            _ba._batch_watchdog_fired_by_track_id = ctid
                        logger.warning(
                            "Batch watchdog: track %s exceeded expected duration (%.0fs + 50s grace), forcing advance",
                            ctid[:8] if ctid else "?", track_dur_s)
                        with _ui_pending_lock:
                            _ui_pending_queue.append({"type": "watchdog_skip"})
                        _batch_advance_to_next(ctid)
    except Exception:
        logger.exception("Batch watchdog check failed")


# ─── Now Playing card UI ──────────────────────────────────────────────────────
def _build_now_playing_card():
    global _np_card_container, _btn_listen, _btn_analyze
    global _track_name_label, _track_progress_label, _track_status_label
    global _desc_status_container

    with ui.card().classes("w-full") as card:
        with ui.column().classes("w-full gap-2") as _np_card_container:
            _render_np_inner()
        ui.timer(0.5, _update_np_ui)

    # ── Description generator status row (visually grouped with Now Playing) ──
    with ui.row().classes("w-full gap-2 items-center mt-2") as _desc_status_container:
        _render_desc_status()

    _viz.init_canvas_js(_viz._viz_canvas_id)

    ui.timer(0.3, _viz._update_viz)


def _render_np_inner():
    global _btn_listen, _btn_analyze
    global _track_name_label, _track_progress_label, _track_status_label

    with ui.row().classes("w-full gap-2 items-center"):
        def on_listen_click():
            global _processing_stop
            # Do NOT hold _mode_lock across _start_listening()/_stop_listening() —
            # those functions internally acquire _mode_lock themselves (non-reentrant
            # threading.Lock), so holding it here would self-deadlock.
            if _listen_mode == 0:
                _start_listening()
            else:
                _stop_listening()

        can_listen = (_state.sp is not None and _state.spotify_device_id is not None
                      and _state.audio_capture_device_index is not None)

        if not _processing_stop:
            btn_text = "Stop Listening" if _listen_mode == 1 else "Start Listening"
            btn_color = "red" if _listen_mode == 1 else "green"
            btn_enabled = can_listen
        else:
            btn_text = "Stopping..."
            btn_color = "orange"
            btn_enabled = False
        _btn_listen = ui.button(btn_text, on_click=on_listen_click, color=btn_color).classes("text-sm")
        _btn_listen.set_enabled(btn_enabled)

        def on_analyze_click():
            # Do NOT hold _mode_lock across _start_analyzing()/_stop_analyzing() —
            # those functions internally acquire _mode_lock themselves (non-reentrant
            # threading.Lock), so holding it here would self-deadlock.
            if _analyze_mode == 0:
                _start_analyzing()
            else:
                _stop_analyzing()

        analyze_text = "Analyzing... Click to stop" if _analyze_mode == 1 else "Analyze"
        analyze_color = "orange" if _analyze_mode == 1 else "green"
        _btn_analyze = ui.button(analyze_text, on_click=on_analyze_click, color=analyze_color).classes("text-sm")
        _btn_analyze.set_enabled(_listen_mode == 1 and not _processing_stop)

        mode_text = []
        if _listen_mode == 1:
            mode_text.append("🎧 Listening")
        if _analyze_mode == 1:
            mode_text.append("🔍 Analyzing")
        mode_str = " | ".join(mode_text) if mode_text else "Idle"
        ui.label(mode_str).classes("text-xs text-gray-500 ml-2")

    if _listen_mode == 0:
        ui.label("Click \"Listen\" to start monitoring Spotify").classes("text-sm text-gray-400")
        return

    if _current_track is None:
        _track_name_label = ui.label("Waiting for playback...").classes("text-base text-gray-500")
        _track_progress_label = ui.label("")
        _track_status_label = ui.label("")
        return

    t = _current_track
    dur_ms = t.get("duration_ms", 0)
    dur_str = f"{dur_ms // 60000}:{(dur_ms // 1000) % 60:02d}" if dur_ms else "?"

    _track_name_label = ui.label(f"🎵 {t['name'][:60]} — {t['artist'][:40]}").classes("text-base font-semibold")
    ui.label(f"Album: {t.get('album', '?')[:50]}").classes("text-xs text-gray-500")
    ui.label(f"Duration: {dur_str}").classes("text-xs text-gray-500")
    _track_status_label = ui.label(_current_track_status).classes("text-sm text-green-600 dark:text-green-400 mt-1")

    elapsed = _current_track_elapsed
    pct = min(elapsed / max(dur_ms, 1) * 100, 100) if dur_ms else 0
    em, es = divmod(elapsed // 1000, 60)
    rm = max(0, dur_ms - elapsed) // 1000
    rem, res = divmod(rm, 60)
    _track_progress_label = ui.label(f"▶ {em}:{es:02d}  [{pct:.0f}%]  -{rem}:{res:02d}").classes("text-xs text-gray-400")
    ui.linear_progress(value=pct / 100).classes("w-full")


# ─── Description generator status row renderer ───────────────────────────────

def _render_desc_status():
    """Delegate to ``playlist_arranger.ui.desc_status.render_desc_status()``."""
    _ds.render_desc_status()


# ─── Play track from playlist table ───────────────────────────────────────────
async def _play_track(track, client=None):
    from playlist_arranger.sources.spotify_source import play_track_on_device
    ctx = client if client else contextlib.nullcontext()
    if not _state.sp or not _state.spotify_device_id:
        with ctx:
            ui.notify("Connect Spotify and select a device first", type="warning")
        return
    try:
        uri = f"spotify:track:{track['id']}"
        play_track_on_device(_state.sp, uri, _state.spotify_device_id)
        with ctx:
            ui.notify(f"Playing: {track['name']}", type="positive")
    except Exception as e:
        with ctx:
            ui.notify(f"Play failed: {e}", type="negative")


def _get_track_status(track: dict) -> str:
    """Delegates to state.get_track_status() (unified 7-priority check)."""
    return _state.get_track_status(track)


def _load_cached_playlist_tracks(playlist_id: str) -> list:
    """Delegate to ``playlist_arranger.sources.playlist_cache.load_cached_playlist_tracks()``."""
    return _pc.load_cached_playlist_tracks(playlist_id)


def _inject_desc_tooltip_css():
    """Delegate to ``playlist_arranger.ui.track_table.inject_desc_tooltip_css()``."""
    _tt.inject_desc_tooltip_css()


def build_spotify_section(set_page_cb):
    # _page_client now accessed via _ph._page_client
    _ph._page_client = ui.context.client
    _inject_desc_tooltip_css()
    ui.label("Spotify Source").classes("text-2xl font-bold mb-2")

    with ui.row().classes("w-full gap-4 items-start"):
        with ui.column().classes("w-[35%]"):
            if _state.sp is not None and _state.spotify_user_id:
                ui.label(f"✓ Connected as {_state.spotify_user_id}").classes("text-sm text-green-600")
            else:
                ui.label("Not connected").classes("text-sm text-gray-500")

            async def do_connect():
                from playlist_arranger.sources.spotify_source import init_spotify as _init_spotify
                try:
                    result = await asyncio.to_thread(_init_spotify, None)
                    _state.sp, _state.spotify_user_id = result
                    logger.info("Spotify connected as: %s", _state.spotify_user_id)
                    logger.debug(
                        "DIAG [do_connect] set_page_cb id=%s, _state.sp=%s",
                        id(set_page_cb), id(_state.sp),
                    )
                    # Rebuild the current page in-place so the playlists list
                    # and "Connected as X" label appear immediately without
                    # requiring a page navigation away and back.
                    #
                    # IMPORTANT: the rebuild is deferred via ui.timer(0.0, ..., once=True)
                    # rather than called inline.  Calling render_right_panel()
                    # synchronously from within do_connect() (which is itself
                    # an on_click handler) causes a race:
                    #
                    # 1. _right_panel.clear() marks old children for removal
                    #    on the Python side, but NiceGUI defers the actual
                    #    browser-DOM removal to the next event-loop tick.
                    # 2. build_spotify_section() then creates a NEW button
                    #    inside the same container BEFORE the old button's
                    #    DOM element has been removed.
                    # 3. Result: TWO button elements co-exist in the browser
                    #    DOM (the old one pending removal + the new one),
                    #    each with its own on_click handler.  Clicks fire on
                    #    BOTH, doubling log output on the 2nd connect and
                    #    tripling on the 3rd.
                    #
                    # Deferring lets the current event-handler fully unwind
                    # and the old DOM subtree be garbage-collected before
                    # the rebuild creates a clean new button.
                    def _deferred_rebuild():
                        # Deferred import avoids circular import at module level:
                        #   main.py → playlist_source.py (build_spotify_section)
                        #   playlist_source.py → main.py (render_right_panel)
                        from playlist_arranger.main import render_right_panel
                        render_right_panel()

                    ui.timer(0.0, _deferred_rebuild, once=True)
                except Exception as e:
                    logger.exception("Spotify connect failed")
                    ui.notify(f"Spotify connect failed: {e}", type="negative")

            with ui.row().classes("w-full items-center gap-2"):
                ui.button("Connect to Spotify", on_click=do_connect).classes("text-sm")
                global _api_counter_label
                _api_counter_label = ui.label("🔄 0 API calls").classes("text-xs text-gray-400")

            def _refresh_spotify_devices():
                if not _state.sp:
                    ui.notify("Connect Spotify first", type="warning")
                    return
                _do_refresh()

            def _do_refresh(retry_delay: float = 0.0):
                """Core device refresh logic with optional retry on 401."""
                import time as _time
                if retry_delay > 0:
                    _time.sleep(retry_delay)
                try:
                    logger.info("Refreshing Spotify devices...")
                    devs = _state.sp.devices().get("devices", [])
                    logger.info("Found %d Spotify devices", len(devs))
                    opts = {d["id"]: d["name"] for d in devs}
                    device_select.set_options(opts)
                    if _state.spotify_device_id is not None:
                        did = str(_state.spotify_device_id)
                        if did in opts:
                            device_select.set_value(did)
                        else:
                            _state.spotify_device_id = None
                    ui.notify(f"Devices refreshed — found {len(devs)}", type="positive")
                except Exception as e:
                    err_msg = str(e)
                    if "401" in err_msg or "unauthorized" in err_msg.lower():
                        if retry_delay == 0:
                            # Token not yet ready at app startup — retry once after 2s
                            logger.warning(
                                "Spotify token not ready for devices() call (401) — retrying in 2s"
                            )
                            _do_refresh(retry_delay=2.0)
                        else:
                            logger.warning(
                                "Spotify devices() still failing after retry (401) — skipping"
                            )
                            ui.notify("Device list unavailable — try refreshing manually", type="warning")
                    else:
                        logger.exception("Failed to list Spotify devices")
                        ui.notify(f"Failed to refresh devices: {e}", type="negative")

            with ui.row().classes("w-full gap-1 items-center"):
                device_select = ui.select(label="Spotify Device", options={}, with_input=True).classes("flex-grow")

                def on_device_change(e):
                    _state.spotify_device_id = device_select.value

                device_select.on("update:model-value", on_device_change)
                ui.button(icon="refresh", on_click=_refresh_spotify_devices).props("flat round dense size=sm").tooltip("Refresh devices")

                # Auto-populate device list if already connected (normal page
                # load when sp was set from a previous session, or the
                # deferred rebuild after do_connect() sets _state.sp).
                if _state.sp is not None:
                    _do_refresh()

        with ui.column().classes("w-[60%]"):
            from playlist_arranger.audio.capture import list_loopback_devices, start_audio_capture, stop_capture

            devices = list_loopback_devices()
            audio_device_options = {}
            for d in devices:
                label = f"[{'LOOP' if d['loopback'] else 'IN'}] {d['name'][:50]} ({d['channels']}ch @ {d['sr']}Hz)"
                audio_device_options[str(d["index"])] = label

            s = _state.get_settings()
            default_audio_idx = None
            persisted_device_name = app.storage.general.get("audio_capture_device_name")
            if persisted_device_name:
                for d in devices:
                    if d["name"] == persisted_device_name:
                        default_audio_idx = str(d["index"])
                        break
            if default_audio_idx is None:
                saved_idx = str(s.selected_audio_device_index) if s.selected_audio_device_index is not None else None
                if saved_idx is not None and saved_idx in audio_device_options:
                    default_audio_idx = saved_idx
                else:
                    default_audio_idx = None
                    if saved_idx is not None:
                        logger.warning(
                            "Saved audio device index %s not found in enumerated devices — "
                            "device list may have changed (plug/unplug). Please reselect.",
                            saved_idx,
                        )

            with ui.row().classes("w-full items-start gap-2 mb-2"):
                # Defensive: only pass value= if it's a valid option key
                _valid_value = default_audio_idx if default_audio_idx in audio_device_options else None
                audio_device_select = ui.select(label="Audio Capture Device", options=audio_device_options,
                                                value=_valid_value).classes("flex-grow")
                if _valid_value is None and default_audio_idx is not None:
                    ui.label("⚠ Audio device changed — please reselect").classes("text-xs text-orange-500 ml-2")

                with ui.row().classes("flex-shrink-0 gap-2 items-start"):
                    ui.html(f'''
                        <canvas id="{_viz._viz_canvas_id}" width="120" height="60"
                                style="width:120px;height:60px;display:block;border-radius:4px;background:#FFFFFF;border:1px solid #E0E0E0;"></canvas>
                    ''')

                    with ui.column().classes("gap-0"):
                        _viz._viz_sr_label = ui.label("SR: — Hz").classes("text-xs font-mono text-gray-500 w-24")
                        _viz._viz_rms_label = ui.label("RMS: — dB").classes("text-xs font-mono text-gray-500 w-24")
                        _viz._viz_peak_label = ui.label("Pk: — dB").classes("text-xs font-mono text-gray-500 w-24")

            def on_audio_device_change(e):
                raw_val = audio_device_select.value
                if raw_val is not None:
                    idx = int(raw_val)
                    _state.audio_capture_device_index = idx
                    selected_dev = next((d for d in devices if d["index"] == idx), None)
                    if selected_dev:
                        app.storage.general["audio_capture_device_name"] = selected_dev["name"]
                    if _state.stream:
                        stop_capture(_state.pa, _state.stream)
                        _state.pa = None
                        _state.stream = None
                    _state.pa, _state.stream, dev_name = start_audio_capture(idx)
                    logger.info("Audio capture started on: %s", dev_name)

            audio_device_select.on("update:model-value", on_audio_device_change)

            if default_audio_idx and not _state.stream:
                _state.audio_capture_device_index = int(default_audio_idx)
                _state.pa, _state.stream, dev_name = start_audio_capture(int(default_audio_idx))

            _build_now_playing_card()

    if _state.sp is not None and _state.spotify_user_id:
        _render_playlists(set_page_cb)


_render_playlists_set_page_cb = None

# ─── Description generator status row ─────────────────────────────────────────
_desc_status_container = None

# ─── Queue for Analysis section ──────────────────────────────────────────────
_queue_table_ref = None
_queue_rows_cache = []
_queue_now_playing_row_keys = {}  # track→idx for now-playing highlight (analyze mode, not batch)
_queue_container = None
_queue_expansion_ref = None
_queue_label_ref = None

def _persist_queue():
    _aq.persist_queue()


def _rebuild_queue_ui():
    global _queue_table_ref, _queue_rows_cache, _queue_container
    _aq.rebuild_queue_ui()
    _queue_table_ref = _aq._queue_table_ref
    _queue_rows_cache = _aq._queue_rows_cache
    _refresh_batch_btn_enabled()


def _add_selected_to_queue(pl_id: str, tracks: list):
    _aq.add_selected_to_queue(pl_id, tracks)

def _update_queue_label():
    _aq.update_queue_label()


def _render_queue_table():
    global _queue_table_ref, _queue_rows_cache
    _aq.render_queue_table()
    _queue_table_ref = _aq._queue_table_ref
    _queue_rows_cache = _aq._queue_rows_cache


# ─── Batch analysis helpers ──────────────────────────────────────────────────
def _is_actively_playing_on_device(device_id: str | None) -> bool:
    """Check Spotify API for active playback on a specific device.

    Returns True only if: response is not None, is_playing is True,
    AND device.id matches the given device_id.
    """
    if not _state.sp or not device_id:
        return False
    try:
        cp = _state.sp.current_playback()
    except Exception:
        logger.debug("_is_actively_playing_on_device: current_playback() call failed — assuming not playing")
        return False
    if cp is None:
        return False
    if not cp.get("is_playing"):
        return False
    cp_device = cp.get("device") or {}
    return cp_device.get("id") == device_id


def _safe_pause_active_playback():
    """Pause playback only if Spotify reports active playback on the selected device.

    Reads current_playback() first (GET /me/player) to check is_playing + device match.
    Skips the pause_playback() PUT entirely if nothing is actively playing, avoiding
    doomed-to-fail 403 responses.
    """
    if not _state.sp or not _state.spotify_device_id:
        return
    if _is_actively_playing_on_device(_state.spotify_device_id):
        try:
            _state.sp.pause_playback(device_id=_state.spotify_device_id)
            logger.debug("Paused active playback on device %s", _state.spotify_device_id[:8] if _state.spotify_device_id else "?")
        except Exception as e:
            err_msg = str(e)
            if "403" in err_msg:
                logger.debug("Pause playback failed with 403 (race condition, playback likely stopped between GET and PUT)")
            else:
                logger.info("Could not pause playback: %s", err_msg)
    else:
        logger.debug("No active playback on device %s — skipping pause",
                     _state.spotify_device_id[:8] if _state.spotify_device_id else "?")
# ---- Wire batch_analyzer's dependency injection (Step 2) ----


_ba.configure(
    stop_analyzing_fn=_stop_analyzing,
    stop_listening_fn=_stop_listening,
    safe_pause_playback_fn=_safe_pause_active_playback,
    rebuild_queue_ui_fn=_rebuild_queue_ui,
    ui_pending_lock=_ui_pending_lock,
    ui_pending_queue=_ui_pending_queue,
)



def _stop_batch_analysis():
    """Delegate to batch_analyzer._stop_batch_analysis() (moved there)."""
    _ba._stop_batch_analysis()


def _batch_advance_to_next(expected_track_id: str | None = None):
    """Delegate to batch_analyzer._batch_advance_to_next() (moved there)."""
    _ba._batch_advance_to_next(expected_track_id)


def _on_start_batch():
    """Start batch analysis: start listen+analyze, play first track from live queue."""

    if not _state.analysis_queue:
        ui.notify("Queue is empty — add tracks first", type="warning")
        return

    if not _state.sp or not _state.spotify_device_id:
        ui.notify("Connect Spotify and select a device first", type="warning")
        return

    with _ba._batch_lock:
        _ba._batch_processing = True
        if _ba._batch_btn is not None:
            _ba._batch_btn.set_text("Stop Batch Analysis")
            _ba._batch_btn.props('color=red')

    _safe_pause_active_playback()

    _start_listening()
    _start_analyzing()
    _batch_advance_to_next()
    logger.info("Batch analysis started — %d tracks in queue", len(_state.analysis_queue))


def _refresh_batch_btn_enabled():
    """Re-evaluate the batch button enabled state based on current conditions.

    Called:
      - explicitly at the end of _rebuild_queue_ui() — single source of truth
        after every queue modification (add, remove, clear, analysis-complete)
      - by _render_queue_controls() right after button creation
      - periodically (1 s timer) to react to device-selection changes without
        a full UI rebuild
    """
    if _ba._batch_btn is None:
        return
    try:
        can_batch = len(_state.analysis_queue) > 0 and _state.spotify_device_id is not None
        _ba._batch_btn.set_enabled(can_batch or _ba._batch_processing)
    except Exception:
        logger.exception("_refresh_batch_btn_enabled() failed — batch_btn may be stale")


def _render_queue_controls():
    with ui.row().classes("w-full gap-2 mb-2"):
        _ba._batch_btn = ui.button(
            "Start Batch Analysis" if not _ba._batch_processing else "Stop Batch Analysis",
            on_click=lambda: _stop_batch_analysis() if _ba._batch_processing else _on_start_batch(),
            color="red" if _ba._batch_processing else "green",
        ).classes("text-sm")
        _refresh_batch_btn_enabled()
        # Periodically refresh the button enabled state so it reacts to
        # device selection / queue restore without requiring a full UI rebuild.
        ui.timer(1.0, _refresh_batch_btn_enabled)

        def _on_remove_selected():
            # _queue_table_ref now accessed via _aq module
            if _aq._queue_table_ref is None:
                return
            selected = list(_aq._queue_table_ref.selected) if hasattr(_aq._queue_table_ref, 'selected') else []
            if not selected:
                return
            to_remove = sorted((r["idx"] for r in selected), reverse=True)
            for idx in to_remove:
                i = idx - 1
                if 0 <= i < len(_state.analysis_queue):
                    with _state.analysis_queue_lock:
                        del _state.analysis_queue[i]
            _persist_queue()
            _update_queue_label()
            ui.notify(f"Removed {len(to_remove)} track(s) from queue", type="positive")
            _rebuild_queue_ui()

        def _refresh_remove_btn():
            nonlocal remove_btn
            if _aq._queue_table_ref is not None:
                selected = list(_aq._queue_table_ref.selected) if hasattr(_aq._queue_table_ref, 'selected') else []
                remove_btn.set_enabled(len(selected) > 0)

        remove_btn = ui.button("Remove Selected Tracks from Queue", on_click=_on_remove_selected, color="orange").classes("text-sm")
        remove_btn.set_enabled(False)
        ui.timer(0.5, _refresh_remove_btn)

        def _on_remove_all():
            with _state.analysis_queue_lock:
                _state.analysis_queue.clear()
            _persist_queue()
            _update_queue_label()
            ui.notify("Queue cleared", type="positive")
            _rebuild_queue_ui()

        ui.button("Remove All Tracks from Queue", on_click=_on_remove_all, color="red").classes("text-sm").set_enabled(len(_state.analysis_queue) > 0)

    # ── Description generation buttons (queue table) ─────────────────────────
    with ui.row().classes("w-full gap-2 mt-1"):
        def _desc_gen_queue_selected():
            if _aq._queue_table_ref is None:
                return
            selected = list(_aq._queue_table_ref.selected) if hasattr(_aq._queue_table_ref, 'selected') else []
            if not selected:
                ui.notify("No tracks selected", type="warning")
                return
            track_ids = [r["track_id"] for r in selected if r.get("track_id")]
            n = _desc_gen.desc_queue_add_many(track_ids)
            if n > 0:
                ui.notify(f"Added {n} tracks to description queue", type="positive")
            else:
                ui.notify("All selected tracks are already in the description queue", type="info")

        desc_q_sel_btn = ui.button(
            "Generate new descriptions for selected tracks",
            on_click=_desc_gen_queue_selected,
            color="blue",
        ).classes("text-sm")
        desc_q_sel_btn.set_enabled(False)

        def _refresh_desc_q_sel_btn():
            if _aq._queue_table_ref is not None:
                selected = list(_aq._queue_table_ref.selected) if hasattr(_aq._queue_table_ref, 'selected') else []
                desc_q_sel_btn.set_enabled(len(selected) > 0)
        ui.timer(0.5, _refresh_desc_q_sel_btn)

        def _desc_gen_queue_all():
            track_ids = [
                t.get("id", "")
                for t in _state.analysis_queue
                if isinstance(t, dict) and t.get("id")
            ]
            n = _desc_gen.desc_queue_add_many(track_ids)
            if n > 0:
                ui.notify(f"Added {n} tracks to description queue", type="positive")
            else:
                ui.notify("All tracks are already in the description queue", type="info")

        desc_q_all_btn = ui.button(
            "Generate new descriptions for all tracks",
            on_click=_desc_gen_queue_all,
            color="blue",
        ).classes("text-sm")
        desc_q_all_btn.set_enabled(len(_state.analysis_queue) > 0)


def _render_analysis_queue():
    global _queue_expansion_ref, _queue_label_ref, _queue_container
    _aq.render_analysis_queue()
    _queue_expansion_ref = _aq._queue_expansion_ref
    _queue_label_ref = _aq._queue_label_ref
    _queue_container = _aq._queue_container


def _render_playlists(set_page_cb):
    global _render_playlists_set_page_cb
    _render_playlists_set_page_cb = set_page_cb
    ui.separator()

    _render_analysis_queue()

    ui.label("Your Playlists").classes("text-lg font-bold mb-2")

    from playlist_arranger.sources.spotify_source import get_own_playlists
    pls = get_own_playlists(_state.sp, _state.spotify_user_id)

    for pl in pls:
        pl_id = pl["id"]
        pl_name = pl["name"]
        total = pl.get("tracks", {}).get("total")
        label = f"{pl_name}" + (f" ({total} tracks)" if total is not None else "")

        with ui.expansion(label, value=pl_id == _state.current_playlist_id).classes("w-full") as exp:
            exp.props(f'header-class="text-lg font-semibold" data-pl-id="{pl_id}"')
            content_col = ui.column().classes("w-full")
            _ph._playlist_expansions[pl_id] = (exp, content_col)

            async def on_expand(e, pid=pl_id, pn=pl_name, col=content_col):
                if not e.args:
                    return
                col.clear()
                with col:
                    ui.spinner(size="sm")
                try:
                    tracks = await asyncio.to_thread(_load_cached_playlist_tracks, pid)
                    _state.current_playlist_id = pid
                    _state.current_playlist_name = pn
                    _state.current_playlist_source = "spotify"
                    _state.current_tracks[:] = tracks
                    col.clear()
                    with col:
                        _show_track_compact_table(tracks, pid, pn, set_page_cb)
                except Exception as exc:
                    col.clear()
                    with col:
                        ui.label(f"Error: {exc}").classes("text-red-500 text-sm")

            exp.on("update:model-value", on_expand)

            if pl_id == _state.current_playlist_id and _state.current_tracks:
                with content_col:
                    _show_track_compact_table(_state.current_tracks, pl_id, pl_name, set_page_cb)


def _get_selected_rows(pl_id: str):
    """Delegate to ``playlist_arranger.ui.track_table.get_selected_rows()``."""
    return _tt.get_selected_rows(pl_id)


def _show_track_compact_table(tracks, pl_id, pl_name, set_page_cb):
    """Delegate to ``playlist_arranger.ui.track_table.show_track_compact_table()``."""
    _tt.show_track_compact_table(tracks, pl_id, pl_name, set_page_cb)


def _run_spotify_analysis(tracks):
    pl_id = _state.current_playlist_id
    pl_name = _state.current_playlist_name

    def bg_task():
        try:
            from playlist_arranger.analysis.session import AnalysisSession
            to_analyze = [t for t in tracks if _get_track_status(t) != _state.STATUS_OK]
            if not to_analyze:
                return
            session = AnalysisSession(sp=_state.sp, tracks=to_analyze, playlist_name=pl_name,
                                      playlist_uri=f"spotify:playlist:{pl_id}",
                                      spotify_device_id=_state.spotify_device_id,
                                      progress_cb=lambda msg: logger.info(msg))
            session.run()
        except Exception:
            logger.exception("Analysis failed")

    threading.Thread(target=bg_task, daemon=True).start()


def _recover_from_backup(pl_id, set_page_cb):
    from playlist_arranger.cache.store import load_backup as _load_backup
    bk_data = _load_backup(pl_id)
    if not bk_data or not bk_data.get("tracks"):
        ui.notify("Backup empty or not found", type="warning")
        return
    descs = []
    for t in bk_data["tracks"]:
        tid = t.get("id") or t.get("track_id", "")
        descs.append({"track_id": tid, "name": t.get("name", "?"), "artist": t.get("artist", "?"),
                      "album": t.get("album", "?"), "description": "", "playlist": _state.current_playlist_name,
                      "bpm": 0, "key": "", "camelot": "", "loudness_db": 0, "dynamic_range": 0,
                      "harm_ratio": 0, "flatness": 0, "bass_pct": 0, "mid_pct": 0, "high_pct": 0,
                      "onset_str": 0, "duration_ms": 0})
    for d in descs:
        entry = _db.get_track(d["track_id"])
        if entry:
            f = entry.get("features") or {}
            d.update({"bpm": round(f.get("bpm", 0), 1), "key": f"{f.get('chroma_key', '')} {f.get('mode', '')}".strip(),
                      "camelot": f.get("camelot", ""), "loudness_db": round(f.get("rms_db", 0), 1),
                      "dynamic_range": round(f.get("dynamic_range", 0), 1), "harm_ratio": round(f.get("harm_ratio", 0), 2),
                      "flatness": round(f.get("flatness", 0), 3), "bass_pct": round(f.get("bass", 0) * 100, 1),
                      "mid_pct": round(f.get("mid", 0) * 100, 1), "high_pct": round(f.get("high", 0) * 100, 1),
                      "onset_str": round(f.get("onset_str", 0), 2), "duration_ms": entry.get("duration_ms", 0)})
    _state.current_descs[:] = descs
    ui.notify(f"Recovered {len(descs)} tracks from backup", type="positive")
    set_page_cb("anchors")


def build_local_section(set_page_cb):
    """Delegate to ``playlist_arranger.ui.local_section.build_local_section()``."""
    _ls.build_local_section(set_page_cb)
# ---- Wire playlist_highlight dependency injection (circular-import avoidance) ----
# Must come after all referenced functions are defined.
_ph.configure(
    load_cached_playlist_tracks=_load_cached_playlist_tracks,
    show_track_compact_table=_tt.show_track_compact_table,
    render_playlists_set_page_cb=_render_playlists_set_page_cb,
    ui_context_lock=_ui_context_lock,
)

# ---- Wire track_table dependency injection ----
_tt.configure(
    state_module=_state,
    db_module=_db,
    ph_module=_ph,
    desc_gen_module=_desc_gen,
    get_desc_age_info_fn=get_desc_age_info,
    get_track_status_fn=_get_track_status,
    play_track_fn=_play_track,
    add_selected_to_queue_fn=_add_selected_to_queue,
    persist_queue_fn=_persist_queue,
    update_queue_label_fn=_update_queue_label,
    rebuild_queue_ui_fn=_rebuild_queue_ui,
    recover_from_backup_fn=_recover_from_backup,
    backup_exists_fn=_backup_exists,
    show_desc_dialog_fn=show_desc_dialog,
)

# ---- Wire analysis_queue dependency injection ----
_aq.configure(
    state_module=_state,
    db_module=_db,
    desc_gen_module=_desc_gen,
    ph_module=_ph,
    get_desc_age_info_fn=get_desc_age_info,
    get_track_status_fn=_get_track_status,
    play_track_fn=_play_track,
    desc_icon_click_fn=_tt.on_desc_icon_click,
    render_queue_controls_fn=_render_queue_controls,
)

# ---- Wire extracted modules (playlist_cache, desc_status, local_section) ----
from playlist_arranger.sources.spotify_source import get_playlist_tracks as _get_playlist_tracks_fn
_pc.configure(state_module=_state, get_playlist_tracks_fn=_get_playlist_tracks_fn)
_ds.configure(desc_gen_module=_desc_gen)
_ls.configure(state_module=_state, show_track_compact_table_fn=_tt.show_track_compact_table)
