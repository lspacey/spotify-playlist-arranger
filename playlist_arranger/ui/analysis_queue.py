"""Queue for Analysis section — table rendering, add/remove handlers.

Extracted from ``playlist_source.py`` (2026-07-29 refactor — pattern #18).
Uses the same ``configure()`` dependency-injection pattern as
``analysis/batch_analyzer.py`` and ``ui/playlist_highlight.py``.

``_render_queue_controls()`` intentionally remains in
``playlist_source.py`` because it wires the batch analysis start/stop
button, which is entangled with the Now Playing threading code that is
explicitly out-of-scope for this refactor.  That function accesses
this module's exported globals (``_queue_table_ref`` etc.) via the
imported module alias ``_aq`` in ``playlist_source.py``.

TODO(step-3): ``_on_desc_icon_click`` will move from
``playlist_source.py`` into ``playlist_arranger/ui/track_table.py`` —
update the injected dependency when that lands, per the playlist_source.py
refactor plan.
"""

import asyncio
import logging

from nicegui import ui

from playlist_arranger.ui import table_pagination as _tp

logger = logging.getLogger(__name__)

# ── Injected dependencies (wired by playlist_source.py at module init) ────────
_state = None              # playlist_arranger.ui.state module ref
_db = None                 # playlist_arranger.database.db module ref
_desc_gen = None           # playlist_arranger.analysis.desc_generator module ref
_ph = None                 # playlist_arranger.ui.playlist_highlight module ref
_get_desc_age_info = None  # playlist_arranger.analysis.desc_status.get_desc_age_info
_get_track_status = None   # playlist_source._get_track_status
_play_track = None         # playlist_source._play_track
_desc_icon_click = None    # playlist_source._on_desc_icon_click (TODO step-3 bridge)
_render_queue_controls_fn = None  # callback: playlist_source._render_queue_controls


def configure(state_module, db_module, desc_gen_module, ph_module,
              get_desc_age_info_fn, get_track_status_fn, play_track_fn,
              desc_icon_click_fn, render_queue_controls_fn):
    """Wire runtime dependencies from ``playlist_source.py`` at init time."""
    global _state, _db, _desc_gen, _ph
    global _get_desc_age_info, _get_track_status, _play_track
    global _desc_icon_click, _render_queue_controls_fn
    _state = state_module
    _db = db_module
    _desc_gen = desc_gen_module
    _ph = ph_module
    _get_desc_age_info = get_desc_age_info_fn
    _get_track_status = get_track_status_fn
    _play_track = play_track_fn
    _desc_icon_click = desc_icon_click_fn
    _render_queue_controls_fn = render_queue_controls_fn


# ── Queue for Analysis section ──────────────────────────────────────────────
_queue_table_ref = None
_queue_rows_cache = []
_queue_now_playing_row_keys = {}  # track→idx for now-playing highlight (analyze mode, not batch)
_queue_container = None
_queue_expansion_ref = None
_queue_label_ref = None


def get_queue_table_ref():
    """Expose _queue_table_ref for _render_queue_controls() in playlist_source.py."""
    return _queue_table_ref


def get_queue_rows_cache():
    """Expose _queue_rows_cache for now-playing highlight in playlist_source.py."""
    return _queue_rows_cache


def persist_queue():
    _state.save_analysis_queue()


def rebuild_queue_ui():
    global _queue_table_ref, _queue_rows_cache, _queue_container
    if _queue_container is None:
        return

    # ── Safety: skip rebuild if client disconnected (background-thread callback) ──
    # _on_desc_generated() is invoked from a background worker thread
    # (_desc_worker_loop → cb(track_id) → _on_desc_generated → rebuild_queue_ui).
    # By the time the callback fires, the user may have navigated away or
    # disconnected, leaving _queue_container as a stale NiceGUI element whose
    # client has no active socket connection.  Calling .clear() on a
    # disconnected element triggers a "deleted but still being used" warning.
    # We check has_socket_connection before any UI mutation and bail out safely.
    try:
        client = _queue_container.client
        if not client.has_socket_connection:
            logger.debug("Skipping queue UI rebuild — client disconnected")
            return
    except Exception:
        # Cannot verify connection state (element may be in a bad state) —
        # bail out safely rather than crash or emit a warning.
        logger.debug("Could not verify client connection — skipping queue UI rebuild")
        return

    _queue_container.clear()
    # Clamp saved page after potential track removals (e.g. analysis-complete
    # auto-removal) — if user was on page 3 of 3 and one track dropped the
    # total to 2 pages, clamp to page 2 instead of leaving an out-of-range val.
    _tp.clamp_page_to_valid("__queue__", len(_state.analysis_queue))
    with _queue_container:
        # Controls above the table for immediate access
        _render_queue_controls_fn()
        render_queue_table()
    # Single source of truth: re-evaluate batch button enabled state AFTER
    # every queue rebuild, regardless of which code path triggered it.
    # Must run outside the context-manager-with in case _render_queue_controls_fn()
    # failed partway through and left _ba._batch_btn stale.
    #
    # NOTE: refresh_batch_btn_enabled is called from _render_queue_controls_fn()
    # at button creation time as well, but calling it again here is harmless
    # and ensures the button state is always correct after any rebuild trigger
    # (e.g. _on_analysis_complete callback).


def add_selected_to_queue(pl_id: str, tracks: list):
    selected_rows = _get_selected_rows(pl_id)
    if not selected_rows:
        ui.notify("No tracks selected", type="warning")
        return

    selected_idxs = sorted(r["idx"] for r in selected_rows)
    added = 0
    skipped = 0
    for idx in selected_idxs:
        i = idx - 1
        if 0 <= i < len(tracks):
            track = tracks[i]
            with _state.analysis_queue_lock:
                tid = track.get("id", "")
                if not tid:
                    logger.warning("Dedup skipped — track has no id: %s", track.get("name", "?")[:40])
                    _state.analysis_queue.append(track)
                    added += 1
                elif any(t.get("id") == tid for t in _state.analysis_queue):
                    skipped += 1
                else:
                    _state.analysis_queue.append(track)
                    added += 1
            # Lock released
            if skipped and not added:
                pass  # notification handled below, outside the per-track loop
        persist_queue()
    update_queue_label()
    rebuild_queue_ui()
    ui.notify(f"Added {added} track(s) to queue" + (f" (skipped {skipped} duplicate(s))" if skipped else ""), type="positive")
    if skipped:
        ui.notify(f"{skipped} track(s) already in queue — skipped", type="info")
    logger.info("Added %d track(s) to analysis queue (total=%d)", added, len(_state.analysis_queue))


def update_queue_label():
    global _queue_label_ref, _queue_expansion_ref
    n = len(_state.analysis_queue)
    label = f"Queue for Analysis — {n} track{'s' if n != 1 else ''}"
    if _queue_label_ref is not None:
        _queue_label_ref.set_text(label)
    if _queue_expansion_ref is not None:
        _queue_expansion_ref.set_text(label)


def render_queue_table():
    global _queue_table_ref, _queue_rows_cache
    if not _state.analysis_queue:
        ui.label("Queue is empty").classes("text-sm text-gray-400 italic")
        return

    columns = [
        {"name": "idx", "label": "#", "field": "idx", "sortable": True},
        {"name": "name", "label": "Track", "field": "name"},
        {"name": "artist", "label": "Artist", "field": "artist"},
        {"name": "duration", "label": "Dur", "field": "duration"},
        {"name": "desc", "label": "Desc", "field": "desc", "sortable": False},
        {"name": "status", "label": "Status", "field": "status"},
    ]
    rows = []
    for i, t in enumerate(_state.analysis_queue, 1):
        dur_ms = t.get("duration_ms", 0)
        dur_str = f"{dur_ms // 60000}:{(dur_ms // 1000) % 60:02d}" if dur_ms else "?"
        if _state.analysis_current_track_id and t.get("id") == _state.analysis_current_track_id:
            status = "⏳ Processing"
        else:
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
        rows.append({"idx": i, "name": t.get("name", "")[:42], "artist": t.get("artist", "")[:40],
                     "duration": dur_str, "status": status,
                     "desc": "✓" if desc_info.has_desc else "—",
                     "desc_icon": icon_name,
                     "desc_color": icon_color,
                     "desc_caption": icon_caption,
                     "track_id": tid,
                     "track_name_original": t.get("name", "")})

    _queue_table_ref = ui.table(
        columns=columns, rows=rows, row_key="idx",
        selection="multiple",
        pagination=_tp.get_default_pagination(),
        on_pagination_change=_tp.on_pagination_change_handler("__queue__"),
    ).classes("w-full").props("dense")
    _tp.apply_pagination(_queue_table_ref, "__queue__")
    _queue_table_ref.add_slot("body-cell-desc", r"""
    <q-td :props="props">
      <span class="desc-icon-container">
        <q-icon :name="props.row.desc_icon" :color="props.row.desc_color" size="18px"
                style="cursor: pointer;"
                @click.stop="() => $parent.$emit('desc_click', props.row)" />
        <span class="desc-icon-tip">{{ props.row.desc_caption }}</span>
      </span>
    </q-td>
    """)
    _queue_table_ref.on("desc_click", _desc_icon_click)
    _queue_rows_cache = rows

    def on_row_dblclick(e):
        row_data = e.args[1] if isinstance(e.args, list) and len(e.args) >= 2 else {}
        row_idx = row_data.get("idx", 0) - 1
        if 0 <= row_idx < len(_state.analysis_queue):
            track = _state.analysis_queue[row_idx]
            client = ui.context.client
            ui.timer(0.0, lambda t=track, c=client: asyncio.ensure_future(_play_track(t, client=c)), once=True)

    _queue_table_ref.on("rowDblclick", on_row_dblclick)


def _get_selected_rows(pl_id: str):
    table = _ph._playlist_tables.get(pl_id)
    if table is None:
        return []
    selected = list(table.selected) if hasattr(table, 'selected') else []
    np_key = _ph._now_playing_row_keys.get(pl_id)
    if np_key is not None:
        selected = [r for r in selected if r.get("idx") != np_key]
    return selected


def render_analysis_queue():
    global _queue_expansion_ref, _queue_label_ref, _queue_container
    n = len(_state.analysis_queue)
    label = f"Queue for Analysis — {n} track{'s' if n != 1 else ''}"

    with ui.expansion(label, value=False).classes("w-full mb-4") as _queue_expansion_ref:
        _queue_expansion_ref.props('header-class="text-lg font-semibold"')
        _queue_label_ref = ui.label(label)
        _queue_container = ui.column().classes("w-full")
        with _queue_container:
            # Controls above the table for immediate access
            _render_queue_controls_fn()
            render_queue_table()
