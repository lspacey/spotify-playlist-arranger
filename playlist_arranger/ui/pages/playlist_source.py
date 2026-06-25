"""Playlist source selection: Spotify / Local files."""

import pathlib
import asyncio
import threading

from nicegui import ui

from playlist_arranger.ui import state as _state
from playlist_arranger.cache.store import backup_exists as _backup_exists
from playlist_arranger.database import db as _db


def build_spotify_section(set_page_cb):
    """Build Spotify source section. set_page_cb(page_name) for navigation."""
    container = ui.column().classes("w-full gap-4")

    with container:
        ui.label("Spotify Source").classes("text-2xl font-bold mb-2")

        # --- Connection status and Connect button ---
        if _state.sp is not None and _state.spotify_user_id:
            status_text = f"✓ Connected as {_state.spotify_user_id}"
            status_cls = "text-sm text-green-600"
        else:
            status_text = "Not connected"
            status_cls = "text-sm text-gray-500"

        with ui.row().classes("w-full items-center gap-2"):
            ui.button("Connect to Spotify", on_click=lambda: connect_spotify()).classes("text-sm")
            status_label = ui.label(status_text).classes(status_cls)

        # --- Device dropdown ---
        device_select = ui.select(
            label="Spotify Device", options={}, with_input=True
        ).classes("w-full max-w-md")

        def on_device_change(e):
            _state.spotify_device_id = e.args

        device_select.on("update:model-value", on_device_change)

        # Restore saved device selection
        if _state.spotify_device_id and _state.sp:
            try:
                devs = _state.sp.devices().get("devices", [])
                opts = {d["id"]: d["name"] for d in devs}
                device_select.set_options(opts)
                device_select.set_value(_state.spotify_device_id)
            except Exception:
                pass

        # --- Playlists section ---
        playlists_container = ui.column().classes("w-full mt-4")

        # If connected, show playlists
        if _state.sp is not None and _state.spotify_user_id:
            _render_playlist_list(playlists_container, device_select)

    # --- Helper functions ---

    async def connect_spotify():
        from playlist_arranger.sources.spotify_source import init_spotify as _init_spotify

        try:
            result = await asyncio.to_thread(_init_spotify, None)
            sp_val, uid = result
            _state.sp = sp_val
            _state.spotify_user_id = uid
            set_page_cb("spotify_source")  # re-render
        except Exception as e:
            ui.notify(f"Spotify connect failed: {e}", type="negative")

    return container


def _render_playlist_list(container, device_select):
    """Render the playlist list with expandable items."""
    container.clear()

    with container:
        ui.separator()
        ui.label("Your Playlists").classes("text-lg font-bold mb-2")

        from playlist_arranger.sources.spotify_source import get_own_playlists

        pls = get_own_playlists(_state.sp, _state.spotify_user_id)

        for pl in pls:
            pl_id = pl["id"]
            pl_name = pl["name"]
            total = pl.get("tracks", {}).get("total")
            track_text = f" ({total} tracks)" if total is not None else ""

            with ui.expansion(
                f"{pl_name}{track_text}",
                value=pl_id == _state.current_playlist_id,
            ).classes("w-full") as expansion:
                # Highlight when expanded: add background
                with ui.column().classes("w-full"):
                    # Fetch tracks lazily and show
                    if pl_id == _state.current_playlist_id and _state.current_tracks:
                        _show_track_compact_table(_state.current_tracks, pl_id, pl_name)
                    else:
                        ui.label("Loading tracks...").classes("text-sm text-gray-500")
                        # Load tracks via a one-shot timer

                        def load_tracks(pid=pl_id, pn=pl_name):
                            from playlist_arranger.sources.spotify_source import get_playlist_tracks

                            try:
                                tracks = get_playlist_tracks(_state.sp, pid)
                                _state.current_playlist_id = pid
                                _state.current_playlist_name = pn
                                _state.current_playlist_source = "spotify"
                                _state.current_tracks[:] = tracks
                                # Re-render page to show tracks
                                ui.timer(0.1, lambda pn=pn: ui.notify(
                                    f"Loaded {len(tracks)} tracks from '{pn}'", type="positive"
                                ), once=True)
                                # Re-render the whole page
                                from playlist_arranger.main import set_page
                                ui.timer(0.2, lambda: set_page("spotify_source"), once=True)
                            except Exception as e:
                                ui.notify(f"Error loading tracks: {e}", type="negative")

                        ui.timer(0.05, lambda: load_tracks(pid, pn), once=True)


def _show_track_compact_table(tracks, pl_id, pl_name):
    """Show a compact track table when a playlist expansion is open."""
    container = ui.column().classes("w-full")

    with container:
        ui.label(f"Tracks: {pl_name}").classes("text-sm font-bold text-green-600 mb-1")
        ui.label(
            f"Total: {len(tracks)} | "
            f"In DB: {sum(1 for t in tracks if not _state.get_track_needs_analysis(t['id'], t.get('duration_ms')))}"
        ).classes("text-xs text-gray-500 mb-2")

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
            if reason:
                status = f"⚠ {reason}"
            else:
                status = "✓ In DB"
            rows.append({
                "idx": i,
                "name": t["name"][:42],
                "artist": t["artist"][:26],
                "duration": dur_str,
                "status": status,
            })

        ui.table(columns=columns, rows=rows, row_key="idx", pagination={"rowsPerPage": 25}).classes("w-full")

        # Action buttons
        missing = sum(
            1 for t in tracks
            if _state.get_track_needs_analysis(t["id"], t.get("duration_ms"))
        )
        with ui.row().classes("w-full gap-2 mt-2"):
            if missing > 0:
                from playlist_arranger.sources.spotify_source import get_playlist_tracks
                ui.button(
                    f"Analyze {missing} missing tracks",
                    on_click=lambda: _run_spotify_analysis(tracks),
                    color="yellow",
                ).classes("text-sm")

            if _backup_exists(pl_id):
                ui.button(
                    "Recover from backup",
                    on_click=lambda: _recover_from_backup(pl_id),
                    color="purple",
                ).classes("text-sm")


def _run_spotify_analysis(tracks):
    """Trigger Spotify analysis from the playlist source page."""
    pl_id = _state.current_playlist_id
    pl_name = _state.current_playlist_name

    def bg_task():
        try:
            from playlist_arranger.analysis.session import AnalysisSession
            to_analyze = [
                t for t in tracks
                if _state.get_track_needs_analysis(t["id"], t.get("duration_ms"))
            ]
            if not to_analyze:
                ui.timer(0.1, lambda: ui.notify("All tracks already in DB", type="positive"), once=True)
                return

            session = AnalysisSession(
                sp=_state.sp,
                tracks=to_analyze,
                playlist_name=pl_name,
                playlist_uri=f"spotify:playlist:{pl_id}",
                spotify_device_id=_state.spotify_device_id,
                progress_cb=lambda msg: ui.timer(0, lambda m=msg: ui.notify(m, type="info"), once=True),
            )
            session.run()
            ui.timer(0.1, lambda: ui.notify("Analysis complete!", type="positive"), once=True)
        except Exception as e:
            ui.timer(0.1, lambda: ui.notify(f"Analysis failed: {e}", type="negative"), once=True)

    threading.Thread(target=bg_task, daemon=True).start()


def _recover_from_backup(pl_id):
    """Recover from backup and navigate to anchors."""
    from playlist_arranger.cache.store import load_backup as _load_backup

    bk_data = _load_backup(pl_id)
    if not bk_data:
        ui.notify("No backup found", type="warning")
        return

    bk_tracks = bk_data.get("tracks", [])
    if not bk_tracks:
        ui.notify("Backup is empty", type="warning")
        return

    descs = []
    for t in bk_tracks:
        tid = t.get("id") or t.get("track_id", "")
        descs.append({
            "track_id": tid,
            "name": t.get("name", "?"),
            "artist": t.get("artist", "?"),
            "album": t.get("album", "?"),
            "description": "",
            "playlist": _state.current_playlist_name,
            "bpm": 0, "key": "", "camelot": "",
            "loudness_db": 0, "dynamic_range": 0,
            "harm_ratio": 0, "flatness": 0,
            "bass_pct": 0, "mid_pct": 0, "high_pct": 0,
            "onset_str": 0, "duration_ms": 0,
        })

    for d in descs:
        tid = d.get("track_id")
        if tid:
            entry = _db.get_track(tid)
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
    ui.notify(f"Recovered {len(descs)} tracks from backup", type="positive")
    from playlist_arranger.main import set_page
    ui.timer(0.2, lambda: set_page("anchors"), once=True)


def build_local_section(set_page_cb):
    """Build Local files source section. set_page_cb(page_name) for navigation."""
    from playlist_arranger.config import LOCAL_MUSIC_DIR

    container = ui.column().classes("w-full gap-4")

    with container:
        ui.label("Local Files Source").classes("text-2xl font-bold mb-2")

        current_dir_label = ui.label("").classes("text-sm text-gray-500")
        file_list = ui.column().classes("w-full")

        with ui.row().classes("w-full gap-2 items-end"):
            folder_input = ui.input(
                label="Folder path",
                value=str(LOCAL_MUSIC_DIR) if LOCAL_MUSIC_DIR else "",
            ).classes("w-full max-w-md flex-1")
            ui.button("Browse", on_click=lambda: scan_folder(folder_input.value)).classes("mb-3")

        def scan_folder(path):
            if not path:
                ui.notify("Please enter a folder path", type="warning")
                return
            folder = pathlib.Path(path).resolve()
            if not folder.exists():
                ui.notify("Folder not found", type="negative")
                return
            if not folder.is_dir():
                ui.notify("Not a directory", type="negative")
                return

            current_dir_label.set_text(f"Browsing: {folder}")
            file_list.clear()

            with file_list:
                if folder.parent != folder:
                    ui.button("📁 ..", on_click=lambda f=folder.parent: scan_folder(str(f))).classes("text-sm w-full text-left")

                dirs = sorted([d for d in folder.iterdir() if d.is_dir()])
                for d in dirs:
                    ui.button(f"📁 {d.name}", on_click=lambda f=d: scan_folder(str(f))).classes("text-sm w-full text-left")

                audio_exts = {".mp3", ".flac"}
                files = sorted([f for f in folder.iterdir() if f.suffix.lower() in audio_exts])
                if files:
                    ui.label(f"Files in this folder ({len(files)} audio files)").classes("text-sm font-bold mt-2 mb-1")
                    col_spec = [
                        {"name": "name", "label": "File", "field": "name"},
                        {"name": "size", "label": "Size", "field": "size"},
                    ]
                    rows = []
                    for f in files:
                        size_kb = f.stat().st_size / 1024
                        rows.append({"name": f.name[:50], "size": f"{size_kb:.0f} KB" if size_kb < 1024 else f"{size_kb/1024:.1f} MB"})
                    ui.table(columns=col_spec, rows=rows, row_key="name", pagination=100).classes("w-full")

                    ui.button(
                        f"🎵 Use this folder ({len(files)} tracks)",
                        on_click=lambda f=folder: load_local_folder(f),
                        color="green",
                    ).classes("mt-2")

        def load_local_folder(folder):
            from playlist_arranger.sources.local_source import scan_folder as _scan

            tracks = _scan(folder)
            if not tracks:
                ui.notify("No readable audio files found", type="warning")
                return

            from playlist_arranger.sources.local_source import make_playlist_id

            _state.current_playlist_id = make_playlist_id(folder)
            _state.current_playlist_name = folder.name
            _state.current_playlist_source = "local"
            _state.current_tracks[:] = tracks
            ui.notify(f"Loaded {len(tracks)} tracks from '{folder.name}'", type="positive")
            set_page_cb("local_source")

        if _state.current_tracks and _state.current_playlist_source == "local":
            _show_track_compact_table(_state.current_tracks, _state.current_playlist_id, _state.current_playlist_name)

    return container