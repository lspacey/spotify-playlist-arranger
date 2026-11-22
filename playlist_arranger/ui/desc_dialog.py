"""Description viewer dialog — shared by playlist + queue desc icons."""

import logging

from nicegui import ui

from playlist_arranger.database import db as _db
from playlist_arranger.analysis.desc_generator import desc_queue_add

logger = logging.getLogger(__name__)

# ── Live-update state: which track's dialog is open, plus its textarea ─────
# These are read by _on_desc_generated in playlist_source.py so the worker
# thread can push newly-generated description text into an open dialog.

_current_open_track_id: str | None = None   # track_id of the currently-open dialog
_current_textarea = None                     # ui.textarea element (or None if closed)
_current_generated_label = None              # "Generated: ..." ui.label (or None if closed)


def _close_dialog_state():
    """Reset live-update state when the dialog closes (any method)."""
    global _current_open_track_id, _current_textarea, _current_generated_label
    _current_open_track_id = None
    _current_textarea = None
    _current_generated_label = None


def show_desc_dialog(track_id: str, track_name: str, artist: str = ""):
    """Open a dialog showing the description for a specific track.

    Looks up current desc_text/desc_generated_at from the DB at dialog-open
    time (never from stale row data captured at render time).  Artist is
    taken from the caller's row data (reliable for both playlist and queue
    tables), falling back to DB if not provided.

    Args:
        track_id: Spotify track ID
        track_name: Track display name (used for dialog title)
        artist: Artist name from the table row (caller-provided, optional)
    """
    global _current_open_track_id, _current_textarea, _current_generated_label

    logger.debug("show_desc_dialog called for track_id=%s, name=%s",
                 track_id[:8] if track_id else "?", track_name[:40] if track_name else "?")

    # ── Track which dialog is open for live-update by background worker ────
    _current_open_track_id = track_id

    entry = _db.get_track(track_id) if track_id else None
    desc_text = entry.get("desc_text") if isinstance(entry, dict) else None
    desc_generated_at = entry.get("desc_generated_at") if isinstance(entry, dict) else None

    # Artist: prefer caller-provided (from row data), fall back to DB, then empty
    if not artist:
        artist = entry.get("artist", "") if isinstance(entry, dict) else ""

    # Build dialog title: "Artist - Track" or just "Track" if no artist
    if artist and track_name:
        title = f"{artist[:40]} - {track_name[:60]}"
    elif track_name:
        title = track_name[:80]
    elif artist:
        title = artist[:80]
    else:
        title = "Unknown Track"

    # Format the human-readable generation date
    if desc_generated_at and isinstance(desc_generated_at, str) and desc_generated_at.strip():
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(desc_generated_at)
            # If naive, treat as local; if aware, convert to local for display
            if dt.tzinfo is None:
                generated_str = dt.strftime("%Y-%m-%d %H:%M")
            else:
                generated_str = dt.astimezone().strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            generated_str = desc_generated_at
    else:
        generated_str = "Never generated"

    text_content = desc_text if (isinstance(desc_text, str) and desc_text.strip()) else "No description yet"

    with ui.dialog(value=True) as dialog, ui.card().classes("w-[520px] max-w-[95vw]"):
        dialog.on("update:model-value", lambda e: _close_dialog_state() if not e.args else None)
        ui.label(title).classes("text-lg font-semibold mb-3")

        # Scrollable read-only text area (1.5x taller than original 180/360px)
        with ui.column().classes("w-full"):
            _current_textarea = ui.textarea(
                value=text_content,
            ).classes("w-full").props(
                "readonly outlined input-style=min-height:270px;max-height:540px"
            )

        _current_generated_label = ui.label(f"Generated: {generated_str}").classes("text-xs text-gray-500 mt-3")

        def on_generate():
            added = desc_queue_add(track_id)
            if added:
                ui.notify(f"Added '{track_name[:40]}' to description queue", type="positive")
            else:
                ui.notify(f"'{track_name[:40]}' is already in the description queue", type="info")

        ui.button("Generate new description", on_click=on_generate, color="blue").classes("mt-2 text-sm")
        ui.button("Close", on_click=dialog.close, color="gray").classes("mt-2 text-sm")

    return dialog
