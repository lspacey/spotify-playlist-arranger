"""Local Files section renderer.

Extracted from ``playlist_source.py`` (2026-07-29 refactor — pattern #18).
Uses the same ``configure()`` dependency-injection pattern as
``analysis/batch_analyzer.py`` and ``ui/playlist_highlight.py``.

TODO(step-3): ``_show_track_compact_table`` will move from
``playlist_source.py`` into ``playlist_arranger/ui/track_table.py`` —
update the injected dependency and the bare-import bridge when that
lands, per the playlist_source.py refactor plan.
"""

import pathlib
import logging

from nicegui import ui

logger = logging.getLogger(__name__)

# ── Injected dependencies (wired by playlist_source.py at module init) ────────
_state = None                     # playlist_arranger.ui.state module ref
_show_track_compact_table = None  # function ref (will be track_table in step-3)


def configure(state_module, show_track_compact_table_fn):
    """Wire runtime dependencies from ``playlist_source.py`` at init time."""
    global _state, _show_track_compact_table
    _state = state_module
    _show_track_compact_table = show_track_compact_table_fn


def build_local_section(set_page_cb):
    """Render the Local Files browser UI (folder scanning, track table)."""
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