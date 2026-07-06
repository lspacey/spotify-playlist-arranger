"""Playlist source selection: Spotify / Local files."""

import pathlib
import asyncio
import json
import glob as _glob
import threading
import time
import logging

from nicegui import ui

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
_current_playlist_uri = ""  # Spotify playlist context URI (if playing from a playlist)

# UI element references (updated by polling thread)
_track_name_label = None
_track_progress_label = None
_track_status_label = None
_btn_listen = None
_btn_analyze = None
_np_card_container = None

# ─── JS highlighting helpers ──────────────────────────────────────────────────

_HIGHLIGHT_JS_INJECTED = False

def _inject_highlight_js():
    """Inject the highlightPlayingTrack JS function once.
    
    Architecture note:
    Track/playlist highlighting uses direct client-side JS DOM manipulation
    (not NiceGUI reactive rebind or Python-side table rebuild) for performance
    — avoids server round-trip on every poll tick. This is an intentional
    exception to the table-rebuild contract documented in track_table.py, since
    highlighting is purely visual state, not row data. If the page/table is
    rebuilt (e.g. playlist re-expanded), _attach_track_data_attrs() and
    _checkExpandHighlight() must be called again to re-bind the DOM attributes
    JS depends on.
    """
    global _HIGHLIGHT_JS_INJECTED
    if _HIGHLIGHT_JS_INJECTED:
        return
    _HIGHLIGHT_JS_INJECTED = True
    ui.run_javascript("""
    window._pa_playing_playlist_id = '';
    window._pa_playing_track_id = '';
    window._pa_highlightPlayingTrack = function(plid, tid) {
        // Remove all highlights
        document.querySelectorAll('.pa-playlist-highlight').forEach(el => {
            el.classList.remove('pa-playlist-highlight');
            el.style.backgroundColor = '';
        });
        document.querySelectorAll('.pa-track-highlight').forEach(el => {
            el.classList.remove('pa-track-highlight');
            el.style.backgroundColor = '';
        });
        window._pa_playing_playlist_id = plid || '';
        window._pa_playing_track_id = tid || '';
        if (!plid) return;
        // Find playlist expansion header by data-pl-id
        const exp = document.querySelector(`[data-pl-id="${plid}"]`);
        if (exp) {
            exp.classList.add('pa-playlist-highlight');
            exp.style.backgroundColor = '#fef3c7'; // amber-100
        }
        if (!tid) return;
        // Find track row by data-track-id
        const row = document.querySelector(`[data-track-id="${tid}"]`);
        if (row) {
            row.classList.add('pa-track-highlight');
            row.style.backgroundColor = '#dbeafe'; // blue-100
        }
    };
    window._pa_checkExpandHighlight = function(plid) {
        if (plid === window._pa_playing_playlist_id && window._pa_playing_track_id) {
            setTimeout(() => {
                const row = document.querySelector(`[data-track-id="${window._pa_playing_track_id}"]`);
                if (row) {
                    row.classList.add('pa-track-highlight');
                    row.style.backgroundColor = '#dbeafe';
                }
            }, 300);
        }
    };
    """)

def _notify_playing_track(playlist_id: str, track_id: str):
    """Call JS to highlight the currently playing track in the playlist."""
    _inject_highlight_js()
    plid = playlist_id or ""
    tid = track_id or ""
    ui.run_javascript(f"window._pa_highlightPlayingTrack('{plid}', '{tid}')")
    logger.debug("Highlight JS: pl=%s track=%s", plid[:8] if plid else '', tid[:8] if tid else '')


# ─── Card logic: background polling thread ────────────────────────────────────
def _card_listen_thread():
    """Background thread: polls Spotify API, captures audio when analyzing."""
    global _current_track, _current_track_elapsed, _current_track_status
    global _listen_mode, _analyze_mode, _listen_stop
    import numpy as np
    from playlist_arranger.config import POLL_FAST, POLL_NORMAL
    from playlist_arranger.audio import capture as _cap
    from playlist_arranger.audio.features import _to_mono

    last_track_id = None
    last_playing_time = 0.0
    track_start_wall = 0
    track_duration = 0
    track_progress_ms = 0
    track_analyzed = False  # has current track been saved to DB?
    audio_collected = None  # accumulated audio for current track
    _saved_on_stop = False  # guard against double-save on stop + track-change
    _last_highlighted_id = None  # track change-only log guard for highlight
    _last_highlighted_pl = None  # playlist change-only log guard

    def _do_save(track_id: str, audio, reason: str) -> None:
        """Save captured audio with trigger reason DEBUG log."""
        nonlocal _saved_on_stop
        if _saved_on_stop:
            logger.debug("Save skipped (already saved on stop): %s [%s]",
                          track_id[:8] if track_id else "?", reason)
            return
        src = _cap.actual_sr if _cap.actual_sr else 22050
        audio_secs = len(audio) / src if audio is not None and len(audio) > 0 else 0.0
        if audio is None or audio_secs < 5.0:
            logger.debug("Save skipped — insufficient audio (%.1fs): %s [%s]",
                         audio_secs, track_id[:8] if track_id else "?", reason)
            return
        try:
            _save_captured_track(track_id, audio)
            logger.debug("Save OK: %s [%s] — %ds audio",
                          track_id[:8], reason, int(audio_secs))
            _saved_on_stop = True
        except Exception as e:
            logger.debug("Save FAILED: %s [%s] — %s",
                          track_id[:8], reason, e)

    while not _listen_stop.is_set():
        with _mode_lock:
            lm = _listen_mode
            am = _analyze_mode

        if lm == 0:
            # Not listening — sleep and loop
            time.sleep(1.0)
            _current_track = None
            _current_track_elapsed = 0
            _current_track_status = ""
            continue

        # Listen mode is ON — poll Spotify API
        try:
            cp = _state.sp.current_playback() if _state.sp else None
        except Exception:
            time.sleep(POLL_FAST)
            continue

        if not cp or not cp.get("is_playing"):
            # Nothing playing
            _current_track = None
            _current_track_elapsed = 0
            _current_track_status = "No track playing"
            last_track_id = None
            track_analyzed = False
            audio_collected = None
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

        # Update current track info and reset idle timer
        last_playing_time = time.time()
        _current_track = {
            "id": tid,
            "name": item.get("name", "?"),
            "artist": ", ".join(a.get("name", "") for a in (item.get("artists") or [])),
            "album": (item.get("album") or {}).get("name", ""),
            "duration_ms": dur_ms,
        }
        _current_track_elapsed = progress_ms

        # Extract Spotify context (playlist URI) for highlight tracking
        context = cp.get("context") or {}
        context_uri = context.get("uri", "")
        # Parse playlist ID from context URI (e.g. "spotify:playlist:1a2b3c4d")
        pl_id_from_context = ""
        if context_uri.startswith("spotify:playlist:"):
            pl_id_from_context = context_uri.split(":")[-1]

        # Update highlight only when playlist or track actually changes
        if pl_id_from_context != _last_highlighted_pl or tid != _last_highlighted_id:
            _last_highlighted_pl = pl_id_from_context
            _last_highlighted_id = tid
            _notify_playing_track(pl_id_from_context, tid)

        # Track change detection
        if tid != last_track_id:
            if last_track_id is not None and am == 1 and not _saved_on_stop:
                _do_save(last_track_id, audio_collected, "track_change")
            last_track_id = tid
            track_start_wall = time.time() - progress_ms / 1000.0
            track_duration = dur_ms
            track_progress_ms = progress_ms
            track_analyzed = False
            audio_collected = np.array([], dtype=np.float32) if am == 1 else None

        # Status line
        dm, ds = divmod(dur_ms // 1000, 60)
        em, es = divmod(progress_ms // 1000, 60)
        remaining = max(0, dur_ms - progress_ms)
        rm, rs = divmod(remaining // 1000, 60)
        pct = min(progress_ms / max(dur_ms, 1) * 100, 100)

        if am == 0:
            _current_track_status = f"{em}:{es:02d} / {dm}:{ds:02d}  ({pct:.0f}%)"
        else:
            _current_track_status = f"🔍 {em}:{es:02d} / {dm}:{ds:02d}  ({pct:.0f}%)  recording..."

        # Collect audio if analyzing
        if am == 1 and _cap.audio_deque is not None:
            with _cap.audio_lock:
                new_samples = np.array(list(_cap.audio_deque), dtype=np.float32)
                _cap.audio_deque.clear()
            if len(new_samples) > 0:
                mono = _to_mono(new_samples, _cap.actual_channels)
                if audio_collected is None:
                    audio_collected = mono
                else:
                    audio_collected = np.concatenate([audio_collected, mono])

        # Adaptive polling interval
        # Fast near track start (first 10%) and near end (last 10%)
        if progress_ms < dur_ms * 0.10 or progress_ms > dur_ms * 0.90:
            interval = POLL_FAST  # 2s
        else:
            interval = POLL_NORMAL  # 5s

        # Sleep in small increments so we can respond to stop quickly
        slept = 0.0
        while slept < interval and not _listen_stop.is_set():
            time.sleep(0.5)
            slept += 0.5
            with _mode_lock:
                if _listen_mode == 0:
                    break

    # Cleanup on stop — save whatever audio was collected
    # (auto_stop flows here via the break above; manual_stop flows here via _listen_stop.set())
    stop_reason = "auto_stop" if not lm else "manual_stop"
    _do_save(last_track_id, audio_collected, stop_reason)


def _save_captured_track(track_id: str, audio_data):
    """Save captured audio to DB via save_track_worker."""
    global _current_track
    if not _current_track or _current_track["id"] != track_id:
        return
    from playlist_arranger.analysis.worker import save_track_worker
    try:
        save_track_worker(
            track_info=_current_track,
            playlist_name="Now Playing",
            playlist_uri="",
            y_full=audio_data,
            status_cb=lambda msg: logger.info("NP: %s", msg),
        )
        logger.info("Saved analyzed track: %s", _current_track.get("name", "?"))
    except Exception as e:
        logger.exception("Failed to save track %s", track_id)


# ─── UI updater tick ──────────────────────────────────────────────────────────
def _update_np_ui():
    """Called by ui.timer to refresh Now Playing card contents."""
    global _np_card_container
    if _np_card_container is None:
        return
    _np_card_container.clear()
    with _np_card_container:
        _render_np_inner()


# ─── Now Playing card UI ──────────────────────────────────────────────────────
def _build_now_playing_card():
    """Build the standalone Now Playing card with Listen / Analyze modes."""
    global _np_card_container, _btn_listen, _btn_analyze
    global _track_name_label, _track_progress_label, _track_status_label

    with ui.card().classes("w-full") as card:
        with ui.column().classes("w-full gap-2") as _np_card_container:
            _render_np_inner()
        # Auto-refresh UI every 500ms
        ui.timer(0.5, _update_np_ui)


def _render_np_inner():
    """Render inner content of Now Playing card. Called on build + every timer tick."""
    global _btn_listen, _btn_analyze
    global _track_name_label, _track_progress_label, _track_status_label

    # ── Buttons row ──
    with ui.row().classes("w-full gap-2 items-center"):
        # Listen button
        def on_listen_click():
            global _listen_mode, _listen_thread, _listen_stop
            with _mode_lock:
                if _listen_mode == 0:
                    # Start listening
                    _listen_mode = 1
                    _listen_stop.clear()
                    _listen_thread = threading.Thread(target=_card_listen_thread, daemon=True)
                    _listen_thread.start()
                    logger.info("Now Playing: listening started")
                else:
                    # Stop listening + analyzing
                    _listen_mode = 0
                    _analyze_mode = 0
                    _listen_stop.set()
                    logger.info("Now Playing: listening stopped")

        can_listen = (
            _state.sp is not None
            and _state.spotify_device_id is not None
            and _state.audio_capture_device_index is not None
        )

        btn_text = "Stop Listening" if _listen_mode == 1 else "Listen"
        btn_color = "red" if _listen_mode == 1 else "green"
        _btn_listen = ui.button(btn_text, on_click=on_listen_click, color=btn_color).classes("text-sm")
        _btn_listen.set_enabled(can_listen)

        # Analyze button
        def on_analyze_click():
            global _analyze_mode
            with _mode_lock:
                if _listen_mode != 1:
                    return
                if _analyze_mode == 0:
                    _analyze_mode = 1
                    logger.info("Now Playing: analysis started")
                else:
                    _analyze_mode = 0
                    logger.info("Now Playing: analysis stopped")

        analyze_text = "Analyzing..." if _analyze_mode == 1 else "Start analysis"
        analyze_color = "orange" if _analyze_mode == 1 else "blue"
        _btn_analyze = ui.button(analyze_text, on_click=on_analyze_click, color=analyze_color).classes("text-sm")
        _btn_analyze.set_enabled(_listen_mode == 1)

        # Mode indicators
        mode_text = []
        if _listen_mode == 1:
            mode_text.append("🎧 Listening")
        if _analyze_mode == 1:
            mode_text.append("🔍 Analyzing")
        mode_str = " | ".join(mode_text) if mode_text else "Idle"
        ui.label(mode_str).classes("text-xs text-gray-500 ml-2")

    # ── Track info display ──
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

    _track_name_label = ui.label(
        f"🎵 {t['name'][:60]} — {t['artist'][:40]}"
    ).classes("text-base font-semibold")
    ui.label(f"Album: {t.get('album', '?')[:50]}").classes("text-xs text-gray-500")
    ui.label(f"Duration: {dur_str}").classes("text-xs text-gray-500")

    _track_status_label = ui.label(_current_track_status).classes("text-sm text-green-600 dark:text-green-400 mt-1")

    # Progress bar
    elapsed = _current_track_elapsed
    pct = min(elapsed / max(dur_ms, 1) * 100, 100) if dur_ms else 0
    em, es = divmod(elapsed // 1000, 60)
    rm = max(0, dur_ms - elapsed) // 1000
    rem, res = divmod(rm, 60)

    _track_progress_label = ui.label(
        f"▶ {em}:{es:02d}  [{pct:.0f}%]  -{rem}:{res:02d}"
    ).classes("text-xs text-gray-400")
    ui.linear_progress(value=pct / 100).classes("w-full")


# ─── Mode change helper ───────────────────────────────────────────────────────
def _process_mode_change():
    """Called after any button press to sync UI state."""
    pass  # UI updates happen via the ui.timer refreshing every 500ms


# ─── Play track from playlist table ───────────────────────────────────────────
async def _play_track(track):
    """Play a single track on the selected Spotify device."""
    from playlist_arranger.sources.spotify_source import play_track_on_device

    if not _state.sp or not _state.spotify_device_id:
        ui.notify("Connect Spotify and select a device first", type="warning")
        return

    try:
        uri = f"spotify:track:{track['id']}"
        play_track_on_device(_state.sp, uri, _state.spotify_device_id)
        ui.notify(f"Playing: {track['name']}", type="positive")
    except Exception as e:
        ui.notify(f"Play failed: {e}", type="negative")


# ─── Track status helper ──────────────────────────────────────────────────────
def _get_track_status(track: dict) -> str:
    """
    Return a detailed status string for a track.
    Checks DB entry, embedding file existence, and duration match.
    Uses configured embeds_dir from settings.
    """
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


# ─── Playlist tracks caching ───────────────────────────────────────────────────

def _load_cached_playlist_tracks(playlist_id: str) -> list:
    """Load playlist tracks from cache or Spotify API, using snapshot_id for invalidation."""
    from playlist_arranger.sources.spotify_source import get_playlist_tracks as _fetch_tracks

    # Get current snapshot_id from Spotify
    try:
        pl_data = _state.sp.playlist(playlist_id, fields="snapshot_id")
        snapshot_id = pl_data.get("snapshot_id", "")
    except Exception:
        snapshot_id = ""

    if snapshot_id:
        cache_file = CACHE_DIR_DEFAULT / f"{playlist_id}-{snapshot_id}.tracks.json"
        if cache_file.exists():
            logger.info("Loading cached tracks for playlist %s (snapshot %s)", playlist_id[:8], snapshot_id[:8])
            try:
                return json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("Corrupted cache file, re-fetching")

    # Cache miss or no snapshot — fetch from Spotify
    logger.info("Fetching tracks from Spotify for playlist %s", playlist_id[:8])
    tracks = _fetch_tracks(_state.sp, playlist_id)

    # Delete old cache files for this playlist
    pattern = str(CACHE_DIR_DEFAULT / f"{playlist_id}-*.tracks.json")
    for old_file in _glob.glob(pattern):
        try:
            pathlib.Path(old_file).unlink()
        except Exception:
            pass

    # Save new cache
    if snapshot_id:
        cache_file = CACHE_DIR_DEFAULT / f"{playlist_id}-{snapshot_id}.tracks.json"
        try:
            cache_file.write_text(json.dumps(tracks, ensure_ascii=False), encoding="utf-8")
            logger.info("Cached %d tracks for playlist %s (snapshot %s)", len(tracks), playlist_id[:8], snapshot_id[:8])
        except Exception:
            logger.warning("Failed to write cache file")

    return tracks


# ─── Spotify section ──────────────────────────────────────────────────────────
def build_spotify_section(set_page_cb):
    """Build Spotify source section using set_page_cb for navigation-only re-renders."""
    ui.label("Spotify Source").classes("text-2xl font-bold mb-2")

    # --- Connection status and button ---
    with ui.row().classes("w-full gap-4 items-start"):
        # Left: connection + device
        with ui.column().classes("flex-1"):
            if _state.sp is not None and _state.spotify_user_id:
                ui.label(f"✓ Connected as {_state.spotify_user_id}").classes("text-sm text-green-600")
            else:
                ui.label("Not connected").classes("text-sm text-gray-500")

            async def do_connect():
                from playlist_arranger.sources.spotify_source import init_spotify as _init_spotify
                try:
                    logger.info("Connecting to Spotify...")
                    result = await asyncio.to_thread(_init_spotify, None)
                    _state.sp, _state.spotify_user_id = result
                    logger.info("Spotify connected as: %s", _state.spotify_user_id)
                    set_page_cb("spotify_source")
                except Exception as e:
                    logger.exception("Spotify connect failed")
                    ui.notify(f"Spotify connect failed: {e}", type="negative")

            ui.button("Connect to Spotify", on_click=do_connect).classes("text-sm mb-2")

            # Spotify Device dropdown
            device_select = ui.select(
                label="Spotify Device", options={}, with_input=True,
            ).classes("w-full max-w-md")

            def on_device_change(e):
                _state.spotify_device_id = e.args

            device_select.on("update:model-value", on_device_change)

            if _state.sp:
                try:
                    devs = _state.sp.devices().get("devices", [])
                    logger.info("Found %d Spotify devices", len(devs))
                    opts = {d["id"]: d["name"] for d in devs}
                    device_select.set_options(opts)
                    if _state.spotify_device_id:
                        device_select.set_value(_state.spotify_device_id)
                except Exception:
                    logger.exception("Failed to list Spotify devices")

        # Right: Audio capture device + Now Playing card
        with ui.column().classes("flex-1"):
            # Audio capture device dropdown
            from playlist_arranger.audio.capture import list_loopback_devices, start_audio_capture, stop_capture

            devices = list_loopback_devices()
            audio_device_options = {}
            for d in devices:
                label = f"[{'LOOP' if d['loopback'] else 'IN'}] {d['name'][:50]} ({d['channels']}ch @ {d['sr']}Hz)"
                audio_device_options[str(d["index"])] = label

            s = _state.get_settings()
            default_audio_idx = str(s.selected_audio_device_index) if s.selected_audio_device_index is not None else None

            audio_device_select = ui.select(
                label="Audio Capture Device",
                options=audio_device_options,
                value=default_audio_idx,
            ).classes("w-full max-w-md mb-2")

            def on_audio_device_change(e):
                new_idx = e.args
                if new_idx is not None:
                    idx = int(new_idx)
                    _state.audio_capture_device_index = idx
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
                logger.info("Auto-started audio capture on: %s", dev_name)

            _build_now_playing_card()

    # --- Playlists ---
    if _state.sp is not None and _state.spotify_user_id:
        _render_playlists(set_page_cb)


def _render_playlists(set_page_cb):
    """Render playlist list with expandable items. Loads tracks on expand."""
    ui.separator()
    ui.label("Your Playlists").classes("text-lg font-bold mb-2")

    from playlist_arranger.sources.spotify_source import get_own_playlists
    pls = get_own_playlists(_state.sp, _state.spotify_user_id)
    logger.info("Loaded %d Spotify playlists", len(pls))

    for pl in pls:
        pl_id = pl["id"]
        pl_name = pl["name"]
        total = pl.get("tracks", {}).get("total")
        label = f"{pl_name}" + (f" ({total} tracks)" if total is not None else "")

        with ui.expansion(label, value=pl_id == _state.current_playlist_id).classes("w-full") as exp:
            exp.props('header-class="text-lg font-semibold"')
            # Add data-pl-id attribute for JS highlighting
            ui.run_javascript(f"document.querySelectorAll('[data-pl-id]').forEach(el => {{ if (!el.hasAttribute('data-pl-id-set')) {{ el.setAttribute('data-pl-id', ''); el.setAttribute('data-pl-id-set', '1'); }} }}); const expEls = document.querySelectorAll('.q-expansion-item'); if (expEls.length > 0) {{ expEls[expEls.length-1].setAttribute('data-pl-id', '{pl_id}'); }}")
            content_col = ui.column().classes("w-full")

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
                    logger.info("Loaded %d tracks from '%s'", len(tracks), pn)
                    col.clear()
                    with col:
                        _show_track_compact_table(tracks, pid, pn, set_page_cb)
                except Exception as exc:
                    logger.exception("Failed to load playlist tracks")
                    col.clear()
                    with col:
                        ui.label(f"Error: {exc}").classes("text-red-500 text-sm")

            exp.on("update:model-value", on_expand)

            if pl_id == _state.current_playlist_id and _state.current_tracks:
                with content_col:
                    _show_track_compact_table(_state.current_tracks, pl_id, pl_name, set_page_cb)


def _show_track_compact_table(tracks, pl_id, pl_name, set_page_cb):
    """Show compact track table. Row click plays the track on Spotify.
    
    IMPORTANT — Table refresh contract (NiceGUI 3.x compatibility):
    This table is rebuilt on every expansion toggle and full page rebuild
    via set_page_cb(). Do NOT mutate the `rows` list in-place after table
    creation (e.g. .append(), .remove(), `rows[i] = ...`) because NiceGUI 3.x
    will NOT detect in-place list mutations. To update reactively, explicitly
    reassign `track_table.rows = new_rows` after mutation.
    """
    in_db = sum(1 for t in tracks if _get_track_status(t) == "✓ OK")
    ui.label(f"Total: {len(tracks)} | In DB: {in_db} | Click any row to ▶ Play").classes("text-xs text-gray-500 mb-2")

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
        rows.append({
            "idx": i,
            "name": t["name"][:42],
            "artist": t["artist"][:40],
            "duration": dur_str,
            "status": status,
        })

    track_table = ui.table(
        columns=columns, rows=rows,
        row_key="idx",
        pagination={"rowsPerPage": 25},
    ).classes("w-full")

    def on_table_click(e):
        """Click any row to play the track on Spotify."""
        args = e.args
        if isinstance(args, list) and len(args) > 0:
            row_data = args[0]
        else:
            return
        if not isinstance(row_data, dict):
            return
        row_idx = row_data.get("idx", 0) - 1
        if 0 <= row_idx < len(tracks):
            track = tracks[row_idx]
            logger.info("Playing track: %s", track.get("name", "?"))
            ui.timer(0.0, lambda t=track: asyncio.ensure_future(_play_track(t)), once=True)

    track_table.on("rowClick", on_table_click)

    # After table render, attach data-track-id to rows for JS highlighting
    ui.timer(0.2, lambda: _attach_track_data_attrs(tracks), once=True)
    # Also check highlight after expansion opens
    ui.timer(0.3, lambda: ui.run_javascript(f"window._pa_checkExpandHighlight && window._pa_checkExpandHighlight('{pl_id}')"), once=True)

    missing = sum(1 for t in tracks if _get_track_status(t) != "✓ OK")
    with ui.row().classes("w-full gap-2 mt-2"):
        if missing > 0:
            ui.button(
                f"Analyze {missing} missing",
                on_click=lambda: _run_spotify_analysis(tracks),
                color="yellow",
            ).classes("text-sm")
        if _backup_exists(pl_id):
            ui.button(
                "Recover from backup",
                on_click=lambda: _recover_from_backup(pl_id, set_page_cb),
                color="purple",
            ).classes("text-sm")


def _run_spotify_analysis(tracks):
    """Run analysis in background thread."""
    pl_id = _state.current_playlist_id
    pl_name = _state.current_playlist_name

    def bg_task():
        try:
            from playlist_arranger.analysis.session import AnalysisSession
            to_analyze = [t for t in tracks if _get_track_status(t) != "✓ OK"]
            if not to_analyze:
                logger.info("All tracks already in DB for playlist '%s'", pl_name)
                return
            logger.info("Starting analysis of %d tracks for playlist '%s'", len(to_analyze), pl_name)
            session = AnalysisSession(
                sp=_state.sp, tracks=to_analyze,
                playlist_name=pl_name, playlist_uri=f"spotify:playlist:{pl_id}",
                spotify_device_id=_state.spotify_device_id,
                progress_cb=lambda msg: logger.info(msg),
            )
            session.run()
            logger.info("Analysis complete for playlist '%s'", pl_name)
        except Exception as e:
            logger.exception("Analysis failed for playlist '%s'", pl_name)

    threading.Thread(target=bg_task, daemon=True).start()


def _recover_from_backup(pl_id, set_page_cb):
    """Recover from backup and navigate to anchors."""
    from playlist_arranger.cache.store import load_backup as _load_backup

    bk_data = _load_backup(pl_id)
    if not bk_data or not bk_data.get("tracks"):
        logger.warning("No backup found for playlist: %s", pl_id)
        ui.notify("Backup empty or not found", type="warning")
        return

    logger.info("Recovering %d tracks from backup for playlist: %s", len(bk_data["tracks"]), pl_id)
    descs = []
    for t in bk_data["tracks"]:
        tid = t.get("id") or t.get("track_id", "")
        descs.append({
            "track_id": tid, "name": t.get("name", "?"), "artist": t.get("artist", "?"),
            "album": t.get("album", "?"), "description": "", "playlist": _state.current_playlist_name,
            "bpm": 0, "key": "", "camelot": "", "loudness_db": 0, "dynamic_range": 0,
            "harm_ratio": 0, "flatness": 0, "bass_pct": 0, "mid_pct": 0, "high_pct": 0,
            "onset_str": 0, "duration_ms": 0,
        })
    for d in descs:
        entry = _db.get_track(d["track_id"])
        if entry:
            f = entry.get("features") or {}
            d["bpm"] = round(f.get("bpm", 0), 1)
            d["key"] = f"{f.get('chroma_key', '')} {f.get('mode', '')}".strip()
            d["camelot"] = f.get("camelot", "")
            d["loudness_db"] = round(f.get("rms_db", 0), 1)
            d["dynamic_range"] = round(f.get("dynamic_range", 0), 1)
            d["harm_ratio"] = round(f.get("harm_ratio", 0), 2)
            d["flatness"] = round(f.get("flatness", 0), 3)
            d["bass_pct"] = round(f.get("bass", 0) * 100, 1)
            d["mid_pct"] = round(f.get("mid", 0) * 100, 1)
            d["high_pct"] = round(f.get("high", 0) * 100, 1)
            d["onset_str"] = round(f.get("onset_str", 0), 2)
            d["duration_ms"] = entry.get("duration_ms", 0)

    _state.current_descs[:] = descs
    logger.info("Recovered %d track descriptions from backup", len(descs))
    ui.notify(f"Recovered {len(descs)} tracks from backup", type="positive")
    set_page_cb("anchors")


def _attach_track_data_attrs(tracks):
    """Attach data-track-id attributes to table rows for JS highlighting."""
    js = ""
    for i, t in enumerate(tracks):
        tid = t["id"]
        js += f"var r = document.querySelectorAll('[data-row-key] tr'); if (r[{i}]) {{ r[{i}].setAttribute('data-track-id', '{tid}'); }} "
    if js:
        ui.run_javascript(js)

# ─── Local files section (unchanged) ──────────────────────────────────────────
def build_local_section(set_page_cb):
    """Build Local files source section with folder browser."""
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
                ui.table(
                    columns=[
                        {"name": "track", "label": "Track", "field": "track"},
                        {"name": "size", "label": "Size", "field": "size"},
                    ],
                    rows=table_rows,
                    row_key="track",
                    pagination=100,
                ).classes("w-full")
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
        logger.info("Loaded %d tracks from local folder: %s", len(tracks), folder)
        ui.notify(f"Loaded {len(tracks)} tracks from '{folder.name}'", type="positive")
        set_page_cb("local_source")

    if _state.current_tracks and _state.current_playlist_source == "local":
        _show_track_compact_table(_state.current_tracks, _state.current_playlist_id, _state.current_playlist_name, set_page_cb)