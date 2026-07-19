"""Playlist highlight / auto-expand / notify utilities for currently-playing track.

Extracted from playlist_source.py — pure move, no behavior changes.
"""

import logging

from nicegui import ui
from playlist_arranger.ui import state as _state

logger = logging.getLogger(__name__)

# ─── Per-playlist table refs for native selection highlight ───────────────────

_playlist_tables = {}
_playlist_rows_cache = {}
_now_playing_row_keys = {}

# ─── Playlist expansion references for auto-expand (keyed by playlist_id) ─────

_playlist_expansions = {}

# ─── Set of playlist IDs that were auto-expanded (not manually by user) ───────

_auto_expanded_playlist_ids = set()

# ─── Client context + change detection for background-thread UI ops ───────────

_page_client = None
_last_notified_playlist_id = None

# ─── Dependency injection (circular-import avoidance) ─────────────────────────

_load_cached_playlist_tracks = None
_show_track_compact_table = None
_render_playlists_set_page_cb = None
_ui_context_lock = None


def configure(load_cached_playlist_tracks, show_track_compact_table,
              render_playlists_set_page_cb, ui_context_lock):
    """Wire the callables that live in playlist_source.py to avoid circular imports.

    Called once at module init time from playlist_source.py.
    """
    global _load_cached_playlist_tracks, _show_track_compact_table
    global _render_playlists_set_page_cb, _ui_context_lock
    _load_cached_playlist_tracks = load_cached_playlist_tracks
    _show_track_compact_table = show_track_compact_table
    _render_playlists_set_page_cb = render_playlists_set_page_cb
    _ui_context_lock = ui_context_lock


# ─── Row highlight sync ──────────────────────────────────────────────────────

def _sync_row_highlight(table, rows_cache: list, tracks: list, tid: str,
                        key_store: dict, store_key: str):
    """Generic: highlight the row matching track `tid` by scanning `tracks` for
    the index of `id`==`tid`, then picking the corresponding row from
    `rows_cache` and setting `table.selected`.  Removes any previous highlight
    tracked in `key_store[store_key]` first.

    Works for both playlist tables (where `tracks` = _state.current_tracks)
    and the queue table (where `tracks` = _state.analysis_queue).
    """
    if not tid or table is None or not rows_cache or not tracks:
        return
    track_index = next((i for i, t in enumerate(tracks) if t.get("id") == tid), None)
    if track_index is None or track_index >= len(rows_cache):
        return
    match_row = rows_cache[track_index]
    prev_key = key_store.get(store_key)
    current = list(table.selected) if hasattr(table, 'selected') else []
    if prev_key is not None and prev_key != match_row["idx"]:
        current = [r for r in current if r.get("idx") != prev_key]
    if not any(r.get("idx") == match_row["idx"] for r in current):
        current.append(match_row)
        table.selected = current
        key_store[store_key] = match_row["idx"]


def _sync_now_playing_row_highlight(plid: str, tid: str):
    """Sync the table.selected to highlight the row for track_id in playlist_id.
    Removes any previous now-playing highlight and adds the current one.
    Extracted from _notify_playing_track() and _update_np_ui() (DRY)."""
    if not plid or not tid:
        return
    table = _playlist_tables.get(plid)
    rows_cache = _playlist_rows_cache.get(plid, [])
    _sync_row_highlight(table, rows_cache, _state.current_tracks, tid,
                        _now_playing_row_keys, plid)


# ─── Auto-expand / collapse ──────────────────────────────────────────────────

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


# ─── Notify playing track (background-thread safe) ───────────────────────────

def _notify_playing_track(playlist_id: str, track_id: str):
    """Highlight and auto-expand from background thread via captured client context."""
    global _page_client, _last_notified_playlist_id
    plid = playlist_id or ""
    tid = track_id or ""

    if _page_client is None:
        return

    playlist_changed = plid and plid != _last_notified_playlist_id
    logger.debug(
        "notify gate: playlist_changed=%s last_notified=%s",
        playlist_changed,
        _last_notified_playlist_id[:8] if _last_notified_playlist_id else _last_notified_playlist_id,
    )

    try:
        with _ui_context_lock, _page_client:
            if playlist_changed:
                if _last_notified_playlist_id:
                    _collapse_playlist(_last_notified_playlist_id)
                _last_notified_playlist_id = plid

            has_tracks = bool(_state.current_tracks)
            plid_matches_current = plid == _state.current_playlist_id
            gate_open = plid and tid and has_tracks and plid_matches_current
            if gate_open:
                _sync_now_playing_row_highlight(plid, tid)

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


__all__ = [
    "_playlist_tables",
    "_playlist_rows_cache",
    "_now_playing_row_keys",
    "_playlist_expansions",
    "_auto_expanded_playlist_ids",
    "_sync_now_playing_row_highlight",
    "_sync_row_highlight",
    "_notify_playing_track",
    "_auto_expand_playlist",
    "_collapse_playlist",
    "_page_client",
    "_last_notified_playlist_id",
    "configure",
    "_ui_context_lock",
]
