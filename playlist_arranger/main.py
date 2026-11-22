#!/usr/bin/env python3
"""
Playlist Arranger — NiceGUI web application entry point.
Refactored from playlist_analyzer.py into a modular package.
"""

import os
import sys
import threading
import logging

from playlist_arranger.config import setup_logging
from playlist_arranger.config import sync_weights_from_settings

setup_logging()
logger = logging.getLogger(__name__)

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

from nicegui import ui

from playlist_arranger.ui import state as _state
from playlist_arranger.ui.pages.welcome import build_welcome
from playlist_arranger.ui.pages.playlist_source import (
    build_spotify_section,
    build_local_section,
)
# Load persisted analysis queue on startup
_state.load_analysis_queue()
logger.info("Analysis queue loaded: %d track(s)", len(_state.analysis_queue))

from playlist_arranger.ui.pages.anchors import build_anchors
from playlist_arranger.ui.pages.smart_sorting import build_smart_sorting
from playlist_arranger.ui.pages.database_panel import build_database_dialog
from playlist_arranger.ui.pages.settings_panel import build_settings_dialog
from playlist_arranger.ui.components.progress import ProgressPanel
from playlist_arranger.sources.spotify_source import is_configured as _spotify_configured


# ─── Navigation state ─────────────────────────────────────────────────────────
_current_page = "welcome"  # welcome | spotify_source | local_source | anchors | sorting
_right_panel = None

# Nav button references (set at build time, updated reactively via _refresh_nav_buttons)
_anchor_btn = None
_sort_btn = None


def _refresh_nav_buttons() -> None:
    """Re-evaluate nav button enabled states based on current application state."""
    global _anchor_btn, _sort_btn
    if _anchor_btn is not None:
        _anchor_btn.set_enabled(_state.sp is not None)
    if _sort_btn is not None:
        _sort_btn.set_enabled(_state.sp is not None)


def set_page(page_name: str) -> None:
    """Switch the right panel to a different page."""
    global _current_page, _right_panel
    _current_page = page_name
    _refresh_nav_buttons()
    render_right_panel()


def render_right_panel() -> None:
    """Render the current page content in the right panel."""
    global _right_panel
    _refresh_nav_buttons()  # keep Anchors/Sorting enabled-state in sync
                            # with _state.sp on every panel refresh —
                            # do_connect()'s deferred rebuild calls this
                            # function directly, bypassing set_page(),
                            # so nav buttons must be refreshed here too.
    logger.debug(
        "DIAG [render_right_panel] container id=%s, page=%s",
        id(_right_panel) if _right_panel is not None else None, _current_page,
    )
    try:
        if _right_panel:
            _right_panel.clear()
    except RuntimeError:
        # Client disconnected or element stale — recreate later
        logger.warning("Right panel client stale, will recreate on next render")
        return

    if _right_panel is None:
        logger.error(
            "render_right_panel() called but _right_panel is None — this "
            "usually means playlist_arranger.main was imported as a SECOND, "
            "independent module object (e.g. because the app was launched "
            "via 'python -m playlist_arranger.main' instead of "
            "'python -m playlist_arranger'). Check sys.modules for duplicate "
            "entries of this module."
        )
        return

    with _right_panel:
        if _current_page == "welcome":
            build_welcome()
        elif _current_page == "spotify_source":
            build_spotify_section(set_page)
        elif _current_page == "local_source":
            build_local_section(set_page)
        elif _current_page == "anchors":
            build_anchors()
        elif _current_page == "sorting":
            build_smart_sorting()


async def run_descriptions() -> None:
    """Generate track descriptions (temporarily stubbed — old pipeline removed).

    TODO(anchors-refactor): wire this to desc_queue_add_many() +
    populate current_descs from DB — old descriptions.py removed 2026-07-21.
    """
    ui.notify(
        "Description generation is being upgraded — "
        "please use the per-track desc icon for now",
        type="warning",
    )
    logger.info(
        "run_descriptions() called but generation pipeline is temporarily stubbed "
        "(old descriptions.py module removed 2026-07-21)"
    )


async def run_analysis() -> None:
    """Run analysis session for missing Spotify tracks (async, logs to logger)."""
    import asyncio
    progress = ProgressPanel("Analyzing Tracks...")
    with _right_panel:
        progress.render()
    try:
        from playlist_arranger.database import db as _db
        from playlist_arranger.analysis.session import AnalysisSession

        to_analyze = [
            t for t in _state.current_tracks
            if _state.get_track_needs_analysis(t["id"], t.get("duration_ms"))
        ]
        if not to_analyze:
            progress.done("All tracks already in DB")
            return

        session = AnalysisSession(
            sp=_state.sp,
            tracks=to_analyze,
            playlist_name=_state.current_playlist_name,
            playlist_uri=f"spotify:playlist:{_state.current_playlist_id}",
            spotify_device_id=_state.spotify_device_id,
            progress_cb=lambda msg: logger.info(msg),
            on_track_done=lambda tid: logger.info("Saved: %s", tid),
        )
        await asyncio.to_thread(session.run)
        progress.done("Analysis complete!")

        still = sum(
            1 for t in _state.current_tracks
            if _state.get_track_needs_analysis(t["id"], t.get("duration_ms"))
        )
        if still == 0:
            logger.info("All %d tracks now in DB", len(_state.current_tracks))
        else:
            logger.info("%d track(s) still missing", still)
    except Exception as e:
        progress.error(str(e))
        logger.exception("Analysis failed")


async def run_analysis_local() -> None:
    """Fast direct analysis for local files — reads audio from disk, no playback needed."""
    import asyncio
    progress = ProgressPanel("Analyzing Local Tracks...")
    with _right_panel:
        progress.render()
    try:
        from playlist_arranger.analysis.worker import save_track_worker
        import pathlib
        import numpy as np

        try:
            import librosa
        except ImportError:
            progress.error("librosa not installed")
            return

        to_analyze = [
            t for t in _state.current_tracks
            if _state.get_track_needs_analysis(t["id"], t.get("duration_ms"))
        ]
        if not to_analyze:
            progress.done("All tracks already in DB")
            return

        logger.info("Fast-analyzing %d local file(s) from disk...", len(to_analyze))

        for idx, track in enumerate(to_analyze, 1):
            logger.info("[%d/%d] %s — %s", idx, len(to_analyze), track['name'], track['artist'])
            fp = pathlib.Path(track.get("file_path", ""))
            if not fp.exists():
                logger.info("File not found — skipping")
                continue

            try:
                # Load audio directly from disk (fast path — no playback/capture)
                y, sr = librosa.load(str(fp), sr=None, mono=True)
                logger.info("Loaded %s (%.1fs at %d Hz)", fp.name, len(y)/sr, sr)
            except Exception as exc:
                logger.warning("Could not read audio file: %s (%s)", fp.name, exc)
                continue

            save_track_worker(
                track_info=track,
                playlist_name=_state.current_playlist_name,
                playlist_uri="",
                y_full=y,
                status_cb=lambda msg: logger.info(msg),
            )
            logger.info("Done")

        progress.done("Analysis complete!")
    except Exception as e:
        progress.error(str(e))
        logger.exception("Local analysis failed")

# ─── Main UI layout ───────────────────────────────────────────────────────────
@ui.page("/")
def main_page():
    """Main page layout with sidebar navigation."""
    # Load settings
    s = _state.get_settings()
    # Sync sorting weights at startup so config.WEIGHTS reflects settings.json
    sync_weights_from_settings(s)

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

    # ── Connection lifecycle monitoring ────────────────────────────────────
    client = ui.context.client
    client.on_disconnect(
        lambda c=client: logging.getLogger("playlist_arranger.main").debug(
            "DIAG [disconnect] client=%s page=%s",
            c.id, _current_page,
        )
    )

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

            # Anchors (disabled until Spotify is connected)
            global _anchor_btn
            _anchor_btn = ui.button("Anchors", on_click=lambda: set_page("anchors"))
            _anchor_btn.classes("w-full text-sm")
            if _state.sp is None:
                _anchor_btn.set_enabled(False)

            # Smart Sorting (disabled until Spotify is connected, same as Anchors)
            global _sort_btn
            _sort_btn = ui.button("Sorting", on_click=lambda: set_page("sorting"))
            _sort_btn.classes("w-full text-sm")
            if _state.sp is None:
                _sort_btn.set_enabled(False)

            ui.separator().classes("my-2")

            # Database button -> opens dialog
            async def open_database():
                with ui.dialog() as dialog, ui.card().style("width: 1100px; max-width: 95vw; max-height: 90vh; overflow-y: auto"):
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
            # On reconnect, restore the last active page instead of flashing Welcome.
            # _current_page persists across client reconnects because it's a
            # module-level global, not per-client state.
            if _current_page == "welcome":
                build_welcome()
            elif _current_page == "spotify_source":
                build_spotify_section(set_page)
            elif _current_page == "local_source":
                build_local_section(set_page)
            elif _current_page == "anchors":
                build_anchors()
            elif _current_page == "sorting":
                build_smart_sorting()
            else:
                build_welcome()  # unknown page — safe default


# ─── Entry point ──────────────────────────────────────────────────────────────
def main():
    """Launch the Playlist Arranger web app."""
    logger.info("Starting Playlist Arranger on http://0.0.0.0:8082")

    # Preload MERT model (non-blocking, loads in background)
    def _preload_mert():
        try:
            from playlist_arranger.audio.mert import load_mert, HAS_MERT
            if HAS_MERT:
                logger.info("Preloading MERT model...")
                load_mert(progress_cb=lambda msg: logger.info(msg))
                logger.info("MERT model ready")
        except Exception:
            logger.exception("MERT preload failed")

    threading.Thread(target=_preload_mert, daemon=True).start()

    try:
        ui.run(
            title="Playlist Arranger",
            host="127.0.0.1",
            port=8082,
            reload=False,
            show=True,
            reconnect_timeout=75.0,
        )
    except KeyboardInterrupt:
        logger.info("Shutdown requested (Ctrl-C) — exiting cleanly.")
    else:
        logger.info("Playlist Arranger stopped")


# WARNING: direct execution as __main__ (e.g. `python -m playlist_arranger.main`)
# creates a SEPARATE module object from the one other code imports via
# `from playlist_arranger.main import ...`.  This causes the imported copy
# to have its own _right_panel=None and stale globals, breaking page rebuilds.
# Always use `python -m playlist_arranger` (via __main__.py) as the launch
# command; this guard is kept only for development convenience.
if __name__ == "__main__":
    main()
