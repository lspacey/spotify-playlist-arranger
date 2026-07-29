"""Description generator status row.

Extracted from ``playlist_source.py`` (2026-07-29 refactor — pattern #18).
Uses the same ``configure()`` dependency-injection pattern as
``analysis/batch_analyzer.py`` and ``ui/playlist_highlight.py``.
"""

import logging

from nicegui import ui

logger = logging.getLogger(__name__)

# ── Injected dependencies (wired by playlist_source.py at module init) ────────
_desc_gen = None   # playlist_arranger.analysis.desc_generator module ref


def configure(desc_gen_module):
    """Wire runtime dependencies from ``playlist_source.py`` at init time."""
    global _desc_gen
    _desc_gen = desc_gen_module


def render_desc_status():
    """Render the description generator status info (called once during build
    and re-rendered on each timer tick)."""
    # Start the desc generator worker (idempotent)
    _desc_gen.start_desc_generator()

    qsize = _desc_gen.desc_queue_size()
    qsize_label = f"{qsize} track{'s' if qsize != 1 else ''} in description queue"

    current_id = _desc_gen.desc_generator_current_track_id
    current_name = _desc_gen.desc_generator_current_track_name
    if current_id and current_name:
        processing_label = f"Currently processing: {current_name[:60]}"
    elif current_id:
        processing_label = f"Currently processing: {current_id[:12]}..."
    else:
        processing_label = "Idle — no tracks in queue" if qsize == 0 else "Idle — waiting for worker"

    def on_clear():
        _desc_gen.desc_queue_clear()
        ui.notify("Description queue cleared", type="positive")

    ui.button(
        "Stop and clean the queue",
        on_click=on_clear,
        color="orange",
    ).classes("text-xs").props("size=sm")

    def on_update_all():
        logger.debug("'Update all in background' clicked — not yet implemented")

    ui.button(
        "Update all in background",
        on_click=on_update_all,
        color="blue",
    ).classes("text-xs").props("size=sm")

    with ui.column().classes("gap-0"):
        ui.label(processing_label).classes("text-sm text-gray-600 dark:text-gray-400")
        ui.label(qsize_label).classes("text-xs text-gray-500")