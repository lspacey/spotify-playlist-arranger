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
    try:
        client = _queue_container.client
        if not client.has_socket_connection:
            logger.debug("Skipping queue UI rebuild — client disconnected")
            return
    except Exception:
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


def update_queue_row_desc(track_id: str) -> bool:
    """Targeted in-place desc update for a single track's row in the queue table.

    Finds the row matching *track_id* in ``_queue_rows_cache``, mutates
    ``desc_icon``/``desc_color``/``desc_caption``/``desc`` fields in-place
    from a fresh DB read via ``_state.get_track_status_with_desc()``, then
    calls ``_queue_table_ref.update()``.

    Does NOT call ``.clear()`` or re-render the container — no full
    table teardown+rebuild cycle.  Preserves pagination state.

    Returns True if a matching row was found and updated, False otherwise.
    """
    global _queue_table_ref, _queue_rows_cache

    tid_short = track_id[:8] if track_id else "?"
    guard_reason = None

    if not track_id:
        guard_reason = "track_id is empty"
    elif _queue_table_ref is None:
        guard_reason = "_queue_table_ref is None (queue table not yet rendered)"
    elif not _queue_rows_cache:
        guard_reason = "_queue_rows_cache is empty (0 rows)"

    if guard_reason:
        logger.warning(
            "update_queue_row_desc(%s): SKIPPED — %s",
            tid_short, guard_reason,
        )
        return False

    cache_track_ids = [r.get("track_id") for r in _queue_rows_cache]
    logger.info(
        "update_queue_row_desc(%s): searching %d cached rows, "
        "sample ids=%s",
        tid_short, len(_queue_rows_cache),
        [t[:8] for t in (cache_track_ids[:5] if cache_track_ids else [])],
    )

    updated = False
    for row in _queue_rows_cache:
        try:
            if row.get("track_id") == track_id:
                old_icon = row.get("desc_icon", "?")
                combined = _state.get_track_status_with_desc(
                    {"id": track_id, "duration_ms": 0}
                )
                new_icon = combined["desc_icon"]
                row["desc_icon"] = new_icon
                row["desc_color"] = combined["desc_color"]
                row["desc_caption"] = combined["desc_caption"]
                row["desc"] = combined["desc"]
                logger.info(
                    "update_queue_row_desc(%s): row FOUND, "
                    "desc_icon '%s' → '%s', calling table.update()",
                    tid_short, old_icon, new_icon,
                )
                updated = True
                break
        except Exception:
            logger.exception(
                "update_queue_row_desc(%s): exception while updating row",
                tid_short,
            )

    if not updated:
        logger.debug(
            "update_queue_row_desc(%s): row NOT FOUND in _queue_rows_cache "
            "(%d rows checked, sample ids: %s) — this is normal for tracks "
            "generated from playlist views (not queued for analysis)",
            tid_short, len(_queue_rows_cache),
            [t[:8] for t in (cache_track_ids[:10] if cache_track_ids else [])],
        )
        return False

    try:
        # NiceGUI's table.update() only re-renders the DOM template;
        # it does NOT re-serialize mutated-in-place dict changes to JSON
        # for the client unless we reassign ._props["rows"] first.
        _queue_table_ref._props["rows"] = list(_queue_rows_cache)
        _queue_table_ref.update()
        logger.info(
            "update_queue_row_desc(%s): rows reassigned + table.update() OK",
            tid_short,
        )
    except Exception:
        logger.exception(
            "update_queue_row_desc(%s): table.update() RAISED",
            tid_short,
        )

    return True


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
            combined = _state.get_track_status_with_desc(t)
            combined["status"] = status
        else:
            combined = _state.get_track_status_with_desc(t)
        rows.append({"idx": i, "name": t.get("name", "")[:42], "artist": t.get("artist", "")[:40],
                     "duration": dur_str, "status": combined["status"],
                     "desc": combined["desc"],
                     "desc_icon": combined["desc_icon"],
                     "desc_color": combined["desc_color"],
                     "desc_caption": combined["desc_caption"],
                     "track_id": t.get("id", ""),
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
