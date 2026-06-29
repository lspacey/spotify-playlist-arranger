"""Playlist source selection: Spotify / Local files."""

import pathlib
import asyncio
import threading
import logging

from nicegui import ui

from playlist_arranger.ui import state as _state
from playlist_arranger.cache.store import backup_exists as _backup_exists
from playlist_arranger.database import db as _db

logger = logging.getLogger(__name__)


def build_spotify_section(set_page_cb):
    """Build Spotify source section using set_page_cb for navigation-only re-renders."""
    ui.label("Spotify Source").classes("text-2xl font-bold mb-2")

    # --- Connection status and button ---
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

    # --- Device dropdown ---
    device_select = ui.select(label="Spotify Device", options={}, with_input=True).classes("w-full max-w-md")

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
            content_col = ui.column().classes("w-full")

            async def on_expand(e, pid=pl_id, pn=pl_name, col=content_col):
                """Load tracks when expansion is opened."""
                if not e.args:  # collapsed, do nothing
                    return
                col.clear()
                with col:
                    ui.spinner(size="sm")
                try:
                    from playlist_arranger.sources.spotify_source import get_playlist_tracks
                    logger.info("Fetching tracks for playlist: %s", pn)
                    tracks = await asyncio.to_thread(get_playlist_tracks, _state.sp, pid)
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

            # If this is the currently selected playlist, pre-load tracks
            if pl_id == _state.current_playlist_id and _state.current_tracks:
                with content_col:
                    _show_track_compact_table(_state.current_tracks, pl_id, pl_name, set_page_cb)


def _show_track_compact_table(tracks, pl_id, pl_name, set_page_cb):
    """Show compact track table + action buttons."""
    ui.label(f"Tracks: {pl_name}").classes("text-sm font-bold text-green-600 mb-1")
    in_db = sum(1 for t in tracks if not _state.get_track_needs_analysis(t["id"], t.get("duration_ms")))
    ui.label(f"Total: {len(tracks)} | In DB: {in_db}").classes("text-xs text-gray-500 mb-2")

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
        reason = _state.get_track_needs_analysis(t["id"], t.get("duration_ms"))
        status = f"⚠ {reason}" if reason else "✓ In DB"
        rows.append({"idx": i, "name": t["name"][:42], "artist": t["artist"][:26], "duration": dur_str, "status": status})

    ui.table(columns=columns, rows=rows, row_key="idx", pagination={"rowsPerPage": 25}).classes("w-full")

    missing = sum(1 for t in tracks if _state.get_track_needs_analysis(t["id"], t.get("duration_ms")))
    with ui.row().classes("w-full gap-2 mt-2"):
        if missing > 0:
            ui.button(f"Analyze {missing} missing", on_click=lambda: _run_spotify_analysis(tracks), color="yellow").classes("text-sm")
        if _backup_exists(pl_id):
            ui.button("Recover from backup", on_click=lambda: _recover_from_backup(pl_id, set_page_cb), color="purple").classes("text-sm")


def _run_spotify_analysis(tracks):
    """Run analysis in background thread. Logs progress to Python logger only."""
    pl_id = _state.current_playlist_id
    pl_name = _state.current_playlist_name

    def bg_task():
        try:
            from playlist_arranger.analysis.session import AnalysisSession
            to_analyze = [t for t in tracks if _state.get_track_needs_analysis(t["id"], t.get("duration_ms"))]
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
                # Build rows with tag data from mutagen
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