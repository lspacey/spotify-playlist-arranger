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

# ── Signal-quality thresholds (editable constants) ───────────────────────────
CV_GOOD_THRESHOLD = 0.20
CV_MODERATE_THRESHOLD = 0.10

# ── Module-level UI references ──────────────────────────────────────────────
_sort_select = None
_track_list_container = None
_anchors_list_container = None
_analyze_stats_btn = None
_start_sort_btn = None
_start_sort_warning = None
_analyze_hint: ui.label | None = None
_logs_expansion = None
_logs_content: ui.label | None = None
_logs_summary: ui.label | None = None
_histogram_image: ui.image | None = None
_stats_panel: ui.element | None = None
_stats_summary_line: ui.label | None = None
_results_container = None
_spinner = None
_save_section_container = None
_save_new_btn = None
_save_current_btn = None
_save_spinner = None

# ── Page-local data ─────────────────────────────────────────────────────────
_playlist_tracks: list = []
_playlist_name: str = ""
_sorted_track_uris: list[str] = []
_save_in_progress: bool = False
_stats_result = None  # StatsResult | None — current analysis result
_weight_sliders: dict[str, "ui.number"] = {}
_current_snapshot_id: str = ""

# ── Thread-safe log queue ───────────────────────────────────────────────────
_log_queue: queue.Queue = queue.Queue()
_log_timer: ui.timer | None = None


def _enqueue_log(msg: str) -> None:
    _log_queue.put(msg)


def _drain_log_queue() -> None:
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
    global _playlist_tracks, _playlist_name, _stats_result, _current_snapshot_id

    _state.selected_playlist_id = pl_id

    if not pl_id:
        _playlist_tracks = []
        _playlist_name = ""
        _stats_result = None
        _current_snapshot_id = ""
        _rebuild_all()
        _refresh_buttons()
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
        pl_data = _state.sp.playlist(pl_id, fields="snapshot_id,name")
        _playlist_name = pl_data.get("name", pl_id[:8])
        _current_snapshot_id = pl_data.get("snapshot_id", "")
    except Exception:
        _playlist_name = pl_id[:8]
        _current_snapshot_id = ""

    # ── Load stats cache ─────────────────────────────────────────────────
    _stats_result = None
    if _current_snapshot_id:
        from playlist_arranger.sorting.stats_analysis import load_stats_cache
        _stats_result = load_stats_cache(pl_id, _current_snapshot_id)

    _rebuild_all()
    _refresh_buttons()
    _update_logs_summary()
    _clear_results()
    _render_stats_panel_summary()


def _refresh_save_buttons():
    global _save_new_btn, _save_current_btn, _save_spinner, _save_in_progress
    has_results = len(_sorted_track_uris) > 0
    can_save = has_results and not _save_in_progress
    if _save_new_btn is not None:
        _save_new_btn.set_enabled(can_save)
    if _save_current_btn is not None:
        _save_current_btn.set_enabled(can_save)
    if _save_spinner is not None:
        _save_spinner.set_visibility(_save_in_progress)


def _refresh_buttons():
    """Update Analyze Statistics + Run Sorting button states."""
    global _analyze_stats_btn, _start_sort_btn, _analyze_hint

    # Analyze Statistics: enabled when tracks are loaded
    if _analyze_stats_btn is not None:
        _analyze_stats_btn.set_enabled(len(_playlist_tracks) > 0)

    # Run Sorting: enabled only when BOTH all tracks are OK AND a valid stats cache exists
    all_ok = len(_playlist_tracks) > 0 and all(
        get_track_status(t) == STATUS_OK for t in _playlist_tracks
    )
    has_valid_cache = _stats_result is not None and _stats_result.components.get("flatness") is not None

    if _start_sort_btn is not None:
        _start_sort_btn.set_enabled(all_ok and has_valid_cache)

    if _analyze_hint is not None:
        if all_ok and not has_valid_cache:
            _analyze_hint.set_text("Run Analyze Statistics first")
            _analyze_hint.set_visibility(True)
        elif not all_ok and _playlist_tracks:
            missing = sum(1 for t in _playlist_tracks if get_track_status(t) != STATUS_OK)
            _analyze_hint.set_text(f"{missing} track(s) missing audio features — analyze them first")
            _analyze_hint.set_visibility(True)
        else:
            _analyze_hint.set_visibility(False)


def _error_user_message(err: dict | str | None) -> str:
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
    global _save_in_progress, _sorted_track_uris, _playlist_name
    if not _sorted_track_uris:
        ui.notify("No sorted results to save", type="warning")
        return
    if not _state.sp:
        ui.notify("Spotify not connected", type="warning")
        return
    _save_in_progress = True; _refresh_save_buttons()
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    new_name = f"{_playlist_name}_{ts}" if _playlist_name else f"Sorted_{ts}"
    result = None
    try:
        result = await asyncio.to_thread(create_playlist, _state.sp, new_name, _sorted_track_uris)
    except Exception as exc:
        logger.exception("Failed to create new playlist '%s'", new_name)
        ui.notify(f"Failed to create playlist: {exc}", type="negative")
    else:
        try:
            if result["success"]:
                pl_info = result.get("playlist") or {}
                pl_name = pl_info.get("name", new_name) if isinstance(pl_info, dict) else new_name
                ui.notify(f"Saved to new playlist: {pl_name} ({result['tracks_saved']} tracks)", type="positive")
            else:
                err_msg = _error_user_message(result.get("error"))
                ui.notify(f"Failed to create playlist: {err_msg}", type="negative")
        except Exception:
            logger.exception("UI error rendering save result")
    finally:
        _save_in_progress = False; _refresh_save_buttons()


def _backup_playlist_snapshot(sp, pl_id: str, pl_name: str):
    try:
        tracks = get_playlist_tracks(sp, pl_id)
    except Exception as exc:
        logger.warning("Failed to fetch track list for backup: %s", exc)
        return None
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_id = pl_id[:12].replace("/", "_").replace("\\", "_")
    bk_path = ANCHORS_DIR_DEFAULT / f"backup_{safe_id}_{ts}.json"
    data = {
        "playlist_id": pl_id, "playlist_name": pl_name,
        "saved_at": datetime.datetime.now().isoformat(),
        "tracks": [{"id": t["id"], "name": t["name"], "artist": t["artist"], "uri": t["uri"]} for t in tracks],
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
    global _save_in_progress, _sorted_track_uris, _playlist_name
    if not _sorted_track_uris:
        ui.notify("No sorted results to save", type="warning"); return
    pl_id = getattr(_state, "selected_playlist_id", None)
    if not pl_id:
        ui.notify("No playlist selected", type="warning"); return
    if not _state.sp:
        ui.notify("Spotify not connected", type="warning"); return
    n = len(_sorted_track_uris)

    def _do_overwrite():
        async def _execute():
            global _save_in_progress
            _save_in_progress = True; _refresh_save_buttons()
            result = None
            try:
                bk = _backup_playlist_snapshot(_state.sp, pl_id, _playlist_name)
                if bk is None:
                    ui.notify("Proceeding without backup — could not save playlist snapshot", type="warning")
                result = await asyncio.to_thread(reorder_playlist, _state.sp, pl_id, _sorted_track_uris)
            except Exception as exc:
                logger.exception("Failed to reorder playlist '%s' (%s)", _playlist_name, pl_id[:8])
                ui.notify(f"Failed to reorder playlist: {exc}", type="negative")
            else:
                try:
                    if result["success"]:
                        ui.notify(f"Playlist reordered: {_playlist_name} ({n} tracks)", type="positive")
                    else:
                        err_msg = _error_user_message(result.get("error"))
                        ui.notify(f"Reorder failed: {err_msg}", type="negative")
                except Exception:
                    logger.exception("UI error rendering reorder result")
            finally:
                _save_in_progress = False; _refresh_save_buttons()
        asyncio.ensure_future(_execute())

    with ui.dialog() as confirm_dialog, ui.card():
        ui.label(f"Confirm Reorder").classes("text-lg font-bold mb-2")
        ui.label(f"This will permanently reorder '{_playlist_name}' ({n} tracks) on Spotify. This action cannot be easily undone.").classes("text-sm mb-4")
        with ui.row().classes("gap-2 justify-end"):
            ui.button("Cancel", on_click=confirm_dialog.close).props("flat")
            ui.button("Confirm", on_click=lambda: (confirm_dialog.close(), _do_overwrite())).props("color=red")
    confirm_dialog.open()


def _rebuild_all():
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
    global _results_container, _sorted_track_uris
    _sorted_track_uris = []
    _refresh_save_buttons()
    if _results_container is not None:
        _results_container.clear()


def _update_logs_summary():
    global _logs_summary
    if _logs_summary is None:
        return
    s = load_settings()
    summary_lines = [
        f"Weights: mood={s.w_mood:.2f}, bpm={s.w_bpm:.2f}, transition={s.w_transition:.2f}, key={s.w_key:.2f}, "
        f"energy={s.w_energy:.2f}, texture={s.w_texture:.2f}, freq_balance={s.w_freq_balance:.2f}",
        f"Penalties: artist={s.artist_penalty:.3f}, album={s.album_penalty:.3f}, duration_tolerance={s.duration_tolerance:.3f}",
        f"SA: iterations_multiplier={s.sa_iterations_multiplier}, n_runs={s.sa_n_runs}, T_start={s.sa_T_start:.4g}, T_end={s.sa_T_end:.4g}",
    ]
    _logs_summary.set_text("\n".join(summary_lines))


# ── Analyze Statistics async handler ─────────────────────────────────────────

async def _on_analyze_statistics():
    """Run per-playlist stats analysis in a background thread."""
    global _analyze_stats_btn, _stats_panel, _stats_result, _weight_sliders

    pl_id = getattr(_state, "selected_playlist_id", None)
    if not pl_id:
        ui.notify("No playlist selected", type="warning")
        return
    if not _playlist_tracks:
        ui.notify("No tracks loaded", type="warning")
        return

    # Disable button during analysis
    if _analyze_stats_btn is not None:
        _analyze_stats_btn.set_enabled(False)
    if _spinner is not None:
        _spinner.set_visibility(True)
    if _logs_expansion is not None:
        _logs_expansion.value = True

    # Show progress in logs
    _enqueue_log("Starting per-playlist analysis...")

    try:
        from playlist_arranger.database import db as _db
        from playlist_arranger.sorting.distance import _load_embedding
        from playlist_arranger.sorting.stats_analysis import analyze_playlist_stats

        db_dict = _db.load_all()
        track_ids = [t["id"] for t in _playlist_tracks]
        all_tracks = [db_dict[tid] for tid in track_ids if tid in db_dict]
        embeddings = [_load_embedding(tid, db_dict) for tid in track_ids]
        s = load_settings()
        weights = {
            "mood": s.w_mood, "bpm": s.w_bpm, "transition": s.w_transition,
            "key": s.w_key, "energy": s.w_energy, "texture": s.w_texture,
            "freq_balance": s.w_freq_balance,
        }

        _stats_result = await asyncio.to_thread(
            analyze_playlist_stats,
            pl_id, _current_snapshot_id, all_tracks, embeddings, weights=weights,
        )
    except Exception as exc:
        logger.exception("Stats analysis failed")
        _enqueue_log(f"ERROR: Stats analysis failed: {exc}")
        ui.notify(f"Analysis failed: {exc}", type="negative")
        if _analyze_stats_btn is not None:
            _analyze_stats_btn.set_enabled(True)
        if _spinner is not None:
            _spinner.set_visibility(False)
        _drain_log_queue()
        return

    _drain_log_queue()

    # Render the full stats panel
    _render_full_stats_panel()

    if _analyze_stats_btn is not None:
        _analyze_stats_btn.set_enabled(True)
    if _spinner is not None:
        _spinner.set_visibility(False)


def _render_full_stats_panel():
    """Render weight sliders, CV badges, effective contribution chart, histogram, save button."""
    global _stats_panel, _weight_sliders, _stats_result

    if _stats_panel is not None:
        _stats_panel.clear()
    else:
        return  # should not happen — caller ensures panel exists

    if _stats_result is None:
        return

    sr = _stats_result
    n_tracks = len(_playlist_tracks)

    with _stats_panel:
        ui.label(f"Per-Playlist Analysis — {n_tracks} tracks").classes("text-lg font-bold mb-2")

        # ── Weight sliders with CV badges ──────────────────────────────────
        comp_order = ["mood", "bpm", "transition", "key", "energy", "texture", "freq_balance"]
        default_weights = {
            "mood": 0.48, "bpm": 0.12, "transition": 0.20,
            "key": 0.12, "energy": 0.08, "texture": 0.10, "freq_balance": 0.08,
        }
        cached_w = sr.weights_used if sr.weights_used else default_weights

        _weight_sliders = {}

        for comp_name in comp_order:
            comp = sr.components.get(comp_name)
            cv = comp.observed_cv if comp else 0.0
            if cv > CV_GOOD_THRESHOLD:
                badge_color = "green"; badge_text = "● good signal"
            elif cv >= CV_MODERATE_THRESHOLD:
                badge_color = "orange"; badge_text = "● moderate"
            else:
                badge_color = "red"; badge_text = "● flat"

            with ui.row().classes("gap-4 items-center mb-1"):
                ui.label(f"{comp_name}").classes("text-sm w-24")
                init_val = float(cached_w.get(comp_name, default_weights.get(comp_name, 0.1)))
                slider = ui.number(
                    label=None, value=init_val, min=0.0, max=1.0, step=0.01,
                    format="%.2f",
                ).classes("w-24").props("dense")
                slider.on_value_change(lambda e, cn=comp_name: _on_weight_changed(cn, e.value))
                _weight_sliders[comp_name] = slider
                ui.label(f"CV={cv:.2f}").classes(f"text-xs text-{badge_color}-500 ml-2")

        # ── Effective contribution bar chart ────────────────────────────────
        ui.separator().classes("my-2")
        ui.label("Effective contribution (observed_max × weight):").classes("text-sm font-semibold mb-1")
        _effective_contribution_container = ui.column().classes("w-full")

        def _redraw_contribution():
            _effective_contribution_container.clear()
            with _effective_contribution_container:
                total = 0.0
                items = []
                for comp_name in comp_order:
                    comp = sr.components.get(comp_name)
                    obs_max = comp.observed_max if comp else 0.0
                    w = float(_weight_sliders[comp_name].value) if comp_name in _weight_sliders else 0.0
                    eff = obs_max * w
                    items.append((comp_name, eff))
                    total += eff
                for comp_name, eff in items:
                    pct = (eff / total * 100) if total > 0 else 0
                    bar_width = int(pct * 2)  # scale to ~200px
                    ui.label(
                        f"{comp_name:>14s}  {eff:.4f}  ({pct:5.1f}%)"
                    ).classes("text-xs font-mono")

        _redraw_contribution()

        # ── Histogram (combined multi-panel PNG) ────────────────────────────
        ui.separator().classes("my-2")
        hist_path = _render_composite_histogram()
        if hist_path:
            ui.image(hist_path).classes("max-w-2xl")
        else:
            ui.label("Histogram not available (matplotlib missing?)").classes("text-sm text-gray-500")

        # ── Save & Enable Sorting ───────────────────────────────────────────
        ui.separator().classes("my-2")
        ui.button("Save & Enable Sorting", on_click=_on_save_stats).props("color=blue")


def _render_composite_histogram() -> str | None:
    """Render 7 histograms as one combined 2×4 PNG. Returns path or None."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None

    sr = _stats_result
    if sr is None:
        return None

    pl_id = getattr(_state, "selected_playlist_id", "unknown")
    safe_id = pl_id.replace("/", "_").replace("\\", "_")
    from playlist_arranger.config import CACHE_DIR_DEFAULT
    png_path = CACHE_DIR_DEFAULT / f"{safe_id}_component_hists.png"
    CACHE_DIR_DEFAULT.mkdir(parents=True, exist_ok=True)

    comp_order = ["mood", "bpm", "transition", "key", "energy", "texture", "freq_balance"]
    fig, axes = plt.subplots(2, 4, figsize=(12, 6))
    axes_flat = axes.flatten()

    for idx, comp_name in enumerate(comp_order):
        ax = axes_flat[idx]
        comp = sr.components.get(comp_name)
        if comp and comp.histogram_counts and comp.histogram_edges:
            counts = comp.histogram_counts
            edges = comp.histogram_edges
            ax.bar(edges[:-1], counts, width=np.diff(edges), align="edge",
                   color="#4A90D9", edgecolor="white", alpha=0.85)
        else:
            ax.text(0.5, 0.5, "no data", ha="center", va="center", transform=ax.transAxes,
                    fontsize=9, color="gray")
        ax.set_title(comp_name, fontsize=9)
        ax.tick_params(labelsize=7)

    # Last panel blank
    axes_flat[7].set_visible(False)
    fig.tight_layout()
    fig.savefig(str(png_path), dpi=120)
    plt.close(fig)
    return str(png_path)


def _on_weight_changed(comp_name: str, value: float):
    """Recompute effective contribution display on weight change (debounced in-place)."""
    if _stats_panel is None:
        return
    _render_full_stats_panel()


def _on_save_stats():
    """Save stats cache with current weight values, then enable Run Sorting."""
    global _stats_result, _start_sort_btn, _analyze_hint

    if _stats_result is None:
        ui.notify("No analysis results to save", type="warning")
        return

    # Collect current slider values
    current_weights = {}
    for comp_name, slider in _weight_sliders.items():
        current_weights[comp_name] = float(slider.value)
    _stats_result.weights_used = current_weights

    from playlist_arranger.sorting.stats_analysis import save_stats_cache
    try:
        save_stats_cache(_stats_result)
    except Exception as exc:
        logger.exception("Failed to save stats cache")
        ui.notify(f"Failed to save: {exc}", type="negative")
        return

    ui.notify("Statistics saved, weights locked in — ready to sort", type="positive")
    _refresh_buttons()
    _render_stats_panel_summary()


def _render_stats_panel_summary():
    """Show a compact summary instead of the full panel with sliders."""
    global _stats_panel, _stats_summary_line

    if _stats_panel is not None:
        _stats_panel.clear()

    sr = _stats_result
    if sr is None:
        return

    n_tracks = len(_playlist_tracks)
    # Compute average CV
    all_cvs = [c.observed_cv for c in sr.components.values()
               if c.observed_cv > 0 and c.name in ("mood", "bpm", "transition", "key", "energy", "texture", "freq_balance")]
    avg_cv = sum(all_cvs) / len(all_cvs) if all_cvs else 0.0

    with _stats_panel:
        with ui.row().classes("gap-4 items-center"):
            ui.label(
                f"Analyzed {n_tracks} tracks · weights saved · avg CV {avg_cv:.2f}"
            ).classes("text-sm text-gray-600 dark:text-gray-400")
            ui.button("Re-analyze", on_click=_on_analyze_statistics).props("flat size=sm")


# ── Start Sorting async handler ──────────────────────────────────────────────

async def _on_start_sorting():
    global _start_sort_btn, _spinner, _logs_expansion, _logs_content, _results_container, _sorted_track_uris

    pl_id = getattr(_state, "selected_playlist_id", None)
    if not pl_id:
        ui.notify("No playlist selected", type="warning")
        return

    plan = _load_anchors_file(pl_id)
    n_anchor_entries = sum(1 for e in (plan or []) if e["type"] == "anchor") if plan else 0
    if n_anchor_entries == 0:
        logger.info("No anchors for playlist %s — running unconstrained free-TSP sort", pl_id[:8] if pl_id else "?")
        ui.notify("No anchors set — sorting all tracks freely for the optimal order", type="info")

    if not _playlist_tracks:
        ui.notify("No tracks loaded — select a playlist above", type="warning")
        return

    if _start_sort_btn is not None:
        _start_sort_btn.set_enabled(False)
    if _spinner is not None:
        _spinner.set_visibility(True)
    if _logs_expansion is not None:
        _logs_expansion.value = True
    if _logs_content is not None:
        _logs_content.set_text("")
    global _histogram_image
    if _histogram_image is not None:
        _histogram_image.set_visibility(False)

    _clear_results()

    descs = [{"track_id": t.get("id", ""), "name": t.get("name", "?"), "artist": t.get("artist", "?")}
             for t in _playlist_tracks]

    from playlist_arranger.database import db as _db
    try:
        db_dict = _db.load_all()
    except Exception as exc:
        logger.exception("Failed to load DB for sorting")
        ui.notify(f"Failed to load track database: {exc}", type="negative")
        _restore_ui_after_sorting(failure=True)
        return

    s = load_settings()
    while not _log_queue.empty():
        try:
            _log_queue.get_nowait()
        except queue.Empty:
            break

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
            stats_cache=_stats_result,
            snapshot_id=_current_snapshot_id,
        )
    except Exception as exc:
        logger.exception("Solver failed")
        _enqueue_log(f"ERROR: {exc}")
        ui.notify(f"Sorting failed: {exc}", type="negative")
        _drain_log_queue()
        _restore_ui_after_sorting(failure=True)
        return

    _restore_ui_after_sorting(failure=False)
    _drain_log_queue()

    # Show distance histogram inline
    from playlist_arranger.sorting.solver import get_last_histogram_path
    hist_path = get_last_histogram_path()
    if hist_path and _histogram_image is not None:
        _histogram_image.set_source(hist_path)
        _histogram_image.set_visibility(True)

    _drain_log_queue()
    if _logs_expansion is not None:
        _logs_expansion.value = False

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
        logger.warning("%d track(s) dropped from save list — no track_id/uri available", _dropped_no_id)

    if _results_container is not None:
        _results_container.clear()
        with _results_container:
            _render_sorted_results(ordered_descs, best_cost)

    _refresh_save_buttons()
    ui.notify(f"Sorting complete — best cost: {best_cost:.4f}", type="positive")


def _restore_ui_after_sorting(*, failure: bool = False):
    global _start_sort_btn, _spinner, _logs_expansion
    if _start_sort_btn is not None:
        _start_sort_btn.set_enabled(True)
    if _spinner is not None:
        _spinner.set_visibility(False)
    if failure and _logs_expansion is not None:
        _logs_expansion.value = True


# ── UI rendering ────────────────────────────────────────────────────────────

def _render_track_list():
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
    ui.label(f"Playlist: {_playlist_name} ({len(_playlist_tracks)} tracks)").classes("text-sm font-semibold mb-1")
    track_table = ui.table(columns=columns, rows=rows, row_key="idx", pagination={"rowsPerPage": 10}).classes("w-full").props("dense")
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
        ui.label("No anchors yet. Switch to the Anchors page to create some.").classes("text-sm text-gray-400 italic mb-2")
        return
    ui.table(columns=columns, rows=rows, row_key="idx", pagination={"rowsPerPage": 0}).classes("w-full").props("dense")


def _render_sorted_results(ordered_descs: list, best_cost: float, ordered_tracks: list | None = None):
    sorted_tracks = []
    for d in ordered_descs:
        tid = d.get("track_id", "")
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
    ui.label(f"Sorted Results — {len(ordered_descs)} tracks (cost: {best_cost:.4f})").classes("text-lg font-bold mb-2 mt-4")
    ui.table(columns=columns, rows=rows, row_key="idx", pagination={"rowsPerPage": 0}).classes("w-full").props("dense")
    global _save_new_btn, _save_current_btn, _save_spinner, _save_section_container
    with ui.row().classes("gap-2 items-center mt-4"):
        _save_new_btn = ui.button("Save into New Playlist", on_click=_on_save_new_playlist).props("color=blue")
        _save_current_btn = ui.button("Save into Current Playlist", on_click=_on_save_current_playlist).props("color=orange")
        _save_spinner = ui.spinner(size="sm")
        _save_spinner.set_visibility(False)
    _refresh_save_buttons()


# ── Page entry point ─────────────────────────────────────────────────────────

def build_smart_sorting():
    global _sort_select, _track_list_container, _anchors_list_container
    global _analyze_stats_btn, _start_sort_btn, _start_sort_warning, _analyze_hint
    global _logs_expansion, _logs_content, _logs_summary, _histogram_image, _stats_panel
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

    # ── 4. Analyze Statistics + Run Sorting buttons ───────────────────────
    with ui.row().classes("gap-2 items-center mt-2"):
        _analyze_stats_btn = ui.button(
            "Analyze Statistics",
            on_click=_on_analyze_statistics,
        ).props("color=teal")
        _start_sort_btn = ui.button(
            "Run Sorting",
            on_click=_on_start_sorting,
        ).props("color=green")
        _spinner = ui.spinner(size="sm")
        _spinner.set_visibility(False)

    _analyze_hint = ui.label("").classes("text-sm text-orange-500 mt-1")
    _analyze_hint.set_visibility(False)

    # ── 5. Sorting Logs expansion ────────────────────────────────────────
    _logs_expansion = ui.expansion("Sorting Logs", value=False).classes("w-full mt-4")
    with _logs_expansion:
        _logs_summary = ui.label("").classes("text-xs font-mono text-gray-600 dark:text-gray-400 whitespace-pre")
        _update_logs_summary()
        ui.separator().classes("my-2")
        with ui.row().classes("gap-4 items-start"):
            _logs_content = ui.label("").classes("text-sm font-mono whitespace-pre-wrap")
            _histogram_image = ui.image("").classes("max-w-md")
            _histogram_image.set_visibility(False)

        # Stats panel (below logs + histogram)
        _stats_panel = ui.column().classes("w-full")

    # ── 6. Sorted results container ─────────────────────────────────────
    _results_container = ui.column().classes("w-full")

    # ── Timer ────────────────────────────────────────────────────────────
    _log_timer = ui.timer(0.3, _drain_log_queue)

    _refresh_buttons()
    if default_val:
        _on_playlist_selected(default_val)