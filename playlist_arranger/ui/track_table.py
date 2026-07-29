"""Compact track table renderer for Spotify and Local File playlists.

Extracted from ``playlist_source.py`` (2026-07-29 refactor — pattern #18).
Uses the same ``configure()`` dependency-injection pattern as
``analysis/batch_analyzer.py`` and ``ui/playlist_highlight.py``.

This module resolves the two temporary bridge imports from Steps 1-2:
``_show_track_compact_table`` (used by ``local_section.py``) and
``_on_desc_icon_click`` (used by ``analysis_queue.py``).  Both are now
injected via ``configure()``.
"""

import asyncio
import logging

from nicegui import ui

logger = logging.getLogger(__name__)

# ── Injected dependencies (wired by playlist_source.py at module init) ────────
_state = None              # playlist_arranger.ui.state module ref
_db = None                 # playlist_arranger.database.db module ref
_ph = None                 # playlist_arranger.ui.playlist_highlight module ref
_desc_gen = None           # playlist_arranger.analysis.desc_generator module ref
_get_desc_age_info = None  # playlist_arranger.analysis.desc_status.get_desc_age_info
_get_track_status = None   # playlist_source._get_track_status
_play_track = None         # playlist_source._play_track
_add_selected_to_queue_fn = None  # playlist_source._add_selected_to_queue
_persist_queue_fn = None          # playlist_source._persist_queue
_update_queue_label_fn = None     # playlist_source._update_queue_label
_rebuild_queue_ui_fn = None       # playlist_source._rebuild_queue_ui
_recover_from_backup_fn = None    # playlist_source._recover_from_backup
_backup_exists_fn = None          # playlist_arranger.cache.store.backup_exists
_show_desc_dialog_fn = None       # playlist_arranger.ui.desc_dialog.show_desc_dialog


def configure(state_module, db_module, ph_module, desc_gen_module,
              get_desc_age_info_fn, get_track_status_fn, play_track_fn,
              add_selected_to_queue_fn, persist_queue_fn,
              update_queue_label_fn, rebuild_queue_ui_fn,
              recover_from_backup_fn, backup_exists_fn,
              show_desc_dialog_fn):
    """Wire runtime dependencies from ``playlist_source.py`` at init time."""
    global _state, _db, _ph, _desc_gen
    global _get_desc_age_info, _get_track_status, _play_track
    global _add_selected_to_queue_fn, _persist_queue_fn
    global _update_queue_label_fn, _rebuild_queue_ui_fn
    global _recover_from_backup_fn, _backup_exists_fn
    global _show_desc_dialog_fn
    _state = state_module
    _db = db_module
    _ph = ph_module
    _desc_gen = desc_gen_module
    _get_desc_age_info = get_desc_age_info_fn
    _get_track_status = get_track_status_fn
    _play_track = play_track_fn
    _add_selected_to_queue_fn = add_selected_to_queue_fn
    _persist_queue_fn = persist_queue_fn
    _update_queue_label_fn = update_queue_label_fn
    _rebuild_queue_ui_fn = rebuild_queue_ui_fn
    _recover_from_backup_fn = recover_from_backup_fn
    _backup_exists_fn = backup_exists_fn
    _show_desc_dialog_fn = show_desc_dialog_fn


# ─── Description icon click handler ───────────────────────────────────────────

def on_desc_icon_click(e):
    """Handler for desc icon clicks — receives the full row dict via $parent.$emit."""
    row = e.args if e.args else {}
    tid = row.get("track_id", "") if isinstance(row, dict) else ""
    tname = row.get("track_name_original", "") if isinstance(row, dict) else ""
    artist = row.get("artist", "") if isinstance(row, dict) else ""
    logger.debug("desc icon clicked: track_id=%s", tid[:8] if tid else "?")
    if tid:
        _show_desc_dialog_fn(tid, tname, artist=artist)


# ─── Tooltip CSS injection ────────────────────────────────────────────────────

def inject_desc_tooltip_css():
    """Inject CSS for description icon tooltips once (idempotent).

    Uses pure CSS :hover tooltip instead of Quasar <q-tooltip> to avoid
    flicker when table cells re-render (rows= reassignment, queue rebuild).
    CSS tooltips are immune to DOM re-render because they have no popup
    lifecycle — the style simply applies to the new element instantly."""
    ui.add_head_html('''
    <style>
    /* Allow the tooltip to overflow the table cell — QTable cells
       may inherit overflow:hidden from scrollable table wrappers. */
    .q-table td:has(.desc-icon-container),
    .q-table th:has(.desc-icon-container) {
      overflow: visible !important;
    }
    .desc-icon-container {
      position: relative;
      display: inline-block;
      cursor: default;
    }
    .desc-icon-tip {
      visibility: hidden;
      opacity: 0;
      position: absolute;
      bottom: calc(100% + 4px);
      left: 50%;
      transform: translateX(-50%);
      white-space: nowrap;
      background: rgba(0, 0, 0, 0.82);
      color: #fff;
      padding: 2px 8px;
      border-radius: 4px;
      font-size: 12px;
      line-height: 1.4;
      z-index: 10000;
      pointer-events: none;
      transition: opacity 0.12s ease;
    }
    .desc-icon-container:hover .desc-icon-tip {
      visibility: visible;
      opacity: 1;
    }
    </style>
    ''')


# ─── Selected rows helper ─────────────────────────────────────────────────────

def get_selected_rows(pl_id: str):
    table = _ph._playlist_tables.get(pl_id)
    if table is None:
        return []
    selected = list(table.selected) if hasattr(table, 'selected') else []
    np_key = _ph._now_playing_row_keys.get(pl_id)
    if np_key is not None:
        selected = [r for r in selected if r.get("idx") != np_key]
    return selected


# ─── Compact track table ──────────────────────────────────────────────────────

def show_track_compact_table(tracks, pl_id, pl_name, set_page_cb):
    in_db = sum(1 for t in tracks if _get_track_status(t) == _state.STATUS_OK)
    ui.label(f"Total: {len(tracks)} | In DB: {in_db} | Double-click a row to ▶ Play").classes("text-xs text-gray-500 mb-2")

    columns = [
        {"name": "idx", "label": "#", "field": "idx", "sortable": True},
        {"name": "name", "label": "Track", "field": "name"},
        {"name": "artist", "label": "Artist", "field": "artist"},
        {"name": "duration", "label": "Dur", "field": "duration"},
        {"name": "desc", "label": "Desc", "field": "desc", "sortable": False},
        {"name": "status", "label": "Status", "field": "status"},
    ]
    rows = []
    for i, t in enumerate(tracks, 1):
        dur_ms = t.get("duration_ms", 0)
        dur_str = f"{dur_ms // 60000}:{(dur_ms // 1000) % 60:02d}" if dur_ms else "?"
        status = _get_track_status(t)
        # Look up desc status from DB
        tid = t.get("id", "")
        entry = _db.get_track(tid) if tid else None
        desc_info = _get_desc_age_info(
            entry.get("desc_text") if entry else None,
            entry.get("desc_generated_at") if entry else None,
        )
        icon_name = "auto_stories" if desc_info.has_desc else "menu_book"
        icon_color = desc_info.color
        icon_caption = desc_info.caption
        rows.append({"idx": i, "name": t["name"][:42], "artist": t["artist"][:40],
                     "duration": dur_str, "status": status,
                     "desc": "✓" if desc_info.has_desc else "—",
                     "desc_icon": icon_name,
                     "desc_color": icon_color,
                     "desc_caption": icon_caption,
                     "track_id": tid,
                     "track_name_original": t.get("name", "")})

    track_table = ui.table(
        columns=columns, rows=rows, row_key="idx",
        selection="multiple",
        pagination={"rowsPerPage": 0},
    ).classes("w-full").props("dense")
    track_table.add_slot("body-cell-desc", r"""
    <q-td :props="props">
      <span class="desc-icon-container">
        <q-icon :name="props.row.desc_icon" :color="props.row.desc_color" size="18px"
                style="cursor: pointer;"
                @click.stop="() => $parent.$emit('desc_click', props.row)" />
        <span class="desc-icon-tip">{{ props.row.desc_caption }}</span>
      </span>
    </q-td>
    """)
    track_table.on("desc_click", on_desc_icon_click)

    _ph._playlist_tables[pl_id] = track_table
    _ph._playlist_rows_cache[pl_id] = rows

    def on_row_dblclick(e):
        row_data = e.args[1] if isinstance(e.args, list) and len(e.args) >= 2 else {}
        row_idx = row_data.get("idx", 0) - 1
        if 0 <= row_idx < len(tracks):
            track = tracks[row_idx]
            client = ui.context.client
            ui.timer(0.0, lambda t=track, c=client: asyncio.ensure_future(_play_track(t, client=c)), once=True)
            _ph._sync_now_playing_row_highlight(pl_id, track.get("id", ""))

    track_table.on("rowDblclick", on_row_dblclick)

    missing = sum(1 for t in tracks if _get_track_status(t) != _state.STATUS_OK)
    with ui.row().classes("w-full gap-2 mt-2"):
        def _build_add_to_queue_btn():
            btn = ui.button(
                "Add Selected Tracks to Queue for Analysis",
                on_click=lambda: _add_selected_to_queue_fn(pl_id, tracks),
                color="yellow",
            ).classes("text-sm")
            btn.set_enabled(len(get_selected_rows(pl_id)) > 0)
            def _refresh_btn_enabled():
                btn.set_enabled(len(get_selected_rows(pl_id)) > 0)
            ui.timer(0.5, _refresh_btn_enabled)
            return btn

        _build_add_to_queue_btn()

        def _add_not_ok_to_queue():
            not_ok = [t for t in tracks if _state.get_track_status(t) != _state.STATUS_OK]
            if not not_ok:
                ui.notify("All tracks are OK — nothing to add", type="info")
                return
            added = 0
            skipped = 0
            for t in not_ok:
                with _state.analysis_queue_lock:
                    tid = t.get("id", "")
                    if not tid:
                        logger.warning("Dedup skipped — track has no id: %s", t.get("name", "?")[:40])
                        _state.analysis_queue.append(t)
                        added += 1
                    elif any(q.get("id") == tid for q in _state.analysis_queue):
                        skipped += 1
                    else:
                        _state.analysis_queue.append(t)
                        added += 1
                # Lock released
            _persist_queue_fn()
            _update_queue_label_fn()
            _rebuild_queue_ui_fn()
            if added:
                ui.notify(f"Added {added} track(s) to queue" + (f" (skipped {skipped} duplicate(s))" if skipped else ""), type="positive")
            elif skipped:
                ui.notify(f"All {skipped} track(s) already in queue — nothing to add", type="info")
            logger.info("Added %d not-OK track(s) to analysis queue (total=%d, skipped %d)", added, len(_state.analysis_queue), skipped)

        ui.button(
            "Add Not OK Tracks to Queue for Analysis",
            on_click=_add_not_ok_to_queue,
            color="yellow",
        ).classes("text-sm")

        # ── Description generation buttons (playlist table) ──────────────────
        def _desc_gen_selected():
            selected_rows = get_selected_rows(pl_id)
            if not selected_rows:
                ui.notify("No tracks selected", type="warning")
                return
            track_ids = [r["track_id"] for r in selected_rows if r.get("track_id")]
            n = _desc_gen.desc_queue_add_many(track_ids)
            if n > 0:
                ui.notify(f"Added {n} tracks to description queue", type="positive")
            else:
                ui.notify("All selected tracks are already in the description queue", type="info")

        desc_selected_btn = ui.button(
            "Generate new descriptions for selected tracks",
            on_click=_desc_gen_selected,
            color="blue",
        ).classes("text-sm")
        desc_selected_btn.set_enabled(len(get_selected_rows(pl_id)) > 0)
        def _refresh_desc_sel_btn():
            desc_selected_btn.set_enabled(len(get_selected_rows(pl_id)) > 0)
        ui.timer(0.5, _refresh_desc_sel_btn)

        def _desc_gen_all():
            track_ids = [t.get("id", "") for t in tracks if t.get("id")]
            n = _desc_gen.desc_queue_add_many(track_ids)
            if n > 0:
                ui.notify(f"Added {n} tracks to description queue", type="positive")
            else:
                ui.notify("All tracks are already in the description queue", type="info")

        desc_all_btn = ui.button(
            "Generate new descriptions for all tracks",
            on_click=_desc_gen_all,
            color="blue",
        ).classes("text-sm")
        desc_all_btn.set_enabled(len(tracks) > 0)

        if _backup_exists_fn(pl_id):
            ui.button("Recover from backup", on_click=lambda: _recover_from_backup_fn(pl_id, set_page_cb), color="purple").classes("text-sm")

    def _refresh_status_cells():
        nonlocal rows, track_table
        updated = False
        for i, t in enumerate(tracks):
            new_status = _get_track_status(t)
            if rows[i].get("status") != new_status:
                rows[i]["status"] = new_status
                updated = True
        if updated:
            track_table.rows = rows
            _ph._playlist_rows_cache[pl_id] = rows

    ui.timer(2.0, _refresh_status_cells)