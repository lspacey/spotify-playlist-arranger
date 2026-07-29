"""Smart sorting page — playlist dropdown, paginated track list, read-only anchors,
Start Sorting button with ATSP+SA solver, thread-safe progress logs, and sorted results.
"""

import asyncio
import datetime
import logging
import pathlib
import queue
import threading

from nicegui import ui

from playlist_arranger.ui import state as _state
from playlist_arranger.sources.spotify_source import (
    get_own_playlists,
    create_playlist,
    reorder_playlist,
    get_playlist_tracks,
    ERROR_USER_MESSAGES,
)
from playlist_arranger.cache.store import atomic_write_json
from playlist_arranger.config import ANCHORS_DIR_DEFAULT
from playlist_arranger.sorting.anchors import _load_anchors_file
from playlist_arranger.ui.components.track_rows import build_track_rows
from playlist_arranger.ui.state import get_track_status, STATUS_OK
from playlist_arranger.config import load_settings

logger = logging.getLogger(__name__)

# ── Module-level UI references ──────────────────────────────────────────────
_sort_select = None
_track_list_container = None
_anchors_list_container = None
_start_sort_btn = None
_start_sort_warning = None
_logs_expansion = None
_logs_content: ui.label | None = None
_logs_summary: ui.label | None = None
_results_container = None
_spinner = None
_save_section_container = None
_save_new_btn = None
_save_current_btn = None
_save_spinner = None

# ── Page-local data ─────────────────────────────────────────────────────────
_playlist_tracks: list = []
_playlist_name: str = ""
_sorted_track_uris: list[str] = []  # populated after successful sort
_save_in_progress: bool = False

# ── Thread-safe log queue ───────────────────────────────────────────────────
# The solver worker thread pushes strings here.  A ui.timer callback drains
# them on the main event-loop thread (safe for NiceGUI calls).
_log_queue: queue.Queue = queue.Queue()
_log_timer: ui.timer | None = None


def _enqueue_log(msg: str) -> None:
    """Push a progress message from any thread (called from solver callback)."""
    _log_queue.put(msg)


def _drain_log_queue() -> None:
    """Drain all queued log messages and append them to the logs content label.

    Called by ``ui.timer(0.3)`` on the main event-loop thread.
    """
    global _logs_content
    if _logs_content is None:
        return
    lines = []
    while True:
        try:
            lines.append(_log_queue.get_nowait())
        except queue.Empty:
            break
    if lines:
        current = _logs_content.text or ""
        if current:
            current += "\n"
        _logs_content.set_text(current + "\n".join(lines))


# ── Event handlers ──────────────────────────────────────────────────────────

def _on_playlist_selected(pl_id: str):
    """Load cached tracks and anchor plan for the selected playlist."""
    global _playlist_tracks, _playlist_name

    _state.selected_playlist_id = pl_id

    if not pl_id:
        _playlist_tracks = []
        _playlist_name = ""
        _rebuild_all()
        _refresh_start_button()
        _update_logs_summary()
        return

    from playlist_arranger.ui.pages.playlist_source import _load_cached_playlist_tracks

    try:
        _playlist_tracks = _load_cached_playlist_tracks(pl_id)
    except Exception as exc:
        logger.exception("Failed to load tracks for playlist %s", pl_id[:8])
        ui.notify(f"Failed to load tracks: {exc}", type="negative")
        _playlist_tracks = []

    _playlist_name = ""
    try:
        pl_data = _state.sp.playlist(pl_id, fields="name")
        _playlist_name = pl_data.get("name", pl_id[:8])
    except Exception:
        _playlist_name = pl_id[:8]

    _rebuild_all()
    _refresh_start_button()
    _update_logs_summary()
    _clear_results()


def _refresh_save_buttons():
    """Enable save buttons only when sorted results exist and no save is in progress."""
    global _save_new_btn, _save_current_btn, _save_spinner, _save_in_progress
    has_results = len(_sorted_track_uris) > 0
    can_save = has_results and not _save_in_progress
    if _save_new_btn is not None:
        _save_new_btn.set_enabled(can_save)
    if _save_current_btn is not None:
        _save_current_btn.set_enabled(can_save)
    if _save_spinner is not None:
        _save_spinner.set_visibility(_save_in_progress)


def _error_user_message(err: dict | str | None) -> str:
    """Convert Spotify error dict (or plain string) to a user-friendly message."""
    if err is None:
        return "Unknown error"
    if isinstance(err, dict):
        err_type = err.get("type", "unknown")
        msg = ERROR_USER_MESSAGES.get(err_type)
        if msg:
            return msg
        return err.get("message", "Unknown error")
    return str(err)


async def _on_save_new_playlist():
    """Create a new Spotify playlist with the sorted track order."""
    global _save_in_progress, _sorted_track_uris, _playlist_name

    if not _sorted_track_uris:
        ui.notify("No sorted results to save", type="warning")
        return
    if not _state.sp:
        ui.notify("Spotify not connected", type="warning")
        return

    _save_in_progress = True
    _refresh_save_buttons()

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    new_name = f"{_playlist_name}_{ts}" if _playlist_name else f"Sorted_{ts}"

    result = None
    try:
        result = await asyncio.to_thread(
            create_playlist, _state.sp, new_name, _sorted_track_uris,
        )
    except Exception as exc:
        logger.exception("Failed to create new playlist '%s'", new_name)
        ui.notify(f"Failed to create playlist: {exc}", type="negative")
    else:
        try:
            if result["success"]:
                pl_info = result.get("playlist") or {}
                pl_name = pl_info.get("name", new_name) if isinstance(pl_info, dict) else new_name
                ui.notify(
                    f"Saved to new playlist: {pl_name} ({result['tracks_saved']} tracks)",
                    type="positive",
                )
                logger.info(
                    "Created new playlist '%s' (%s) with %d tracks",
                    new_name, pl_info.get("id", "?"), result["tracks_saved"],
                )
            else:
                err_msg = _error_user_message(result.get("error"))
                pl_info = result.get("playlist")
                if pl_info and result["tracks_saved"] > 0:
                    # Partial success: playlist created, some tracks added
                    ui.notify(
                        f"Created playlist '{new_name}' but only saved "
                        f"{result['tracks_saved']} of {len(_sorted_track_uris)} tracks. "
                        f"Error: {err_msg}",
                        type="warning",
                    )
                    logger.error(
                        "Partial save to '%s': %d/%d tracks (chunk %d/%d failed). Error: %s",
                        new_name, result["tracks_saved"], len(_sorted_track_uris),
                        (result.get("failed_chunk_index") or 0) + 1,
                        result["chunks_total"],
                        result.get("error"),
                    )
                elif pl_info:
                    ui.notify(
                        f"Created playlist '{new_name}' but failed to add tracks. {err_msg}",
                        type="warning",
                    )
                    logger.error("Playlist created but 0 tracks added: %s", result.get("error"))
                else:
                    ui.notify(f"Failed to create playlist: {err_msg}", type="negative")
                    logger.error(
                        "Playlist creation failed for '%s': %s", new_name, result.get("error")
                    )
        except Exception as _render_exc:
            logger.exception("UI error rendering save result")
    finally:
        _save_in_progress = False
        _refresh_save_buttons()


def _backup_playlist_snapshot(sp, pl_id: str, pl_name: str):
    """Fetch current track order and write a timestamped backup JSON.

    Returns the backup file path on success, None on failure.
    Does NOT raise — failures are logged and surfaced as warnings only.
    """
    try:
        tracks = get_playlist_tracks(sp, pl_id)
    except Exception as exc:
        logger.warning("Failed to fetch track list for backup: %s", exc)
        return None

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_id = pl_id[:12].replace("/", "_").replace("\\", "_")
    bk_path = ANCHORS_DIR_DEFAULT / f"backup_{safe_id}_{ts}.json"
    data = {
        "playlist_id": pl_id,
        "playlist_name": pl_name,
        "saved_at": datetime.datetime.now().isoformat(),
        "tracks": [
            {"id": t["id"], "name": t["name"], "artist": t["artist"], "uri": t["uri"]}
            for t in tracks
        ],
    }
    try:
        bk_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(bk_path, data)
        logger.info("Backup saved to %s (%d tracks)", bk_path, len(tracks))
        return bk_path
    except Exception as exc:
        logger.warning("Failed to write backup file: %s", exc)
        return None


async def _on_save_current_playlist():
    """Overwrite the current playlist's track order with the sorted result."""
    global _save_in_progress, _sorted_track_uris, _playlist_name

    if not _sorted_track_uris:
        ui.notify("No sorted results to save", type="warning")
        return

    pl_id = getattr(_state, "selected_playlist_id", None)
    if not pl_id:
        ui.notify("No playlist selected", type="warning")
        return
    if not _state.sp:
        ui.notify("Spotify not connected", type="warning")
        return

    n = len(_sorted_track_uris)

    # ── Confirmation dialog ────────────────────────────────────────────────
    def _do_overwrite():
        async def _execute():
            global _save_in_progress
            _save_in_progress = True
            _refresh_save_buttons()

            result = None
            try:
                # ── 1. Backup current playlist state BEFORE any write ──────
                bk = _backup_playlist_snapshot(_state.sp, pl_id, _playlist_name)
                if bk is None:
                    ui.notify(
                        "Proceeding without backup — could not save playlist snapshot",
                        type="warning",
                    )

                # ── 2. Execute reorder ─────────────────────────────────────
                result = await asyncio.to_thread(
                    reorder_playlist, _state.sp, pl_id, _sorted_track_uris,
                )
            except Exception as exc:
                logger.exception("Failed to reorder playlist '%s' (%s)", _playlist_name, pl_id[:8])
                ui.notify(f"Failed to reorder playlist: {exc}", type="negative")
            else:
                try:
                    if result["success"]:
                        ui.notify(
                            f"Playlist reordered: {_playlist_name} ({n} tracks)",
                            type="positive",
                        )
                        logger.info(
                            "Reordered playlist '%s' (%s) with %d tracks",
                            _playlist_name, pl_id[:8], n,
                        )
                    else:
                        err_msg = _error_user_message(result.get("error"))
                        saved = result.get("tracks_saved", 0)
                        chunk_info = (
                            f"chunk {(result.get('failed_chunk_index') or 0)+1}/"
                            f"{result.get('chunks_total', '?')}"
                        )
                        if saved > 0:
                            # Partial success: some chunks completed
                            ui.notify(
                                f"Reordered {saved} of {n} tracks — "
                                f"stopped at {chunk_info}. {err_msg}",
                                type="warning",
                            )
                            logger.error(
                                "Partial reorder of '%s': %d/%d tracks (%s failed). Error: %s",
                                _playlist_name, saved, n, chunk_info, result.get("error"),
                            )
                        else:
                            # Total failure: no tracks saved
                            ui.notify(f"Reorder failed ({chunk_info}): {err_msg}", type="negative")
                            logger.error(
                                "Reorder of '%s' failed at %s: %s",
                                _playlist_name, chunk_info, result.get("error"),
                            )
                except Exception as _render_exc:
                    logger.exception("UI error rendering reorder result")
            finally:
                _save_in_progress = False
                _refresh_save_buttons()

        asyncio.ensure_future(_execute())

    with ui.dialog() as confirm_dialog, ui.card():
        ui.label(f"Confirm Reorder").classes("text-lg font-bold mb-2")
        ui.label(
            f"This will permanently reorder '{_playlist_name}' ({n} tracks) "
            f"on Spotify. This action cannot be easily undone."
        ).classes("text-sm mb-4")
        with ui.row().classes("gap-2 justify-end"):
            ui.button("Cancel", on_click=confirm_dialog.close).props("flat")
            ui.button("Confirm", on_click=lambda: (confirm_dialog.close(), _do_overwrite())) \
                .props("color=red")

    confirm_dialog.open()


def _refresh_start_button():
    """Enable Start Sorting only when ALL tracks have status == 'OK' (not empty)."""
    global _start_sort_btn, _start_sort_warning
    if _start_sort_btn is None:
        return
    all_ok = len(_playlist_tracks) > 0 and all(
        get_track_status(t) == STATUS_OK for t in _playlist_tracks
    )
    _start_sort_btn.set_enabled(all_ok)

    if _start_sort_warning is not None:
        if not all_ok and _playlist_tracks:
            missing = sum(1 for t in _playlist_tracks if get_track_status(t) != STATUS_OK)
            _start_sort_warning.set_text(
                f"{missing} track(s) missing audio features — analyze them first"
            )
            _start_sort_warning.set_visibility(True)
        elif not _playlist_tracks:
            _start_sort_warning.set_text("No tracks loaded — select a playlist above")
            _start_sort_warning.set_visibility(True)
        else:
            _start_sort_warning.set_visibility(False)


def _rebuild_all():
    """Rebuild track list + anchors list containers."""
    global _track_list_container, _anchors_list_container
    if _track_list_container is not None:
        _track_list_container.clear()
        with _track_list_container:
            _render_track_list()
    if _anchors_list_container is not None:
        _anchors_list_container.clear()
        with _anchors_list_container:
            _render_anchors_list()


def _clear_results():
    """Clear the sorted results list + saved URIs."""
    global _results_container, _sorted_track_uris
    _sorted_track_uris = []
    _refresh_save_buttons()
    if _results_container is not None:
        _results_container.clear()
        with _results_container:
            pass  # empty — nothing to show yet


def _update_logs_summary():
    """Update the static weights + SA summary at the top of the Sorting Logs."""
    global _logs_summary
    if _logs_summary is None:
        return
    s = load_settings()
    summary_lines = [
        f"Weights: mood={s.w_mood:.2f}, bpm={s.w_bpm:.2f}, "
        f"transition={s.w_transition:.2f}, key={s.w_key:.2f}, "
        f"energy={s.w_energy:.2f}, texture={s.w_texture:.2f}, "
        f"freq_balance={s.w_freq_balance:.2f}",
        f"Penalties: artist={s.artist_penalty:.3f}, album={s.album_penalty:.3f}, "
        f"duration_tolerance={s.duration_tolerance:.3f}",
        f"SA: iterations_multiplier={s.sa_iterations_multiplier}, "
        f"n_runs={s.sa_n_runs}, T_start={s.sa_T_start:.4g}, "
        f"T_end={s.sa_T_end:.4g}",
    ]
    _logs_summary.set_text("\n".join(summary_lines))


# ── Start Sorting async handler ──────────────────────────────────────────────

async def _on_start_sorting():
    """Start the ATSP + SA solver in a background thread via asyncio.to_thread."""
    global _start_sort_btn, _spinner, _logs_expansion, _logs_content, _results_container, _sorted_track_uris

    pl_id = getattr(_state, "selected_playlist_id", None)
    if not pl_id:
        ui.notify("No playlist selected", type="warning")
        return

    # Load anchor plan (may be None or contain only placeholders).
    # If no anchors are present, proceed with an unconstrained free-TSP
    # sort — the solver's _solve_atsp_with_anchors() already handles
    # anchors=[] natively via its `if not anchors:` branch.
    plan = _load_anchors_file(pl_id)
    n_anchor_entries = sum(1 for e in (plan or []) if e["type"] == "anchor") if plan else 0
    if n_anchor_entries == 0:
        logger.info(
            "No anchors for playlist %s — running unconstrained free-TSP sort "
            "over all %d tracks (optimal path, no anchor pinning)",
            pl_id[:8] if pl_id else "?", len(_playlist_tracks),
        )
        ui.notify(
            "No anchors set — sorting all tracks freely for the optimal order",
            type="info",
        )
        # Deliberately NOT returning — fall through to the solver call below.

    # Validate tracks are loaded
    if not _playlist_tracks:
        ui.notify("No tracks loaded — select a playlist above", type="warning")
        return

    # ── Disable UI, show spinner, auto-expand logs ──────────────────────
    if _start_sort_btn is not None:
        _start_sort_btn.set_enabled(False)
    if _spinner is not None:
        _spinner.set_visibility(True)

    if _logs_expansion is not None:
        _logs_expansion.value = True
    if _logs_content is not None:
        _logs_content.set_text("")

    # Clear previous results
    _clear_results()

    # Build track descs list for the solver
    descs = []
    for t in _playlist_tracks:
        descs.append({
            "track_id": t.get("id", ""),
            "name": t.get("name", "?"),
            "artist": t.get("artist", "?"),
        })

    # Load DB once (cached in solver via _SORTING_CACHE per playlist)
    from playlist_arranger.database import db as _db

    try:
        db_dict = _db.load_all()
    except Exception as exc:
        logger.exception("Failed to load DB for sorting")
        ui.notify(f"Failed to load track database: {exc}", type="negative")
        _restore_ui_after_sorting(failure=True)
        return

    # Read current settings for SA params
    s = load_settings()

    # Clear log queue of any stale messages
    while not _log_queue.empty():
        try:
            _log_queue.get_nowait()
        except queue.Empty:
            break

    # ── Run solver in background thread ──────────────────────────────────
    from playlist_arranger.sorting.solver import _run_smart_sorting

    try:
        ordered_descs, best_cost = await asyncio.to_thread(
            _run_smart_sorting,
            db_dict,
            descs,
            pl_id,
            _playlist_name,
            progress_cb=_enqueue_log,
            settings=s,
        )
    except Exception as exc:
        logger.exception("Solver failed")
        _enqueue_log(f"ERROR: {exc}")
        ui.notify(f"Sorting failed: {exc}", type="negative")
        # Drain logs one last time before leaving section expanded
        _drain_log_queue()
        _restore_ui_after_sorting(failure=True)
        return

    # ── Populate sorted results ──────────────────────────────────────────
    _restore_ui_after_sorting(failure=False)

    # Drain any remaining log lines
    _drain_log_queue()

    # Auto-collapse logs on success
    if _logs_expansion is not None:
        _logs_expansion.value = False

    # ── Build sorted full-track list for URIs + rendering ─────────────────
    # ordered_descs from solver has track_id/name/artist — look up full
    # track dicts in _playlist_tracks to get Spotify URIs.
    sorted_full_tracks = []
    for d in ordered_descs:
        tid = d.get("track_id", "")
        full = next((t for t in _playlist_tracks if t.get("id") == tid), d)
        sorted_full_tracks.append(full)
    _sorted_track_uris = []
    _dropped_no_id = 0
    for t in sorted_full_tracks:
        uri = t.get("uri") or ""
        if not uri.startswith("spotify:track:"):
            tid = t.get("id") or t.get("track_id") or ""
            uri = f"spotify:track:{tid}" if tid else ""
        if uri.startswith("spotify:track:"):
            _sorted_track_uris.append(uri)
        else:
            _dropped_no_id += 1
    if _dropped_no_id:
        logger.warning(
            "%d track(s) dropped from save list — no track_id/uri available",
            _dropped_no_id,
        )

    if _results_container is not None:
        _results_container.clear()
        with _results_container:
            _render_sorted_results(ordered_descs, best_cost)

    _refresh_save_buttons()

    ui.notify(f"Sorting complete — best cost: {best_cost:.4f}", type="positive")


def _restore_ui_after_sorting(*, failure: bool = False):
    """Re-enable the Start Sorting button and hide spinner.

    On failure, leaves the Sorting Logs section expanded (so the user can
    see what happened).  On success, the caller collapses it after rendering
    results instead.
    """
    global _start_sort_btn, _spinner, _logs_expansion
    if _start_sort_btn is not None:
        _start_sort_btn.set_enabled(True)
    if _spinner is not None:
        _spinner.set_visibility(False)
    if failure and _logs_expansion is not None:
        # Leave logs visible on failure so user can see error context
        _logs_expansion.value = True


# ── UI rendering ────────────────────────────────────────────────────────────

def _render_track_list():
    """Paginated (10 rows/page) track list — same columns as Anchors page."""
    if not _playlist_tracks:
        ui.label("No tracks loaded. Select a playlist above.").classes("text-sm text-gray-400 italic")
        return

    rows = build_track_rows(_playlist_tracks, anchored_ids=set())

    columns = [
        {"name": "idx", "label": "#", "field": "idx", "sortable": True},
        {"name": "name", "label": "Track", "field": "name"},
        {"name": "artist", "label": "Artist", "field": "artist"},
        {"name": "duration", "label": "Dur", "field": "duration"},
        {"name": "desc", "label": "Desc", "field": "desc", "sortable": False},
        {"name": "status", "label": "Status", "field": "status"},
    ]

    ui.label(f"Playlist: {_playlist_name} ({len(_playlist_tracks)} tracks)") \
        .classes("text-sm font-semibold mb-1")

    track_table = ui.table(
        columns=columns, rows=rows, row_key="idx",
        pagination={"rowsPerPage": 10},
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

    from playlist_arranger.ui.pages.playlist_source import _on_desc_icon_click
    track_table.on("desc_click", _on_desc_icon_click)


def _render_anchors_list():
    """Read-only anchors list — same visual structure as Anchors page but no edit controls."""
    pl_id = getattr(_state, "selected_playlist_id", None)
    anchor_plan = _load_anchors_file(pl_id) if pl_id else None
    if anchor_plan is None or len(anchor_plan) == 0:
        anchor_plan = [{"type": "placeholder"}]

    title_text = f"Anchors for {_playlist_name}" if _playlist_name else "Anchors"
    ui.label(title_text).classes("text-lg font-bold mb-1 mt-4")

    columns = [
        {"name": "idx", "label": "#", "field": "idx", "sortable": True},
        {"name": "type", "label": "Type", "field": "type"},
        {"name": "info", "label": "Track", "field": "info"},
    ]
    rows = []
    track_by_id = {t["id"]: t for t in _playlist_tracks}
    for i, entry in enumerate(anchor_plan, 1):
        if entry["type"] == "anchor":
            tid = entry.get("track_id", "")
            track = track_by_id.get(tid, {})
            name = track.get("name", "?")[:40]
            artist = track.get("artist", "?")[:30]
            rows.append({"idx": i, "type": "⚓ Anchor", "info": f"{name} — {artist}"})
        else:
            rows.append({"idx": i, "type": "· Placeholder", "info": "— placeholder —"})

    if not rows:
        ui.label("No anchors yet. Switch to the Anchors page to create some.") \
            .classes("text-sm text-gray-400 italic mb-2")
        return

    ui.table(
        columns=columns, rows=rows, row_key="idx", pagination={"rowsPerPage": 0},
    ).classes("w-full").props("dense")


def _render_sorted_results(ordered_descs: list, best_cost: float, ordered_tracks: list | None = None):
    """Render the final sorted track order as an unpaginated read-only table."""
    # Build track list in the solver's order
    sorted_tracks = []
    for i, d in enumerate(ordered_descs):
        tid = d.get("track_id", "")
        # Look up the full track info from _playlist_tracks
        full = next((t for t in _playlist_tracks if t.get("id") == tid), d)
        sorted_tracks.append(full)

    rows = build_track_rows(sorted_tracks, anchored_ids=set())

    columns = [
        {"name": "idx", "label": "#", "field": "idx", "sortable": False},
        {"name": "name", "label": "Track", "field": "name"},
        {"name": "artist", "label": "Artist", "field": "artist"},
        {"name": "duration", "label": "Dur", "field": "duration"},
        {"name": "desc", "label": "Desc", "field": "desc", "sortable": False},
        {"name": "status", "label": "Status", "field": "status"},
    ]

    ui.label(f"Sorted Results — {len(ordered_descs)} tracks (cost: {best_cost:.4f})") \
        .classes("text-lg font-bold mb-2 mt-4")

    ui.table(
        columns=columns, rows=rows, row_key="idx",
        pagination={"rowsPerPage": 0},
    ).classes("w-full").props("dense")

    # ── Save buttons ──────────────────────────────────────────────────────
    global _save_new_btn, _save_current_btn, _save_spinner, _save_section_container
    with ui.row().classes("gap-2 items-center mt-4"):
        _save_new_btn = ui.button(
            "Save into New Playlist",
            on_click=_on_save_new_playlist,
        ).props("color=blue")
        _save_current_btn = ui.button(
            "Save into Current Playlist",
            on_click=_on_save_current_playlist,
        ).props("color=orange")
        _save_spinner = ui.spinner(size="sm")
        _save_spinner.set_visibility(False)
    _refresh_save_buttons()


# ── Page entry point ─────────────────────────────────────────────────────────

def build_smart_sorting():
    """Build the smart sorting page layout:

    1. Playlist selector dropdown
    2. Paginated track list (10 rows/page)
    3. Read-only anchors list
    4. Start Sorting button (disabled until all tracks have OK status)
    5. Sorting Logs expansion (weights + SA summary, live progress)
    6. Sorted results list (populated after successful sort)
    """
    global _sort_select, _track_list_container, _anchors_list_container
    global _start_sort_btn, _start_sort_warning
    global _logs_expansion, _logs_content, _logs_summary
    global _results_container, _spinner
    global _playlist_tracks, _playlist_name, _sorted_track_uris
    global _log_timer
    global _save_new_btn, _save_current_btn, _save_spinner

    ui.label("Sorting").classes("text-2xl font-bold mb-4")

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

    # ── 1. Shared playlist selector ──────────────────────────────────────
    saved_id = getattr(_state, "selected_playlist_id", None)
    default_val = saved_id if saved_id in options else (list(options.keys())[0] if options else None)

    def on_change(e):
        _on_playlist_selected(e.value)

    _sort_select = ui.select(
        label="Select a playlist", options=options, value=default_val, on_change=on_change,
    ).classes("w-80 mb-4")

    # ── 2. Paginated track list ──────────────────────────────────────────
    _track_list_container = ui.column().classes("w-full mb-4")
    with _track_list_container:
        _render_track_list()

    # ── 3. Read-only anchors list ────────────────────────────────────────
    _anchors_list_container = ui.column().classes("w-full mb-2")
    with _anchors_list_container:
        _render_anchors_list()

    # ── 4. Start Sorting button + spinner + warning ──────────────────────
    with ui.row().classes("gap-2 items-center mt-2"):
        _start_sort_btn = ui.button(
            "Start Sorting",
            on_click=_on_start_sorting,
        ).props("color=green")
        _spinner = ui.spinner(size="sm")
        _spinner.set_visibility(False)

    _start_sort_warning = ui.label("").classes("text-sm text-orange-500 mt-1")
    _start_sort_warning.set_visibility(False)

    # ── 5. Sorting Logs expansion ────────────────────────────────────────
    _logs_expansion = ui.expansion("Sorting Logs", value=False).classes("w-full mt-4")
    with _logs_expansion:
        _logs_summary = ui.label("").classes("text-xs font-mono text-gray-600 dark:text-gray-400 whitespace-pre")
        _update_logs_summary()
        ui.separator().classes("my-2")
        _logs_content = ui.label("").classes("text-sm font-mono whitespace-pre-wrap")

    # ── 6. Sorted results container (initially empty) ─────────────────────
    _results_container = ui.column().classes("w-full")

    # ── Timer to drain log queue (runs while sorting is active) ──────────
    _log_timer = ui.timer(0.3, _drain_log_queue)
    # Timer runs continuously — _drain_log_queue is a no-op when queue is
    # empty and _logs_content is set, which it always is after build.

    # ── Evaluate initial button state ────────────────────────────────────
    _refresh_start_button()

    # ── Load data for the initially-selected playlist ────────────────────
    if default_val:
        _on_playlist_selected(default_val)