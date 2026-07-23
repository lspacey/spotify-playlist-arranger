"""Anchors page — playlist dropdown, anchor plan editor, track list."""

import json
import logging

from nicegui import ui

from playlist_arranger.ui import state as _state
from playlist_arranger.sources.spotify_source import get_own_playlists
from playlist_arranger.sorting.anchors import _load_anchors_file, _save_anchors_file
from playlist_arranger.ui.components.track_rows import build_track_rows

logger = logging.getLogger(__name__)

# ── Module-level button refs & selection state ──────────────────────────────
_controls_row = None
_move_up_btn = None
_move_down_btn = None
_remove_btn = None
_add_placeholder_btn = None
_add_selected_track_btn = None
_clear_btn = None
_save_btn = None
_anchors_select = None
_anchors_list_container = None
_track_list_container = None

# ── SINGLE SOURCE OF TRUTH for current anchor plan ─────────────────────────
_anchor_plan: list = []

# Selected rows (tracked in Python + native Quasar highlight via .selected)
_selected_anchor_idx: int | None = None
_selected_track_idx: int | None = None

# Table refs (for programmatic .selected row highlighting)
_anchors_table = None
_track_table = None

# Cached playlist data
_playlist_tracks: list = []
_playlist_name: str = ""


def _anchored_track_ids() -> set:
    """Return the set of track IDs currently in the anchor plan."""
    return {e["track_id"] for e in _anchor_plan if e["type"] == "anchor"}


def _parse_row_idx(e) -> int | None:
    """Extract 0-based row index from NiceGUI 3.x row click event.

    e.args is a LIST: e.args[0] = JS event object, e.args[1] = row data dict.
    Returns 0-based index, or None on failure.
    """
    row = e.args[1] if isinstance(e.args, list) and len(e.args) >= 2 else {}
    idx = row.get("idx", 0)
    if isinstance(idx, (int, float)) and idx > 0:
        return int(idx) - 1
    return None


def _refresh_control_buttons():
    """Re-evaluate enable/disable state for all control buttons.
    Reads directly from _anchor_plan and _selected_* globals (single source of truth)."""
    global _move_up_btn, _move_down_btn, _remove_btn
    global _add_selected_track_btn, _clear_btn, _save_btn

    n = len(_anchor_plan)
    sel = _selected_anchor_idx
    single_selected = sel is not None and 0 <= sel < n

    # Move Up: enabled iff single selected AND index > 0 (NOT first)
    if _move_up_btn is not None:
        _move_up_btn.set_enabled(single_selected and sel > 0)

    # Move Down: enabled iff single selected AND index < n-1 (NOT last)
    if _move_down_btn is not None:
        _move_down_btn.set_enabled(single_selected and sel < n - 1)

    # Remove: enabled iff single selected
    if _remove_btn is not None:
        _remove_btn.set_enabled(single_selected)

    # Add selected track: enabled iff one track selected AND it's not already anchored
    if _add_selected_track_btn is not None:
        track_enabled = False
        if _selected_track_idx is not None and 0 <= _selected_track_idx < len(_playlist_tracks):
            tid = _playlist_tracks[_selected_track_idx].get("id", "")
            track_enabled = tid not in _anchored_track_ids()
        _add_selected_track_btn.set_enabled(track_enabled)

    # Clear: always enabled if list has items
    if _clear_btn is not None:
        _clear_btn.set_enabled(n > 0)

    # Save: always enabled (no condition needed)


def _rebuild_anchors_list():
    """Clear and re-render the anchors list table."""
    global _anchors_list_container
    if _anchors_list_container is None:
        return
    _anchors_list_container.clear()
    with _anchors_list_container:
        _render_anchors_list()


def _rebuild_track_list():
    """Clear and re-render the playlist tracks table."""
    global _track_list_container
    if _track_list_container is None:
        return
    _track_list_container.clear()
    with _track_list_container:
        _render_track_list()


# ── Event handlers (NO Quasar selection mode — all selection tracked in Python) ─

def _on_anchor_row_click(e):
    """Handle single-selection on anchors table + native Quasar highlight."""
    global _selected_anchor_idx, _anchors_table
    idx = _parse_row_idx(e)
    row_data = e.args[1] if isinstance(e.args, list) and len(e.args) >= 2 else None

    if idx is not None and 0 <= idx < len(_anchor_plan):
        _selected_anchor_idx = idx
        if _anchors_table is not None and row_data is not None:
            _anchors_table.selected = [row_data]
    else:
        _selected_anchor_idx = None
        if _anchors_table is not None:
            _anchors_table.selected = []
    _refresh_control_buttons()


def _on_track_row_click(e):
    """Handle single-selection on playlist tracks table + native Quasar highlight."""
    global _selected_track_idx, _track_table
    idx = _parse_row_idx(e)
    row_data = e.args[1] if isinstance(e.args, list) and len(e.args) >= 2 else None

    if idx is not None and 0 <= idx < len(_playlist_tracks):
        tid = _playlist_tracks[idx].get("id", "")
        if tid and tid not in _anchored_track_ids():
            _selected_track_idx = idx
            if _track_table is not None and row_data is not None:
                _track_table.selected = [row_data]
        else:
            _selected_track_idx = None
            if _track_table is not None:
                _track_table.selected = []
    else:
        _selected_track_idx = None
        if _track_table is not None:
            _track_table.selected = []
    _refresh_control_buttons()


def _on_track_double_click(e):
    """Double-click on playlist track → select + add to anchors + native Quasar highlight."""
    global _selected_track_idx, _track_table
    idx = _parse_row_idx(e)
    row_data = e.args[1] if isinstance(e.args, list) and len(e.args) >= 2 else None

    if idx is not None and 0 <= idx < len(_playlist_tracks):
        tid = _playlist_tracks[idx].get("id", "")
        if tid and tid not in _anchored_track_ids():
            _selected_track_idx = idx
            if _track_table is not None and row_data is not None:
                _track_table.selected = [row_data]
            _add_selected_track()
    _refresh_control_buttons()


# ── Anchor plan mutation methods (ALL operate on the SAME _anchor_plan list) ─

def _move_up():
    global _anchor_plan, _selected_anchor_idx
    if _selected_anchor_idx is None or _selected_anchor_idx <= 0:
        return
    idx = _selected_anchor_idx
    _anchor_plan[idx - 1], _anchor_plan[idx] = _anchor_plan[idx], _anchor_plan[idx - 1]
    _selected_anchor_idx = idx - 1
    _rebuild_anchors_list()
    _rebuild_track_list()
    _refresh_control_buttons()


def _move_down():
    global _anchor_plan, _selected_anchor_idx
    if _selected_anchor_idx is None or _selected_anchor_idx >= len(_anchor_plan) - 1:
        return
    idx = _selected_anchor_idx
    _anchor_plan[idx], _anchor_plan[idx + 1] = _anchor_plan[idx + 1], _anchor_plan[idx]
    _selected_anchor_idx = idx + 1
    _rebuild_anchors_list()
    _rebuild_track_list()
    _refresh_control_buttons()


def _remove_anchor():
    global _anchor_plan, _selected_anchor_idx
    if _selected_anchor_idx is None or _selected_anchor_idx < 0 or _selected_anchor_idx >= len(_anchor_plan):
        return
    del _anchor_plan[_selected_anchor_idx]
    _selected_anchor_idx = None
    _rebuild_anchors_list()
    _rebuild_track_list()
    _refresh_control_buttons()


def _add_placeholder():
    global _anchor_plan
    _anchor_plan.append({"type": "placeholder"})
    _rebuild_anchors_list()
    _rebuild_track_list()
    _refresh_control_buttons()


def _add_selected_track():
    global _anchor_plan, _selected_track_idx
    if _selected_track_idx is None or _selected_track_idx < 0 or _selected_track_idx >= len(_playlist_tracks):
        return
    tid = _playlist_tracks[_selected_track_idx].get("id", "")
    if tid and tid not in _anchored_track_ids():
        _anchor_plan.append({"type": "anchor", "track_id": tid})
    _rebuild_anchors_list()
    _rebuild_track_list()
    _refresh_control_buttons()


def _clear_anchors():
    """Clear ALL items from the anchor plan (no leftover placeholder)."""
    global _anchor_plan, _selected_anchor_idx, _selected_track_idx
    _anchor_plan.clear()
    _selected_anchor_idx = None
    _selected_track_idx = None
    _rebuild_anchors_list()
    _rebuild_track_list()
    _refresh_control_buttons()


def _save_anchors():
    """Save the current _anchor_plan to disk. Does NOT mutate the plan.
    
    Saving an empty anchor plan saves an EMPTY list ([]), full stop.
    There is no product requirement to inject a placeholder on save.
    """
    global _anchor_plan
    pl_id = getattr(_state, "anchors_selected_playlist_id", None)
    if not pl_id:
        ui.notify("No playlist selected", type="warning")
        return

    # Make a COPY to avoid mutating the live plan
    plan_to_save = list(_anchor_plan)

    _save_anchors_file(pl_id, _playlist_name, plan_to_save)
    actual_count = len(plan_to_save)
    ui.notify(f"Anchors saved ({actual_count} items)", type="positive")
    logger.info("Saved %d anchor items for playlist %s", actual_count, pl_id[:8])


# ── UI rendering ───────────────────────────────────────────────────────────

def _render_controls():
    """Render the anchor control buttons row."""
    global _controls_row, _move_up_btn, _move_down_btn, _remove_btn
    global _add_placeholder_btn, _add_selected_track_btn, _clear_btn, _save_btn

    with ui.row().classes("w-full gap-1 items-center mt-2") as _controls_row:
        _move_up_btn = ui.button("↑", on_click=_move_up).classes("text-sm").props("size=sm")
        _move_down_btn = ui.button("↓", on_click=_move_down).classes("text-sm").props("size=sm")
        _remove_btn = ui.button("Remove", on_click=_remove_anchor).classes("text-sm").props("size=sm color=red")
        _add_placeholder_btn = ui.button("Add placeholder", on_click=_add_placeholder).classes("text-sm").props("size=sm")
        _add_selected_track_btn = ui.button("Add selected track", on_click=_add_selected_track).classes("text-sm").props("size=sm color=blue")
        _clear_btn = ui.button("Clear Anchors", on_click=_clear_anchors).classes("text-sm").props("size=sm color=orange")
        _save_btn = ui.button("Save Anchors", on_click=_save_anchors).classes("text-sm").props("size=sm color=green")

    _refresh_control_buttons()


def _render_anchors_list():
    """Render the anchors list table — compact, no pagination.
    NO Quasar selection mode — all selection tracked in Python via rowClick."""
    title_text = f"Anchors for {_playlist_name}" if _playlist_name else "Anchors"

    columns = [
        {"name": "idx", "label": "#", "field": "idx", "sortable": True},
        {"name": "type", "label": "Type", "field": "type"},
        {"name": "info", "label": "Track", "field": "info"},
    ]
    rows = []
    track_by_id = {t["id"]: t for t in _playlist_tracks}
    for i, entry in enumerate(_anchor_plan, 1):
        if entry["type"] == "anchor":
            tid = entry.get("track_id", "")
            track = track_by_id.get(tid, {})
            name = track.get("name", "?")[:40]
            artist = track.get("artist", "?")[:30]
            rows.append({
                "idx": i,
                "type": "⚓ Anchor",
                "info": f"{name} — {artist}",
            })
        else:
            rows.append({
                "idx": i,
                "type": "· Placeholder",
                "info": "— placeholder —",
            })

    ui.label(title_text).classes("text-lg font-bold mb-1")
    if not rows:
        ui.label("No anchors yet. Add some below.").classes("text-sm text-gray-400 italic mb-2")
        return

    global _anchors_table
    _anchors_table = ui.table(
        columns=columns,
        rows=rows,
        row_key="idx",
        pagination={"rowsPerPage": 0},
    ).classes("w-full").props("dense")
    _anchors_table.on("rowClick", _on_anchor_row_click)


def _render_track_list():
    """Render the playlist track list — reuses shared build_track_rows().
    NO Quasar selection mode — all selection tracked in Python."""
    if not _playlist_tracks:
        ui.label("No tracks loaded.").classes("text-sm text-gray-400 italic")
        return

    anchored_ids = _anchored_track_ids()
    rows = build_track_rows(_playlist_tracks, anchored_ids=anchored_ids)

    columns = [
        {"name": "idx", "label": "#", "field": "idx", "sortable": True},
        {"name": "name", "label": "Track", "field": "name"},
        {"name": "artist", "label": "Artist", "field": "artist"},
        {"name": "duration", "label": "Dur", "field": "duration"},
        {"name": "desc", "label": "Desc", "field": "desc", "sortable": False},
        {"name": "status", "label": "Status", "field": "status"},
    ]

    ui.label(f"Playlist: {_playlist_name} ({len(_playlist_tracks)} tracks)").classes("text-sm font-semibold mb-1")
    global _track_table
    _track_table = ui.table(
        columns=columns,
        rows=rows,
        row_key="idx",
        selection="multiple",
        pagination={"rowsPerPage": 0},
    ).classes("w-full").props("dense")
    # Custom body slot with native checkbox column (selection="multiple") +
    # anchored-row graying + desc icon rendering + click/dblclick forwarding.
    # Quasar's default body renders <q-checkbox v-model="props.selected"> in
    # the first cell, then each data column.  We replicate this and add
    # @click/@dblclick on <q-tr> which emit the same events QTable's native
    # body would: ("rowClick", evt, row, pageIndex) and
    # ("rowDblclick", evt, row, pageIndex) — confirmed at
    # nicegui/static/quasar.umd.js:emit("rowDblclick", evt, row, pageIndex).
    # $parent.$emit is standard Vue 3 component communication, not an internal.
    # Checkbox @click.stop prevents the row click from toggling selection AND
    # triggering _selected_track_idx — checkbox clicks stay isolated.
    from playlist_arranger.ui.pages.playlist_source import _on_desc_icon_click
    _track_table.add_slot("body", r'''
    <q-tr :props="props" :class="props.row.locked ? 'anchors-locked-row' : ''"
          @click="(evt) => $parent.$emit('rowClick', evt, props.row, props.pageIndex)"
          @dblclick="(evt) => $parent.$emit('rowDblclick', evt, props.row, props.pageIndex)">
        <q-td auto-width>
            <q-checkbox v-if="props.selected !== void 0" v-model="props.selected" @click.stop />
        </q-td>
        <q-td v-for="col in props.cols" :key="col.name" :props="props">
            <template v-if="col.name === 'desc'">
              <span class="desc-icon-container">
                <q-icon :name="props.row.desc_icon" :color="props.row.desc_color" size="18px"
                        style="cursor: pointer;"
                        @click.stop="() => $parent.$emit('desc_click', props.row)" />
                <span class="desc-icon-tip">{{ props.row.desc_caption }}</span>
              </span>
            </template>
            <template v-else>
              {{ props.row[col.field] }}
            </template>
        </q-td>
    </q-tr>
    ''')
    _track_table.on("desc_click", _on_desc_icon_click)

    # CSS for anchored rows
    ui.add_head_html("""
    <style>
    .anchors-locked-row {
        opacity: 0.45 !important;
        pointer-events: none !important;
        background-color: #f5f5f5 !important;
    }
    body.body--dark .anchors-locked-row {
        background-color: #1a1a1a !important;
    }
    </style>
    """)


def _on_playlist_selected(pl_id: str):
    """Handle playlist selection change — load tracks and anchor plan."""
    global _anchor_plan, _playlist_tracks, _playlist_name
    global _selected_anchor_idx, _selected_track_idx

    _state.anchors_selected_playlist_id = pl_id

    if not pl_id:
        return

    # Load anchor plan
    _anchor_plan = _load_anchors_file(pl_id) or []

    # Load playlist tracks via the existing caching/fetching pipeline
    from playlist_arranger.ui.pages.playlist_source import _load_cached_playlist_tracks
    try:
        _playlist_tracks = _load_cached_playlist_tracks(pl_id)
    except Exception as exc:
        logger.exception("Failed to load tracks for playlist %s", pl_id[:8])
        ui.notify(f"Failed to load tracks: {exc}", type="negative")
        _playlist_tracks = []

    # Extract playlist name
    _playlist_name = ""
    try:
        pl_data = _state.sp.playlist(pl_id, fields="name")
        _playlist_name = pl_data.get("name", pl_id[:8])
    except Exception:
        _playlist_name = pl_id[:8]

    _selected_anchor_idx = None
    _selected_track_idx = None

    _rebuild_anchors_list()
    _rebuild_track_list()
    _refresh_control_buttons()


def build_anchors():
    """Build the full anchors page."""
    global _anchor_plan, _playlist_tracks, _playlist_name
    global _selected_anchor_idx, _selected_track_idx
    global _anchors_select, _anchors_list_container, _track_list_container

    ui.label("Anchors").classes("text-2xl font-bold mb-4")

    if _state.sp is None or not _state.spotify_user_id:
        ui.label("Connect Spotify to view your playlists.").classes("text-red-500")
        return

    try:
        playlists = get_own_playlists(_state.sp, _state.spotify_user_id)
    except Exception as exc:
        ui.label(f"Failed to load playlists: {exc}").classes("text-red-500")
        return

    if not playlists:
        ui.label("No playlists found.").classes("text-gray-500")
        return

    options = {pl["id"]: pl["name"] for pl in playlists}

    saved_id = getattr(_state, "anchors_selected_playlist_id", None)
    default_val = saved_id if saved_id in options else (list(options.keys())[0] if options else None)

    def on_change(e):
        _on_playlist_selected(e.value)

    _anchors_select = ui.select(
        label="Select a playlist",
        options=options,
        value=default_val,
        on_change=on_change,
    ).classes("w-80 mb-4")

    # ── Anchors list + controls ───────────────────────────────────────────
    _anchors_list_container = ui.column().classes("w-full mb-2")
    with _anchors_list_container:
        _render_anchors_list()

    _render_controls()

    # ── Playlist track list ───────────────────────────────────────────────
    ui.separator().classes("my-4")
    _track_list_container = ui.column().classes("w-full")
    with _track_list_container:
        _render_track_list()

    if default_val:
        _on_playlist_selected(default_val)