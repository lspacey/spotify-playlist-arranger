"""Playlist source selection: Spotify / Local files."""

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

logger = logging.getLogger(__name__)


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

# UI element references (updated by polling thread)
_track_name_label = None
_track_progress_label = None
_track_status_label = None
_btn_listen = None
_btn_analyze = None
_np_card_container = None
_api_counter_label = None
_viz_canvas_id = "pa-viz-canvas"
_viz_sr_label = None
_viz_rms_label = None
_viz_peak_label = None

# Playlist expansion references for auto-expand (keyed by playlist_id)
_playlist_expansions = {}

# Set of playlist IDs that were auto-expanded (not manually by user)
_auto_expanded_playlist_ids = set()

# Captured client context for background thread UI operations
_page_client = None

# Last notified playlist_id for change detection
_last_notified_playlist_id = None

# Per-playlist table refs for native selection highlight from background thread
_playlist_tables = {}       # pl_id → ui.table instance
_playlist_rows_cache = {}   # pl_id → rows list for row lookup
_now_playing_row_keys = {}  # pl_id → row_key value currently highlighted as "now playing" (excluded from user selection getter)

def _auto_expand_playlist(playlist_id: str, track_id: str):
    """Auto-expand a collapsed playlist, loading its tracks and rendering table."""
    entry = _playlist_expansions.get(playlist_id)
    if entry is None:
        return
    exp, content_col = entry
    if not exp.value:
        try:
            tracks = _load_cached_playlist_tracks(playlist_id)
            _state.current_playlist_id = playlist_id
            _state.current_playlist_name = ""
            _state.current_playlist_source = "spotify"
            _state.current_tracks[:] = tracks
            content_col.clear()
            with content_col:
                _show_track_compact_table(tracks, playlist_id, _state.current_playlist_name, _render_playlists_set_page_cb)
            exp.value = True
            _auto_expanded_playlist_ids.add(playlist_id)
        except Exception:
            logger.exception("Auto-expand failed")


def _collapse_playlist(playlist_id: str):
    """Collapse a previously auto-expanded playlist and remove highlight."""
    if playlist_id not in _auto_expanded_playlist_ids:
        return
    entry = _playlist_expansions.get(playlist_id)
    if entry is None:
        return
    exp, _content_col = entry
    exp.value = False
    _auto_expanded_playlist_ids.discard(playlist_id)
    # Clear highlight: clear native table selection + playlist header highlight
    table = _playlist_tables.get(playlist_id)
    if table is not None:
        table.selected = []
    if playlist_id:
        js = (
            "(function(){"
            "var el=document.querySelector('[data-pl-id=\"" + playlist_id + "\"]');"
            "if(el){el.classList.remove('pa-playlist-highlight');el.style.backgroundColor='';}"
            "console.log('[collapse] cleared');"
            "})()"
        )
        ui.run_javascript(js)
    logger.debug("Collapsed playlist %s", playlist_id[:8] if playlist_id else "?")


def _notify_playing_track(playlist_id: str, track_id: str):
    """Highlight and auto-expand from background thread via captured client context."""
    global _page_client, _last_notified_playlist_id
    plid = playlist_id or ""
    tid = track_id or ""

    if _page_client is None:
        logger.warning("No page client captured — skipping highlight/auto-expand")
        return

    playlist_changed = plid and plid != _last_notified_playlist_id
    logger.debug(
        "notify gate: playlist_changed=%s last_notified=%s",
        playlist_changed,
        _last_notified_playlist_id[:8] if _last_notified_playlist_id else _last_notified_playlist_id,
    )

    try:
        with _page_client:
            if playlist_changed:
                if _last_notified_playlist_id:
                    _collapse_playlist(_last_notified_playlist_id)
                _last_notified_playlist_id = plid

            # ── Now-playing row highlight via Quasar native selection ───────
            # Sets table.selected to the matching row, giving it the same
            # visual treatment as user-checked rows. No visual distinction
            # between "checked" and "now playing" — both share .selected.
            has_tracks = bool(_state.current_tracks)
            plid_matches_current = plid == _state.current_playlist_id
            gate_open = plid and tid and has_tracks and plid_matches_current
            logger.debug(
                "notify gate: plid=%s tid=%s has_tracks=%s plid_matches_current=%s gate_open=%s",
                plid[:8] if plid else plid,
                tid[:8] if tid else tid,
                has_tracks,
                plid_matches_current,
                gate_open,
            )
            if gate_open:
                rows_cache = _playlist_rows_cache.get(plid, [])
                table = _playlist_tables.get(plid)
                if table is not None and rows_cache:
                    tracks = _state.current_tracks
                    track_index = next((i for i, t in enumerate(tracks) if t.get("id") == tid), None)
                    if track_index is not None and track_index < len(rows_cache):
                        match_row = rows_cache[track_index]
                        # Additive selection: preserve user-checked rows, add now-playing row
                        # Remove previous now-playing row (if not also user-checked)
                        prev_np_key = _now_playing_row_keys.get(plid)
                        current = list(table.selected) if hasattr(table, 'selected') else []
                        if prev_np_key is not None:
                            current = [r for r in current if r.get("idx") != prev_np_key]
                        # Add new now-playing row (idempotent — if user also checked it, stays)
                        if not any(r.get("idx") == match_row["idx"] for r in current):
                            current.append(match_row)
                        table.selected = current
                        _now_playing_row_keys[plid] = match_row["idx"]
                        logger.debug("Native selection: row %d added (total selected=%d) for plid=%s",
                                     track_index, len(current), plid[:8] if plid else plid)
                    else:
                        logger.warning(
                            "track_index NOT FOUND: tid=%s not in current_tracks (sample_ids: %s)",
                            tid[:8] if tid else tid,
                            [t.get("id", "")[:8] for t in tracks[:5]],
                        )

            # ── Playlist header highlight (yellow background) ────────────────
            if plid:
                js = (
                    "(function(){"
                    "document.querySelectorAll('.pa-playlist-highlight').forEach(el=>{"
                    "el.classList.remove('pa-playlist-highlight');el.style.backgroundColor='';"
                    "});"
                    "var exp=document.querySelector('[data-pl-id=\"" + plid + "\"]');"
                    "if(exp){exp.classList.add('pa-playlist-highlight');exp.style.backgroundColor='#fef3c7';}"
                    "})()"
                )
                ui.run_javascript(js)

            if plid:
                _auto_expand_playlist(plid, tid)
    except Exception:
        logger.exception("_notify_playing_track() failed")


# ─── Card logic: background polling thread (STAGE 1 — pure Spotify monitoring) ──
def _card_listen_thread():
    """Background thread: polls Spotify API for track info + highlight only."""
    global _current_track, _current_track_elapsed, _current_track_status
    global _listen_mode, _analyze_mode, _listen_stop, _processing_stop
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
                # Paused/stopped — gate audio collection off (prevent silence pollution)
                _live_ctx.set_playing(False)
                # Flush analyze buffer when playback stops — sync_analyze_buffer(None) handles
                # flushing the current buffer if any (no-op if already flushed or not analyzing)
                _live_ctx.sync_analyze_buffer(None)

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

            # Playing — gate audio collection on (resume from pause)
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
                logger.debug(
                    "Highlight check: plid=%s tid=%s last_plid=%s last_tid=%s will_notify=%s",
                    pl_id_from_context[:8] if pl_id_from_context else pl_id_from_context,
                    tid[:8] if tid else tid,
                    _last_highlighted_pl[:8] if _last_highlighted_pl else _last_highlighted_pl,
                    _last_highlighted_id[:8] if _last_highlighted_id else _last_highlighted_id,
                    will_notify,
                )
                _last_highlighted_pl = pl_id_from_context
                _last_highlighted_id = tid
                try:
                    _notify_playing_track(pl_id_from_context, tid)
                except Exception:
                    logger.exception("_notify_playing_track() crashed in polling thread")

            if tid != last_track_id:
                dur_s = dur_ms / 1000.0 if dur_ms else 0
                name = item.get("name", "?")
                artist = ", ".join(a.get("name", "") for a in (item.get("artists") or []))
                logger.info("Track started: %s - %s (id=%s, duration=%.0fs)", artist, name, tid[:8] if tid else "?", dur_s)
                # Notify live buffer context of track change
                _live_ctx.sync_analyze_buffer(new_track_info)
                # Also call any additional on_track_changed callback
                cb = _on_track_changed_cb
                if cb:
                    try:
                        cb(previous_track_info, new_track_info)
                    except Exception:
                        logger.exception("_on_track_changed_cb crashed")
                last_track_id = tid
                last_progress_ms = 0

            # ── Seek-back / loop detection ──────────────────────────────────
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
                    _live_ctx.sync_analyze_buffer(None)  # drain current buffer
                    _live_ctx.sync_analyze_buffer(new_track_info)  # start fresh buffer
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


# ─── UI updater tick ──────────────────────────────────────────────────────────
def _update_np_ui():
    global _np_card_container, _api_counter_label
    try:
        if _np_card_container is None:
            return
        _np_card_container.clear()
        with _np_card_container:
            _render_np_inner()
    except Exception:
        logger.exception("_update_np_ui() failed — timer callback crashed")

    # ── API counter update ─────────────────────────────────────────────────
    try:
        if _api_counter_label is not None and _state.sp is not None:
            count = getattr(_state.sp, 'call_count', 0)
            _api_counter_label.set_text(f"🔄 {count} calls")
    except Exception:
        logger.exception("API counter update failed")

    # ── Self-healing: re-sync now-playing row in table selection ──────────
    try:
        if _current_track is not None:
            tid = _current_track.get("id", "")
            plid = _state.current_playlist_id
            if plid and tid:
                table = _playlist_tables.get(plid)
                rows_cache = _playlist_rows_cache.get(plid, [])
                if table is not None and rows_cache:
                    tracks = _state.current_tracks
                    track_index = next((i for i, t in enumerate(tracks) if t.get("id") == tid), None)
                    if track_index is not None and track_index < len(rows_cache):
                        match_row = rows_cache[track_index]
                        # Additive: preserve user-checked rows, ensure now-playing row is present
                        prev_np_key = _now_playing_row_keys.get(plid)
                        current = list(table.selected) if hasattr(table, 'selected') else []
                        if prev_np_key is not None and prev_np_key != match_row["idx"]:
                            current = [r for r in current if r.get("idx") != prev_np_key]
                        if not any(r.get("idx") == match_row["idx"] for r in current):
                            current.append(match_row)
                            table.selected = current
                            _now_playing_row_keys[plid] = match_row["idx"]
                            logger.debug("Selection re-synced after refresh: track=%s row_idx=%d (total=%d)",
                                         tid[:8] if tid else tid, track_index, len(current))
    except Exception:
        logger.exception("Selection re-sync check failed")


# ─── Now Playing card UI ──────────────────────────────────────────────────────
def _build_now_playing_card():
    global _np_card_container, _btn_listen, _btn_analyze
    global _track_name_label, _track_progress_label, _track_status_label

    with ui.card().classes("w-full") as card:
        with ui.column().classes("w-full gap-2") as _np_card_container:
            _render_np_inner()
        ui.timer(0.5, _update_np_ui)

    # ── Canvas init JS (runs once, canvas created in build_spotify_section) ─
    ui.run_javascript(f'''
        (function() {{
            var c = document.getElementById("{_viz_canvas_id}");
            if (!c) return;
            c._paVizBands = [];
            c._paRedraw = function() {{
                var ctx = c.getContext("2d");
                var w = c.width, h = c.height;
                ctx.clearRect(0, 0, w, h);
                var bands = c._paVizBands;
                if (!bands.length) {{
                    ctx.fillStyle = "#BDBDBD";
                    ctx.font = "8px monospace";
                    ctx.textAlign = "center";
                    ctx.fillText("no signal", w/2, h/2+3);
                    return;
                }}
                var bw = (w - 4) / bands.length;
                for (var i = 0; i < bands.length; i++) {{
                    var v = Math.min(1, Math.max(0, bands[i]));
                    var barH = v * (h - 8);
                    var x = 2 + i * bw;
                    ctx.fillStyle = "#9E9E9E";
                    ctx.fillRect(x + 1, h - barH - 2, bw - 2, barH);
                }}
            }};
            c._paRedraw();
        }})();
    ''')

    ui.timer(0.3, _update_viz)


def _update_viz():
    """Sample audio deque, compute FFT + RMS, push bands + stats to canvas JS."""
    global _viz_sr_label, _viz_rms_label, _viz_peak_label
    import numpy as np
    import json as _json

    cap = _cap
    if cap is None or cap.audio_deque is None:
        return

    sr = getattr(cap, 'actual_sr', 44100)

    # ── Sample deque under lock (brief) ──────────────────────────────────────
    try:
        with cap.audio_lock:
            if cap.audio_deque is None or len(cap.audio_deque) < 64:
                return
            window = np.array(list(cap.audio_deque)[-4096:], dtype=np.float32)
    except Exception:
        return

    if len(window) < 64:
        return

    # ── Mono conversion (stereo→mean) ──────────────────────────────────────
    from playlist_arranger.audio.features import _to_mono
    if hasattr(cap, 'actual_channels') and cap.actual_channels > 1:
        mono = _to_mono(window, cap.actual_channels)
    else:
        mono = window if window.ndim == 1 else window.mean(axis=1)

    # ── Silence gate: no real audio → show placeholder, don't compute dB ──
    if float(np.max(np.abs(mono))) < 1e-10:
        if _viz_sr_label is not None:
            _viz_sr_label.set_text(f"SR: {sr} Hz")
        if _viz_rms_label is not None:
            _viz_rms_label.set_text("RMS: — dB")
        if _viz_peak_label is not None:
            _viz_peak_label.set_text("Pk: — dB")
        # Push empty bands → canvas renders "no signal"
        ui.run_javascript(
            f"(function(){{var c=document.getElementById('{_viz_canvas_id}');"
            f"if(c){{c._paVizBands=[];c._paRedraw();}}}})()"
        )
        return

    # ── RMS + peak dB ──────────────────────────────────────────────────────
    rms = float(np.sqrt(np.mean(np.square(mono))) + 1e-12)
    peak = float(np.max(np.abs(mono)) + 1e-12)
    rms_db = 20.0 * np.log10(rms)  # noqa: F841  (used in JS via JSON)
    peak_db = 20.0 * np.log10(peak)  # noqa: F841

    # ── FFT → 8 log-spaced bands ───────────────────────────────────────────
    n_fft = min(512, len(mono))
    fft = np.abs(np.fft.rfft(mono, n=n_fft))
    num_bins = len(fft)
    num_bands = 10
    band_edges = np.logspace(0, np.log10(num_bins), num_bands + 1).astype(int)
    bands = []
    for i in range(num_bands):
        lo, hi = band_edges[i], band_edges[i + 1]
        lo = max(0, min(lo, num_bins - 1))
        hi = max(0, min(hi, num_bins))
        if hi > lo:
            band_val = float(np.mean(fft[lo:hi]))
        else:
            band_val = 0.0
        bands.append(band_val)

    # ── Normalize bands to 0-1 ────────────────────────────────────────────
    max_val = float(np.max(bands) + 1e-12)
    if max_val > 0:
        bands = [min(1.0, b / (max_val * 1.5)) for b in bands]
    else:
        bands = [0.0] * num_bands

    # ── Push bands to canvas JS; stats to NiceGUI labels ──────────────────
    bands_json = _json.dumps(bands)
    js = (
        f"(function(){{"
        f"var c=document.getElementById('{_viz_canvas_id}');"
        f"if(!c)return;"
        f"c._paVizBands={bands_json};"
        f"c._paRedraw();"
        f"}})()"
    )
    ui.run_javascript(js)

    # Update text stat labels (NiceGUI handles rendering, no canvas overlay needed)
    if _viz_sr_label is not None:
        _viz_sr_label.set_text(f"SR: {sr} Hz")
    if _viz_rms_label is not None:
        _viz_rms_label.set_text(f"RMS: {rms_db:.1f} dB")
    if _viz_peak_label is not None:
        _viz_peak_label.set_text(f"Pk: {peak_db:.1f} dB")


def _render_np_inner():
    global _btn_listen, _btn_analyze
    global _track_name_label, _track_progress_label, _track_status_label

    with ui.row().classes("w-full gap-2 items-center"):
        def on_listen_click():
            global _listen_mode, _listen_thread, _listen_stop
            global _processing_stop, _analyze_mode
            logger.info("Button clicked: %s", "Listen (now ON)" if _listen_mode == 0 else "Stop Listening")
            with _mode_lock:
                if _listen_mode == 0:
                    _listen_mode = 1
                    _listen_stop.clear()
                    _listen_thread = threading.Thread(target=_card_listen_thread, daemon=True)
                    _listen_thread.start()
                    logger.info("Now Playing: listening started")
                else:
                    _processing_stop = True
                    _listen_mode = 0
                    if _analyze_mode:
                        _analyze_mode = 0
                        _live_ctx.stop_poll_thread()
                        # Flush buffer on manual stop (if not already submitted)
                        _live_ctx.flush_before_stop()
                        logger.info("Analyze mode stopped: Listen stopped")
                    if _btn_listen is not None:
                        _btn_listen.props('color=orange')
                        _btn_listen.set_text("Stopping...")
                        _btn_listen.set_enabled(False)
                    _listen_stop.set()

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

        # Analyze button — single label for simplicity (buffering+save overlap in practice)
        def on_analyze_click():
            global _analyze_mode
            should_sync = False
            with _mode_lock:
                if _listen_mode != 1:
                    return
                if _analyze_mode == 0:
                    _analyze_mode = 1
                    _live_ctx.start_poll_thread()
                    logger.info("Now Playing: analysis started")
                    should_sync = True
                else:
                    _analyze_mode = 0
                    _live_ctx.stop_poll_thread()
                    logger.info("Now Playing: analysis stopped")
            if should_sync:
                _live_ctx.sync_analyze_buffer(_current_track)

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


def _process_mode_change():
    pass


# ─── Play track from playlist table ───────────────────────────────────────────
async def _play_track(track, client=None):
    from playlist_arranger.sources.spotify_source import play_track_on_device
    if not _state.sp or not _state.spotify_device_id:
        if client:
            with client:
                ui.notify("Connect Spotify and select a device first", type="warning")
        else:
            ui.notify("Connect Spotify and select a device first", type="warning")
        return
    try:
        uri = f"spotify:track:{track['id']}"
        play_track_on_device(_state.sp, uri, _state.spotify_device_id)
        if client:
            with client:
                ui.notify(f"Playing: {track['name']}", type="positive")
        else:
            ui.notify(f"Playing: {track['name']}", type="positive")
    except Exception as e:
        if client:
            with client:
                ui.notify(f"Play failed: {e}", type="negative")
        else:
            ui.notify(f"Play failed: {e}", type="negative")


def _get_track_status(track: dict) -> str:
    tid = track["id"]
    entry = _db.get_track(tid)
    if not entry:
        return "✗ Not in DB"
    missing = []
    features = entry.get("features")
    if not features or not isinstance(features, dict) or not features:
        missing.append("no features")
    s = load_settings()
    emb_path = s.embeds_dir / f"{tid}.npy"
    if not emb_path.exists():
        missing.append("no embedding")
    real_dur = track.get("duration_ms", 0)
    stored_dur = entry.get("duration_ms", 0)
    if real_dur > 0 and stored_dur > 0:
        from playlist_arranger.config import DURATION_TOLERANCE
        diff = abs(stored_dur - real_dur) / real_dur
        if diff > DURATION_TOLERANCE:
            missing.append("duration mismatch")
    if missing:
        return f"✗ {', '.join(missing)}"
    return "✓ OK"


def _load_cached_playlist_tracks(playlist_id: str) -> list:
    from playlist_arranger.sources.spotify_source import get_playlist_tracks as _fetch_tracks
    try:
        pl_data = _state.sp.playlist(playlist_id, fields="snapshot_id")
        snapshot_id = pl_data.get("snapshot_id", "")
    except Exception:
        snapshot_id = ""
    if snapshot_id:
        cache_file = CACHE_DIR_DEFAULT / f"{playlist_id}-{snapshot_id}.tracks.json"
        if cache_file.exists():
            try:
                return json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("Corrupted cache file, re-fetching")
    logger.info("Fetching tracks from Spotify for playlist %s", playlist_id[:8])
    tracks = _fetch_tracks(_state.sp, playlist_id)
    pattern = str(CACHE_DIR_DEFAULT / f"{playlist_id}-*.tracks.json")
    for old_file in _glob.glob(pattern):
        try:
            pathlib.Path(old_file).unlink()
        except Exception:
            pass
    if snapshot_id:
        cache_file = CACHE_DIR_DEFAULT / f"{playlist_id}-{snapshot_id}.tracks.json"
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(tracks, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to write cache file: %s", e)
    return tracks


def build_spotify_section(set_page_cb):
    global _page_client
    _page_client = ui.context.client
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
                    set_page_cb("spotify_source")
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
                    logger.exception("Failed to list Spotify devices")
                    ui.notify(f"Failed to refresh devices: {e}", type="negative")

            with ui.row().classes("w-full gap-1 items-center"):
                device_select = ui.select(label="Spotify Device", options={}, with_input=True).classes("flex-grow")

                def on_device_change(e):
                    _state.spotify_device_id = device_select.value

                device_select.on("update:model-value", on_device_change)
                ui.button(icon="refresh", on_click=_refresh_spotify_devices).props("flat round dense size=sm").tooltip("Refresh devices")

            if _state.sp:
                ui.timer(0.2, _refresh_spotify_devices, once=True)

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
                default_audio_idx = str(s.selected_audio_device_index) if s.selected_audio_device_index is not None else None

            global _viz_sr_label, _viz_rms_label, _viz_peak_label

            with ui.row().classes("w-full items-start gap-2 mb-2"):
                audio_device_select = ui.select(label="Audio Capture Device", options=audio_device_options,
                                                value=default_audio_idx).classes("flex-grow")

                # Canvas + stats side by side — fixed total width (never reflows)
                with ui.row().classes("flex-shrink-0 gap-2 items-start"):
                    ui.html(f'''
                        <canvas id="{_viz_canvas_id}" width="120" height="60"
                                style="width:120px;height:60px;display:block;border-radius:4px;background:#FFFFFF;border:1px solid #E0E0E0;"></canvas>
                    ''')

                    with ui.column().classes("gap-0"):
                        _viz_sr_label = ui.label("SR: — Hz").classes("text-xs font-mono text-gray-500 w-24")
                        _viz_rms_label = ui.label("RMS: — dB").classes("text-xs font-mono text-gray-500 w-24")
                        _viz_peak_label = ui.label("Pk: — dB").classes("text-xs font-mono text-gray-500 w-24")

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

# ─── Queue for Analysis section ──────────────────────────────────────────────
_queue_table_ref = None          # ui.table instance for the queue tracks
_queue_rows_cache = []           # rows list for row_key lookup
_queue_container = None          # the ui.column that holds the queue table + controls
_queue_expansion_ref = None      # the ui.expansion for collapsible state
_queue_label_ref = None          # ui.label showing "Queue for Analysis — N tracks"


def _persist_queue():
    """Save queue to disk after every mutation."""
    _state.save_analysis_queue()


def _rebuild_queue_ui():
    """Clear and re-render the queue table + controls inside the expansion body."""
    global _queue_table_ref, _queue_rows_cache, _queue_container
    if _queue_container is None:
        return
    _queue_container.clear()
    with _queue_container:
        _render_queue_table()
        _render_queue_controls()


def _add_selected_to_queue(pl_id: str, tracks: list):
    """Append checked tracks (in row order) to the analysis queue."""
    selected_rows = _get_selected_rows(pl_id)
    if not selected_rows:
        ui.notify("No tracks selected", type="warning")
        return

    # Map selected row idx values back to track dicts in row order
    selected_idxs = sorted(r["idx"] for r in selected_rows)
    added = 0
    for idx in selected_idxs:
        i = idx - 1  # rows are 1-indexed
        if 0 <= i < len(tracks):
            _state.analysis_queue.append(tracks[i])
            added += 1

    _persist_queue()
    _update_queue_label()
    _rebuild_queue_ui()
    ui.notify(f"Added {added} track(s) to queue", type="positive")
    logger.info("Added %d track(s) to analysis queue (total=%d)", added, len(_state.analysis_queue))


def _update_queue_label():
    """Refresh the expansion label text with current queue count."""
    global _queue_label_ref, _queue_expansion_ref
    n = len(_state.analysis_queue)
    label = f"Queue for Analysis — {n} track{'s' if n != 1 else ''}"
    if _queue_label_ref is not None:
        _queue_label_ref.set_text(label)
    if _queue_expansion_ref is not None:
        _queue_expansion_ref.set_text(label)


def _render_queue_table():
    """Render the track table inside the queue section."""
    global _queue_table_ref, _queue_rows_cache
    if not _state.analysis_queue:
        ui.label("Queue is empty").classes("text-sm text-gray-400 italic")
        return

    columns = [
        {"name": "idx", "label": "#", "field": "idx", "sortable": True},
        {"name": "name", "label": "Track", "field": "name"},
        {"name": "artist", "label": "Artist", "field": "artist"},
        {"name": "duration", "label": "Dur", "field": "duration"},
        {"name": "status", "label": "Status", "field": "status"},
    ]
    rows = []
    for i, t in enumerate(_state.analysis_queue, 1):
        dur_ms = t.get("duration_ms", 0)
        dur_str = f"{dur_ms // 60000}:{(dur_ms // 1000) % 60:02d}" if dur_ms else "?"
        status = _get_track_status(t)
        rows.append({"idx": i, "name": t.get("name", "")[:42], "artist": t.get("artist", "")[:40],
                     "duration": dur_str, "status": status})

    _queue_table_ref = ui.table(
        columns=columns, rows=rows, row_key="idx",
        selection="multiple",
        pagination={"rowsPerPage": 0},
    ).classes("w-full").props("dense")
    _queue_rows_cache = rows

    # Double-click → play track from queue
    def on_row_dblclick(e):
        row_data = e.args[1] if isinstance(e.args, list) and len(e.args) >= 2 else {}
        row_idx = row_data.get("idx", 0) - 1
        if 0 <= row_idx < len(_state.analysis_queue):
            track = _state.analysis_queue[row_idx]
            client = ui.context.client
            ui.timer(0.0, lambda t=track, c=client: asyncio.ensure_future(_play_track(t, client=c)), once=True)

    _queue_table_ref.on("rowDblclick", on_row_dblclick)


def _render_queue_controls():
    """Render the three control buttons above the queue table."""
    with ui.row().classes("w-full gap-2 mb-2"):
        # ── 1. Start Batch Analysis ────────────────────────────────────────
        def _on_start_batch():
            logger.info("Batch analysis started — stub (execution logic not yet implemented)")
            ui.notify("Batch analysis started (stub — real execution coming soon)", type="positive")

        can_batch = len(_state.analysis_queue) > 0 and _state.spotify_device_id is not None
        ui.button("Start Batch Analysis", on_click=_on_start_batch, color="green").classes("text-sm").set_enabled(can_batch)

        # ── 2. Remove Selected Tracks ───────────────────────────────────────
        def _on_remove_selected():
            global _queue_table_ref
            if _queue_table_ref is None:
                return
            selected = list(_queue_table_ref.selected) if hasattr(_queue_table_ref, 'selected') else []
            if not selected:
                return
            to_remove = sorted((r["idx"] for r in selected), reverse=True)
            for idx in to_remove:
                i = idx - 1
                if 0 <= i < len(_state.analysis_queue):
                    del _state.analysis_queue[i]
            _persist_queue()
            _update_queue_label()
            _rebuild_queue_ui()
            ui.notify(f"Removed {len(to_remove)} track(s) from queue", type="positive")

        def _refresh_remove_btn():
            nonlocal remove_btn
            if _queue_table_ref is not None:
                selected = list(_queue_table_ref.selected) if hasattr(_queue_table_ref, 'selected') else []
                remove_btn.set_enabled(len(selected) > 0)

        remove_btn = ui.button("Remove Selected Tracks from Queue", on_click=_on_remove_selected, color="orange").classes("text-sm")
        remove_btn.set_enabled(False)
        ui.timer(0.5, _refresh_remove_btn)

        # ── 3. Remove All Tracks ────────────────────────────────────────────
        def _on_remove_all():
            _state.analysis_queue.clear()
            _persist_queue()
            _update_queue_label()
            _rebuild_queue_ui()
            ui.notify("Queue cleared", type="positive")

        ui.button("Remove All Tracks from Queue", on_click=_on_remove_all, color="red").classes("text-sm").set_enabled(len(_state.analysis_queue) > 0)


def _render_analysis_queue():
    """Render the 'Queue for Analysis' section as a collapsible expansion panel."""
    global _queue_expansion_ref, _queue_label_ref, _queue_container
    n = len(_state.analysis_queue)
    label = f"Queue for Analysis — {n} track{'s' if n != 1 else ''}"

    with ui.expansion(label, value=False).classes("w-full mb-4") as _queue_expansion_ref:
        _queue_expansion_ref.props('header-class="text-lg font-semibold"')
        _queue_label_ref = ui.label(label)  # dummy — expansion label is the primary display
        _queue_container = ui.column().classes("w-full")
        with _queue_container:
            _render_queue_table()
            _render_queue_controls()


def _render_playlists(set_page_cb):
    global _render_playlists_set_page_cb
    _render_playlists_set_page_cb = set_page_cb
    ui.separator()

    # ── Queue for Analysis (above "Your Playlists") ──────────────────────
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
            _playlist_expansions[pl_id] = (exp, content_col)

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
    """Return list of user-checked row dicts, excluding the now-playing row (which shares .selected)."""
    table = _playlist_tables.get(pl_id)
    if table is None:
        return []
    selected = list(table.selected) if hasattr(table, 'selected') else []
    np_key = _now_playing_row_keys.get(pl_id)
    if np_key is not None:
        selected = [r for r in selected if r.get("idx") != np_key]
    return selected


def _show_track_compact_table(tracks, pl_id, pl_name, set_page_cb):
    in_db = sum(1 for t in tracks if _get_track_status(t) == "✓ OK")
    ui.label(f"Total: {len(tracks)} | In DB: {in_db} | Double-click a row to ▶ Play").classes("text-xs text-gray-500 mb-2")

    columns = [
        {"name": "idx", "label": "#", "field": "idx", "sortable": True},
        {"name": "name", "label": "Track", "field": "name"},
        {"name": "artist", "label": "Artist", "field": "artist"},
        {"name": "duration", "label": "Dur", "field": "duration"},
        {"name": "status", "label": "Status", "field": "status"},
    ]
    rows = []
    for i, t in enumerate(tracks, 1):
        dur_ms = t.get("duration_ms", 0)
        dur_str = f"{dur_ms // 60000}:{(dur_ms // 1000) % 60:02d}" if dur_ms else "?"
        status = _get_track_status(t)
        rows.append({"idx": i, "name": t["name"][:42], "artist": t["artist"][:40], "duration": dur_str, "status": status})

    track_table = ui.table(
        columns=columns, rows=rows, row_key="idx",
        selection="multiple",
        pagination={"rowsPerPage": 0},
    ).classes("w-full").props("dense")

    # Store table + rows for native selection highlight from background thread
    _playlist_tables[pl_id] = track_table
    _playlist_rows_cache[pl_id] = rows

    # Double-click → play track (single-click only selects/highlights natively)
    def on_row_dblclick(e):
        logger.debug("row-dblclick fired: raw e.args=%r", e.args)
        row_data = e.args[1] if isinstance(e.args, list) and len(e.args) >= 2 else {}
        row_idx = row_data.get("idx", 0) - 1
        if 0 <= row_idx < len(tracks):
            track = tracks[row_idx]
            client = ui.context.client
            ui.timer(0.0, lambda t=track, c=client: asyncio.ensure_future(_play_track(t, client=c)), once=True)
            # Optimistic highlight (additive — preserves user-checked rows)
            if track_table is not None:
                prev_np = _now_playing_row_keys.get(pl_id)
                current = list(track_table.selected) if hasattr(track_table, 'selected') else []
                if prev_np is not None:
                    current = [r for r in current if r.get("idx") != prev_np]
                if not any(r.get("idx") == row_data["idx"] for r in current):
                    current.append(row_data)
                track_table.selected = current
                _now_playing_row_keys[pl_id] = row_data["idx"]
                logger.debug("Optimistic selection applied on double-click: track=%s (total=%d)",
                             row_data.get("name", "")[:30], len(current))

    track_table.on("rowDblclick", on_row_dblclick)

    missing = sum(1 for t in tracks if _get_track_status(t) != "✓ OK")
    with ui.row().classes("w-full gap-2 mt-2"):
        # ── "Add Selected to Queue" button (replaces old "Analyze X missing") ──
        def _build_add_to_queue_btn():
            btn = ui.button(
                "Add Selected Tracks to Queue for Analysis",
                on_click=lambda: _add_selected_to_queue(pl_id, tracks),
                color="yellow",
            ).classes("text-sm")
            btn.set_enabled(len(_get_selected_rows(pl_id)) > 0)
            # Re-evaluate enabled state periodically (checkbox changes don't
            # trigger a full re-render, so we poll the selection count)
            def _refresh_btn_enabled():
                btn.set_enabled(len(_get_selected_rows(pl_id)) > 0)
            ui.timer(0.5, _refresh_btn_enabled)
            return btn

        _build_add_to_queue_btn()
        if _backup_exists(pl_id):
            ui.button("Recover from backup", on_click=lambda: _recover_from_backup(pl_id, set_page_cb), color="purple").classes("text-sm")


# TODO: unused after Queue for Analysis feature (2026-07-16) — remove if confirmed obsolete
def _run_spotify_analysis(tracks):
    pl_id = _state.current_playlist_id
    pl_name = _state.current_playlist_name

    def bg_task():
        try:
            from playlist_arranger.analysis.session import AnalysisSession
            to_analyze = [t for t in tracks if _get_track_status(t) != "✓ OK"]
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
    from playlist_arranger.config import LOCAL_MUSIC_DIR
    ui.label("Local Files Source").classes("text-2xl font-bold mb-2")
    current_dir = ui.label("").classes("text-sm text-gray-500")
    file_list = ui.column().classes("w-full")
    folder_input = ui.input(label="Folder path", value=str(LOCAL_MUSIC_DIR) if LOCAL_MUSIC_DIR else "").classes("w-full max-w-md")
    ui.button("Browse", on_click=lambda: scan_folder(folder_input.value)).classes("mb-3")

    def scan_folder(path):
        if not path:
            ui.notify("Please enter a folder path", type="warning"); return
        folder = pathlib.Path(path).resolve()
        if not folder.is_dir():
            ui.notify("Folder not found", type="negative"); return
        current_dir.set_text(f"Browsing: {folder}")
        file_list.clear()
        with file_list:
            if folder.parent != folder:
                ui.button("📁 ..", on_click=lambda f=folder.parent: scan_folder(str(f))).classes("text-sm w-full text-left")
            for d in sorted([d for d in folder.iterdir() if d.is_dir()]):
                ui.button(f"📁 {d.name}", on_click=lambda f=d: scan_folder(str(f))).classes("text-sm w-full text-left")
            audio_exts = {".mp3", ".flac"}
            files = sorted([f for f in folder.iterdir() if f.suffix.lower() in audio_exts])
            if files:
                ui.label(f"Audio files ({len(files)})").classes("text-sm font-bold mt-2 mb-1")
                from playlist_arranger.sources.local_source import _read_tags
                table_rows = []
                for f in files:
                    tags = _read_tags(f)
                    artist = tags["artist"]
                    title = tags["name"]
                    display = f"{artist} — {title}" if artist else title
                    size_kb = f.stat().st_size / 1024
                    size_str = f"{size_kb:.0f} KB" if size_kb < 1024 else f"{size_kb/1024:.1f} MB"
                    table_rows.append({"track": display[:60], "size": size_str})
                ui.table(columns=[{"name": "track", "label": "Track", "field": "track"}, {"name": "size", "label": "Size", "field": "size"}],
                         rows=table_rows, row_key="track", pagination=100).classes("w-full")
                ui.button(f"🎵 Use this folder ({len(files)} tracks)", on_click=lambda f=folder: load_local_folder(f), color="green").classes("mt-2")

    def load_local_folder(folder):
        from playlist_arranger.sources.local_source import scan_folder as _scan, make_playlist_id
        tracks = _scan(folder)
        if not tracks:
            ui.notify("No readable audio files found", type="warning"); return
        _state.current_playlist_id = make_playlist_id(folder)
        _state.current_playlist_name = folder.name
        _state.current_playlist_source = "local"
        _state.current_tracks[:] = tracks
        ui.notify(f"Loaded {len(tracks)} tracks from '{folder.name}'", type="positive")
        set_page_cb("local_source")

    if _state.current_tracks and _state.current_playlist_source == "local":
        _show_track_compact_table(_state.current_tracks, _state.current_playlist_id, _state.current_playlist_name, set_page_cb)