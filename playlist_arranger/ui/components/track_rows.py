"""Shared track table row builder — used by anchors.py and playlist_source.py."""

from playlist_arranger.database import db as _db
from playlist_arranger.analysis.desc_status import get_desc_age_info
from playlist_arranger.ui import state as _state


def build_track_rows(tracks: list, anchored_ids: set | None = None) -> list[dict]:
    """Build row dicts for a playlist track table.

    Each row has: idx, name, artist, duration, status, locked, track_id,
    track_name_original, desc, desc_icon, desc_color, desc_caption.
    Fields are intentionally aligned with playlist_source.py's ``columns``
    definitions so both pages can share the same field names.

    anchored_ids: optional set of track IDs already anchored (adds
    "🔒 Anchored" status text and locked=True flag for CSS graying).
    """
    if anchored_ids is None:
        anchored_ids = set()

    rows = []
    for i, t in enumerate(tracks, 1):
        tid = t.get("id", "")
        is_anchored = tid in anchored_ids
        # Compute actual analysis status (same as playlist_source.py / Analysis page)
        if is_anchored:
            status = "🔒 Anchored"
        else:
            status = _state.get_track_status(t)
        # Look up desc status from DB (same as playlist_source.py)
        entry = _db.get_track(tid) if tid else None
        desc_info = get_desc_age_info(
            entry.get("desc_text") if entry else None,
            entry.get("desc_generated_at") if entry else None,
        )
        rows.append({
            "idx": i,
            "name": t.get("name", "?")[:42],
            "artist": t.get("artist", "?")[:40],
            "duration": _format_duration(t.get("duration_ms", 0)),
            "status": status,
            "locked": is_anchored,
            "track_id": tid,
            "track_name_original": t.get("name", ""),
            "desc": "✓" if desc_info.has_desc else "—",
            "desc_icon": "auto_stories" if desc_info.has_desc else "menu_book",
            "desc_color": desc_info.color,
            "desc_caption": desc_info.caption,
        })
    return rows


def _format_duration(dur_ms: int) -> str:
    """Format milliseconds to M:SS string."""
    if not dur_ms:
        return "?"
    return f"{dur_ms // 60000}:{(dur_ms // 1000) % 60:02d}"