#!/usr/bin/env python3
"""
Playlist Arranger — NiceGUI web application entry point.
Refactored from playlist_analyzer.py into a modular package.
"""

import os
import sys
import threading
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

from nicegui import ui

from playlist_arranger.ui import state as _state
from playlist_arranger.ui.pages.welcome import build_welcome
from playlist_arranger.ui.pages.playlist_source import (
    build_spotify_section,
    build_local_section,
)
from playlist_arranger.ui.pages.anchor_editor import build_anchor_editor
from playlist_arranger.ui.pages.smart_sorting import build_smart_sorting
from playlist_arranger.ui.pages.database_panel import build_database_dialog
from playlist_arranger.ui.pages.settings_panel import build_settings_dialog
from playlist_arranger.ui.components.progress import ProgressPanel
from playlist_arranger.sources.spotify_source import is_configured as _spotify_configured


# ─── Navigation state ─────────────────────────────────────────────────────────
_current_page = "welcome"  # welcome | spotify_source | local_source | anchors | sorting
_right_panel = None


def set_page(page_name: str) -> None:
    """Switch the right panel to a different page."""
    global _current_page, _right_panel
    _current_page = page_name
    render_right_panel()


def render_right_panel() -> None:
    """Render the current page content in the right panel."""
    global _right_panel
    if _right_panel:
        _right_panel.clear()
    else:
        return

    with _right_panel:
        if _current_page == "welcome":
            build_welcome()
        elif _current_page == "spotify_source":
            build_spotify_section(set_page)
        elif _current_page == "local_source":
            build_local_section(set_page)
        elif _current_page == "anchors":
            if _state.has_descriptions():
                build_anchor_editor()
            else:
                ui.label("No descriptions available. Generate descriptions first.").classes("text-yellow-500")
                ui.button("Generate Descriptions", on_click=run_descriptions).classes("mt-2")
        elif _current_page == "sorting":
            if _state.has_anchor_plan():
                build_smart_sorting()
            else:
                ui.label("No anchor plan. Create anchors first.").classes("text-yellow-500")


def run_descriptions() -> None:
    """Generate track descriptions in background thread."""
    progress = ProgressPanel("Generating Descriptions...")
    with _right_panel:
        progress.render()

    def bg_task():
        try:
            from playlist_arranger.llm.descriptions import generate_track_descriptions
            descs = generate_track_descriptions(
                _state.current_tracks,
                _state.current_playlist_name,
                _state.current_playlist_id,
                progress_cb=lambda msg: ui.timer(0, lambda m=msg: progress.log(m), once=True),
            )
            _state.current_descs[:] = descs
            ui.timer(0.1, lambda: progress.done(f"Generated {len(descs)} descriptions"), once=True)
            ui.timer(0.2, lambda: set_page("anchors"), once=True)
        except Exception as e:
            ui.timer(0.1, lambda: progress.error(str(e)), once=True)
            logger.exception("Description generation failed")

    threading.Thread(target=bg_task, daemon=True).start()


def run_analysis() -> None:
    """Run analysis session for missing Spotify tracks."""
    progress = ProgressPanel("Analyzing Tracks...")
    with _right_panel:
        progress.render()

    def _log(msg):
        ui.timer(0, lambda m=msg: progress.log(m), once=True)

    def _done(msg="Analysis complete!"):
        ui.timer(0.1, lambda m=msg: progress.done(m), once=True)

    def _error(msg):
        ui.timer(0.1, lambda m=msg: progress.error(m), once=True)

    def bg_task():
        try:
            from playlist_arranger.database import db as _db
            from playlist_arranger.analysis.session import AnalysisSession

            to_analyze = [
                t for t in _state.current_tracks
                if _state.get_track_needs_analysis(t["id"], t.get("duration_ms"))
            ]
            if not to_analyze:
                _done("All tracks already in DB")
                return

            session = AnalysisSession(
                sp=_state.sp,
                tracks=to_analyze,
                playlist_name=_state.current_playlist_name,
                playlist_uri=f"spotify:playlist:{_state.current_playlist_id}",
                spotify_device_id=_state.spotify_device_id,
                progress_cb=_log,
                on_track_done=lambda tid: _log(f"Saved: {tid}"),
            )
            session.run()
            _done("Analysis complete!")

            # Check results
            still = sum(
                1 for t in _state.current_tracks
                if _state.get_track_needs_analysis(t["id"], t.get("duration_ms"))
            )
            if still == 0:
                _log(f"All {len(_state.current_tracks)} tracks now in DB!")
            else:
                _log(f"{still} track(s) still missing.")
        except Exception as e:
            _error(str(e))
            logger.exception("Analysis failed")

    threading.Thread(target=bg_task, daemon=True).start()


def run_analysis_local() -> None:
    """Run analysis for local files (play + capture)."""
    progress = ProgressPanel("Analyzing Local Tracks...")
    with _right_panel:
        progress.render()

    def _log(msg):
        ui.timer(0, lambda m=msg: progress.log(m), once=True)

    def _done(msg="Analysis complete!"):
        ui.timer(0.1, lambda m=msg: progress.done(m), once=True)

    def _error(msg):
        ui.timer(0.1, lambda m=msg: progress.error(m), once=True)

    def bg_task():
        try:
            from playlist_arranger.database import db as _db
            from playlist_arranger.sources.local_source import play_local_file
            from playlist_arranger.audio.capture import (
                start_audio_capture,
                stop_capture,
            )
            from playlist_arranger.analysis.worker import save_track_worker
            import pathlib

            to_analyze = [
                t for t in _state.current_tracks
                if _state.get_track_needs_analysis(t["id"], t.get("duration_ms"))
            ]
            if not to_analyze:
                _done("All tracks already in DB")
                return

            # Start audio capture
            s = _state.get_settings()
            pa, stream, dev_name = start_audio_capture(
                s.selected_audio_device_index
            )
            if stream is None:
                _error("Audio capture unavailable")
                return
            _log(f"Audio: {dev_name}")

            total = len(to_analyze)
            for idx, track in enumerate(to_analyze, 1):
                _log(f"[{idx}/{total}] {track['name']} — {track['artist']}")

                # Play file
                fp = pathlib.Path(track.get("file_path", ""))
                if fp.exists():
                    play_local_file(fp)
                else:
                    _log("File not found — skipping")
                    continue

                # Wait for track duration
                dur_s = track["duration_ms"] / 1000
                import time
                time.sleep(dur_s + 1.0)

                # Save
                from playlist_arranger.audio.features import _to_mono
                import numpy as np
                from playlist_arranger.audio.capture import actual_sr, audio_deque, audio_lock

                with audio_lock:
                    y_live = _to_mono(np.array(audio_deque, dtype=np.float32), actual_sr)
                y_final = y_live if len(y_live) > actual_sr * 2 else None

                if y_final is not None:
                    save_track_worker(
                        track_info=track,
                        playlist_name=_state.current_playlist_name,
                        playlist_uri="",
                        y_full=y_final,
                        status_cb=_log,
                    )
                    _log("Done")
                else:
                    _log("Not enough audio — skipping")

            stop_capture(pa, stream)
            _done("Analysis complete!")
        except Exception as e:
            _error(str(e))
            logger.exception("Local analysis failed")

    threading.Thread(target=bg_task, daemon=True).start()


def recover_backup() -> None:
    """Recover from backup — load descriptions and go to anchors."""
    from playlist_arranger.cache.store import load_backup as _load_backup
    from playlist_arranger.database import db as _db

    bk_data = _load_backup(_state.current_playlist_id)
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

    # Enrich from DB
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
    set_page("anchors")


# ─── Main UI layout ───────────────────────────────────────────────────────────
@ui.page("/")
def main_page():
    """Main page layout with sidebar navigation."""
    # Load settings
    s = _state.get_settings()

    # Theme toggle in header
    with ui.header(elevated=True).classes("bg-primary text-white"):
        with ui.row().classes("w-full items-center justify-between px-4"):
            ui.label("🎧 Playlist Arranger").classes("text-xl font-bold")
            with ui.row().classes("gap-2"):
                dark = ui.dark_mode()
                ui.button(
                    icon="dark_mode" if dark.value else "light_mode",
                    on_click=lambda: dark.set_value(not dark.value),
                ).props("flat color=white")

    with ui.row().classes("w-full h-[calc(100vh-64px)]"):
        # ─── Left sidebar ───────────────────────────────────────────────────
        with ui.column().classes("w-48 bg-gray-100 dark:bg-gray-900 p-4 gap-2 h-full"):
            ui.label("Navigation").classes("text-sm font-bold text-gray-500 mb-2")

            # Source buttons
            spotify_available = _spotify_configured()

            spotify_btn = ui.button("Spotify", on_click=lambda: set_page("spotify_source"))
            spotify_btn.classes("w-full text-sm")
            if not spotify_available:
                spotify_btn.set_enabled(False)

            ui.button("Local Files", on_click=lambda: set_page("local_source")) \
                .classes("w-full text-sm")

            # Anchor Selection (disabled until playlist selected)
            anchor_btn = ui.button("Anchors", on_click=lambda: set_page("anchors"))
            anchor_btn.classes("w-full text-sm")
            if not _state.has_playlist():
                anchor_btn.set_enabled(False)

            # Smart Sorting (disabled until anchor plan non-empty)
            sort_btn = ui.button("Sorting", on_click=lambda: set_page("sorting"))
            sort_btn.classes("w-full text-sm")
            if not _state.has_anchor_plan():
                sort_btn.set_enabled(False)

            ui.separator().classes("my-2")

            # Database button -> opens dialog
            async def open_database():
                with ui.dialog() as dialog, ui.card().classes("w-[800px] max-h-[90vh] overflow-y-auto"):
                    build_database_dialog()
                dialog.open()

            ui.button("Database", on_click=open_database).classes("w-full text-sm")

            # Settings button -> opens dialog
            async def open_settings():
                with ui.dialog() as dialog, ui.card().classes("w-[800px] max-h-[90vh] overflow-y-auto"):
                    build_settings_dialog()
                dialog.open()

            ui.button("Settings", on_click=open_settings).classes("w-full text-sm")

        # ─── Right panel ────────────────────────────────────────────────────
        global _right_panel
        with ui.column().classes("flex-1 p-6 overflow-y-auto h-full") as _right_panel:
            build_welcome()


# ─── Entry point ──────────────────────────────────────────────────────────────
def main():
    """Launch the Playlist Arranger web app."""
    logger.info("Starting Playlist Arranger on http://0.0.0.0:8082")
    ui.run(
        title="Playlist Arranger",
        host="0.0.0.0",
        port=8082,
        reload=False,
        show=True,
    )
    logger.info("Playlist Arranger stopped")


if __name__ == "__main__":
    main()