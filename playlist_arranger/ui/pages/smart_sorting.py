"""Smart sorting page — playlist dropdown, paginated track list, read-only anchors,
Start Sorting button with ATSP+SA solver, thread-safe progress logs, and sorted results.
"""

import asyncio
import datetime
import logging
import queue

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
from playlist_arranger.config import ANCHORS_DIR_DEFAULT, CACHE_DIR_DEFAULT
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
_analyze_hint: ui.label | None = None
_logs_expansion = None
_logs_content: ui.label | None = None
_logs_summary: ui.label | None = None
_histogram_image: ui.image | None = None
_stats_panel: ui.element | None = None
_results_container = None
_spinner = None
_save_new_btn = None
_save_current_btn = None
_save_spinner = None
_insert_last_btn = None
_insert_n_input: "ui.number | None" = None

# ── Page-local data ─────────────────────────────────────────────────────────
_playlist_tracks: list = []
_playlist_name: str = ""
_sorted_track_uris: list[str] = []
_save_in_progress: bool = False
_stats_result = None  # StatsResult | None — current analysis result
_weight_sliders: dict[str, "ui.number"] = {}
_adv_inputs: dict[str, "ui.number"] = {}
_eff_bar_containers: dict[str, "ui.element"] = {}
_obs_max_cache: dict[str, float] = {}
_comp_order: list[str] = []
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
    for btn in (_save_new_btn, _save_current_btn):
        if btn is None:
            continue
        try:
            btn.set_enabled(can_save)
        except RuntimeError:
            logger.debug("Save button already deleted — skipping set_enabled")
        except Exception:
            logger.exception("Unexpected error in _refresh_save_buttons set_enabled")
    if _save_spinner is not None:
        try:
            _save_spinner.set_visibility(_save_in_progress)
        except RuntimeError:
            logger.debug("Save spinner already deleted — skipping set_visibility")
        except Exception:
            logger.exception("Unexpected error in _refresh_save_buttons spinner visibility")


def _validate_insert_n() -> bool:
    """Return True if the N input value is a strictly positive integer."""
    global _insert_n_input
    if _insert_n_input is None:
        return False
    try:
        val = _insert_n_input.value
    except Exception:
        return False
    if val is None:
        return False
    try:
        n = int(val)
    except (ValueError, TypeError):
        return False
    return n > 0 and float(val) == float(n)


def _refresh_buttons():
    """Update Analyze Statistics + Run Sorting + Insert N Last Tracks button states.

    All UI-element accesses are guarded with try/except RuntimeError to survive
    stale references to deleted elements from a previous page instance.
    """
    global _analyze_stats_btn, _start_sort_btn, _analyze_hint
    global _insert_last_btn, _insert_n_input

    def _safe_set_enabled(btn, enabled: bool, label: str = "") -> None:
        if btn is None:
            return
        try:
            btn.set_enabled(enabled)
        except RuntimeError:
            logger.debug("UI element %s already deleted — skipping set_enabled", label)
        except Exception:
            logger.exception("Unexpected error in _refresh_buttons set_enabled for %s", label)

    def _safe_set_visibility(el, visible: bool, label: str = "") -> None:
        if el is None:
            return
        try:
            el.set_visibility(visible)
        except RuntimeError:
            logger.debug("UI element %s already deleted — skipping set_visibility", label)
        except Exception:
            logger.exception("Unexpected error in _refresh_buttons set_visibility for %s", label)

    def _safe_set_text(el, text: str, label: str = "") -> None:
        if el is None:
            return
        try:
            el.set_text(text)
        except RuntimeError:
            logger.debug("UI element %s already deleted — skipping set_text", label)
        except Exception:
            logger.exception("Unexpected error in _refresh_buttons set_text for %s", label)

    # Analyze Statistics: enabled when tracks are loaded
    _safe_set_enabled(_analyze_stats_btn, len(_playlist_tracks) > 0, "analyze_stats_btn")

    # Run Sorting: enabled only when BOTH all tracks are OK AND a valid stats cache exists
    all_ok = len(_playlist_tracks) > 0 and all(
        get_track_status(t) == STATUS_OK for t in _playlist_tracks
    )
    has_valid_cache = _stats_result is not None and _stats_result.components.get("flatness") is not None

    _safe_set_enabled(_start_sort_btn, all_ok and has_valid_cache, "start_sort_btn")

    # Insert N Last Tracks: base preconditions (tracks loaded + valid cache)
    insert_base_ok = len(_playlist_tracks) > 0 and has_valid_cache
    insert_n_ok = _validate_insert_n()
    _safe_set_enabled(_insert_last_btn, insert_base_ok and insert_n_ok, "insert_last_btn")

    if _analyze_hint is not None:
        if all_ok and not has_valid_cache:
            _safe_set_text(_analyze_hint, "Run Analyze Statistics first", "analyze_hint")
            _safe_set_visibility(_analyze_hint, True, "analyze_hint")
        elif not all_ok and _playlist_tracks:
            missing = sum(1 for t in _playlist_tracks if get_track_status(t) != STATUS_OK)
            _safe_set_text(_analyze_hint, f"{missing} track(s) missing audio features — analyze them first", "analyze_hint")
            _safe_set_visibility(_analyze_hint, True, "analyze_hint")
        else:
            _safe_set_visibility(_analyze_hint, False, "analyze_hint")


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
    client = ui.context.client
    _save_in_progress = True; _refresh_save_buttons()
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    new_name = f"{_playlist_name}_{ts}" if _playlist_name else f"Sorted_{ts}"
    result = None
    try:
        result = await asyncio.to_thread(create_playlist, _state.sp, new_name, _sorted_track_uris)
    except Exception as exc:
        logger.exception("Failed to create new playlist '%s'", new_name)
        with client:
            ui.notify(f"Failed to create playlist: {exc}", type="negative")
    else:
        with client:
            try:
                if result["success"]:
                    pl_info = result.get("playlist") or {}
                    pl_name = pl_info.get("name", new_name) if isinstance(pl_info, dict) else new_name
                    ui.notify(f"Saved to new playlist: {pl_name} ({result['tracks_saved']} tracks)", type="positive")
                    await _refresh_playlist_dropdown()
                else:
                    err_msg = _error_user_message(result.get("error"))
                    ui.notify(f"Failed to create playlist: {err_msg}", type="negative")
            except Exception:
                logger.exception("UI error rendering save result")
    finally:
        _save_in_progress = False
        with client:
            _refresh_save_buttons()


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

    client = ui.context.client

    def _do_overwrite():
        async def _execute():
            global _save_in_progress
            _save_in_progress = True
            with client:
                _refresh_save_buttons()
            result = None
            try:
                bk = _backup_playlist_snapshot(_state.sp, pl_id, _playlist_name)
                if bk is None:
                    with client:
                        ui.notify("Proceeding without backup — could not save playlist snapshot", type="warning")
                result = await asyncio.to_thread(reorder_playlist, _state.sp, pl_id, _sorted_track_uris)
            except Exception as exc:
                logger.exception("Failed to reorder playlist '%s' (%s)", _playlist_name, pl_id[:8])
                with client:
                    ui.notify(f"Failed to reorder playlist: {exc}", type="negative")
            else:
                with client:
                    try:
                        if result["success"]:
                            ui.notify(f"Playlist reordered: {_playlist_name} ({n} tracks)", type="positive")
                            await _refresh_current_playlist_tracks()
                        else:
                            err_msg = _error_user_message(result.get("error"))
                            ui.notify(f"Reorder failed: {err_msg}", type="negative")
                    except Exception:
                        logger.exception("UI error rendering reorder result")
            finally:
                _save_in_progress = False
                with client:
                    _refresh_save_buttons()
        asyncio.ensure_future(_execute())

    with ui.dialog() as confirm_dialog, ui.card():
        ui.label(f"Confirm Reorder").classes("text-lg font-bold mb-2")
        ui.label(f"This will permanently reorder '{_playlist_name}' ({n} tracks) on Spotify. This action cannot be easily undone.").classes("text-sm mb-4")
        with ui.row().classes("gap-2 justify-end"):
            ui.button("Cancel", on_click=confirm_dialog.close).props("flat")
            ui.button("Confirm", on_click=lambda: (confirm_dialog.close(), _do_overwrite())).props("color=red")
    confirm_dialog.open()


async def _refresh_playlist_dropdown():
    """Re-fetch user playlists and update the dropdown options in place.

    Called after successfully creating a new playlist via Save as New Playlist.
    Preserves the currently selected playlist value.
    """
    global _sort_select
    if _sort_select is None:
        return
    try:
        if not _state.sp or not _state.spotify_user_id:
            return
        playlists = await asyncio.to_thread(get_own_playlists, _state.sp, _state.spotify_user_id)
        options = {pl["id"]: pl["name"] for pl in playlists}
        current_value = _sort_select.value
        _sort_select.set_options(options)
        if current_value and current_value in options:
            _sort_select.value = current_value
    except Exception:
        logger.exception("Failed to refresh playlist dropdown after save-as-new")


async def _refresh_current_playlist_tracks():
    """Re-fetch this playlist's tracks from Spotify after overwrite.

    Called after a successful reorder_playlist in Save/Overwrite Current Playlist.
    Updates the local track list, writes the cache file with the new snapshot_id,
    and triggers a page redraw so the on-page track list reflects the new order
    immediately.
    """
    global _playlist_tracks, _current_snapshot_id

    pl_id = getattr(_state, "selected_playlist_id", None)
    if not pl_id:
        return
    try:
        tracks = await asyncio.to_thread(get_playlist_tracks, _state.sp, pl_id)
        if not tracks:
            logger.warning("get_playlist_tracks returned empty list for %s after reorder", pl_id[:8])
            return
        _playlist_tracks = tracks

        # Update snapshot_id and write cache
        try:
            pl_data = _state.sp.playlist(pl_id, fields="snapshot_id,name")
            _current_snapshot_id = pl_data.get("snapshot_id", "")
        except Exception:
            logger.exception("Failed to fetch snapshot_id after reorder for %s", pl_id[:8])
            _current_snapshot_id = ""

        if _current_snapshot_id:
            cache_file = CACHE_DIR_DEFAULT / f"{pl_id}-{_current_snapshot_id}.tracks.json"
            try:
                atomic_write_json(cache_file, tracks)
            except Exception:
                logger.exception("Failed to write track cache after reorder for %s", pl_id[:8])

        _rebuild_all()
    except Exception:
        logger.exception("Failed to refresh playlist tracks after reorder")


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


def _update_logs_summary(*, weights: dict[str, float] | None = None) -> None:
    """Refresh the summary line from current weights/cache/settings.

    If *weights* is None, uses ``_build_weights_from_sliders_or_cache()``
    which resolves slider values > cache > settings.json.
    """
    global _logs_summary
    if _logs_summary is None:
        return
    if weights is None:
        weights = _build_weights_from_sliders_or_cache()
    params = _build_penalties_and_sa_from_cache_or_settings()
    summary_lines = [
        f"Weights: mood={weights.get('mood', 0):.2f}, bpm={weights.get('bpm', 0):.2f}, "
        f"transition={weights.get('transition', 0):.2f}, key={weights.get('key', 0):.2f}, "
        f"energy={weights.get('energy', 0):.2f}, texture={weights.get('texture', 0):.2f}, "
        f"freq_balance={weights.get('freq_balance', 0):.2f}",
        f"Penalties: artist={params.get('artist_penalty', 0):.3f}, "
        f"album={params.get('album_penalty', 0):.3f}, "
        f"duration_tolerance={params.get('duration_tolerance', 0):.3f}",
        f"SA: iterations_multiplier={params.get('iterations_multiplier', 0)}, "
        f"n_runs={params.get('n_runs', 0)}, T_start={params.get('T_start', 0):.4g}, "
        f"T_end={params.get('T_end', 0):.4g}",
    ]
    _logs_summary.set_text("\n".join(summary_lines))


# ── Analyze Statistics async handler ─────────────────────────────────────────

def _build_weights_from_sliders_or_cache() -> dict[str, float]:
    """Return current weights: slider values if sliders exist, else cache, else settings.json defaults.

    This is the SINGLE source of truth for "what weights are currently in effect"
    — used by summary line rendering (B.2) and analysis initialization (B.3).
    """
    if _weight_sliders:
        return {cn: float(slider.value) for cn, slider in _weight_sliders.items()}

    if _stats_result is not None and _stats_result.weights_used:
        return dict(_stats_result.weights_used)

    s = load_settings()
    return {
        "mood": s.w_mood, "bpm": s.w_bpm, "transition": s.w_transition,
        "key": s.w_key, "energy": s.w_energy, "texture": s.w_texture,
        "freq_balance": s.w_freq_balance,
    }


def _build_penalties_and_sa_from_cache_or_settings() -> dict:
    """Return penalties and SA params from cache if available, else settings.json.

    Cache precedence rule (same as weights): if a saved stats cache exists
    with cached params, use those. Otherwise fall back to settings.json.
    """
    if _stats_result is not None and hasattr(_stats_result, "penalties_and_sa_params") and _stats_result.penalties_and_sa_params:
        return dict(_stats_result.penalties_and_sa_params)

    s = load_settings()
    return {
        "artist_penalty": s.artist_penalty,
        "album_penalty": s.album_penalty,
        "duration_tolerance": s.duration_tolerance,
        "iterations_multiplier": s.sa_iterations_multiplier,
        "n_runs": s.sa_n_runs,
        "T_start": s.sa_T_start,
        "T_end": s.sa_T_end,
    }


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

    # ── B.3 fix: check cache FIRST for weights, fall back to settings ─────
    # Try loading existing cache to preserve user-tuned weights on Re-analyze
    existing_cache = None
    if _current_snapshot_id:
        try:
            from playlist_arranger.sorting.stats_analysis import load_stats_cache
            existing_cache = load_stats_cache(pl_id, _current_snapshot_id)
        except Exception:
            logger.debug("No existing stats cache for %s", pl_id[:8])

    if existing_cache is not None and existing_cache.weights_used:
        weights = dict(existing_cache.weights_used)
    else:
        s = load_settings()
        weights = {
            "mood": s.w_mood, "bpm": s.w_bpm, "transition": s.w_transition,
            "key": s.w_key, "energy": s.w_energy, "texture": s.w_texture,
            "freq_balance": s.w_freq_balance,
        }

    try:
        from playlist_arranger.database import db as _db
        from playlist_arranger.sorting.distance import _load_embedding
        from playlist_arranger.sorting.stats_analysis import analyze_playlist_stats

        db_dict = _db.load_all()
        track_ids = [t["id"] for t in _playlist_tracks]
        all_tracks = [db_dict[tid] for tid in track_ids if tid in db_dict]
        embeddings = [_load_embedding(tid, db_dict) for tid in track_ids]

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

    # ── Preserve existing cache's penalties_and_sa_params on re-analyze ──
    # (Same cache-over-settings precedence as weights — see B.3 fix above)
    if existing_cache is not None and existing_cache.penalties_and_sa_params:
        _stats_result.penalties_and_sa_params = dict(existing_cache.penalties_and_sa_params)

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

        # ── CV explanation ─────────────────────────────────────────────────
        with ui.row().classes("gap-2 items-center mb-2"):
            ui.icon("info", size="18px").props("color=grey")
            with ui.tooltip().classes("text-xs max-w-md whitespace-pre-wrap"):
                ui.label(
                    "CV (coefficient of variation) = std / mean of this component's "
                    "distances across all track pairs in this playlist.\n\n"
                    "Higher CV means more variation between tracks — the algorithm "
                    "has clearer signal to distinguish them on this dimension.\n"
                    "Low CV means tracks are similar on this dimension regardless of "
                    "the configured weight — increasing the weight won't help much if "
                    "CV is low.\n\n"
                    "● green: CV > 0.20 — good signal   |   "
                    "● yellow: 0.10–0.20 — moderate   |   "
                    "● red: CV < 0.10 — flat, weight has little effect"
                )

        # ── Weight sliders + CV + effective contribution (one row each) ────
        comp_order = ["mood", "bpm", "transition", "key", "energy", "texture", "freq_balance"]
        from playlist_arranger.config import WEIGHTS as _cfg_weights
        cached_w = sr.weights_used if sr.weights_used else _cfg_weights

        global _eff_bar_containers, _obs_max_cache, _comp_order
        _comp_order = comp_order
        _weight_sliders = {}

        # pre-compute observed_max for each component
        _obs_max_cache = {}
        for comp_name in comp_order:
            comp = sr.components.get(comp_name)
            _obs_max_cache[comp_name] = comp.observed_max if comp else 0.0

        # containers for each component's contribution bar (re-rendered live)
        _eff_bar_containers = {}

        # ── Component descriptions (verified against distance.py) ──────────
        _comp_descriptions = {
            "mood": "Overall emotional/timbral similarity between tracks (via audio embeddings or chroma). "
                    "Higher weight prioritizes smooth mood transitions over other factors.",
            "bpm": "Tempo difference between consecutive tracks. "
                   "Higher weight keeps tempo changes gradual; lower weight allows tempo jumps if other factors align better.",
            "transition": "Timbral/textural similarity at track boundaries (via MFCC). "
                          "Higher weight favors tracks that blend smoothly into each other at the mix point.",
            "key": "Harmonic (Camelot wheel) compatibility between tracks. "
                   "Higher weight enforces harmonic mixing — tracks in compatible keys are placed adjacent.",
            "energy": "Loudness/intensity difference between tracks. "
                      "Higher weight smooths energy transitions; lower weight allows more dramatic loud/quiet contrasts.",
            "texture": "Combined harmonic ratio, spectral flatness, dynamic range, and onset strength similarity. "
                       "Higher weight groups tracks with similar production texture together.",
            "freq_balance": "Similarity in bass/mid/high frequency distribution. "
                            "Higher weight keeps the frequency 'shape' of the mix consistent across the playlist.",
        }

        for comp_name in comp_order:
            comp = sr.components.get(comp_name)
            cv = comp.observed_cv if comp else 0.0
            if cv > CV_GOOD_THRESHOLD:
                badge_color = "green"; badge_text = "● good"
            elif cv >= CV_MODERATE_THRESHOLD:
                badge_color = "orange"; badge_text = "● moderate"
            else:
                badge_color = "red"; badge_text = "● flat"

            with ui.column().classes("mb-2 w-full"):
                with ui.row().classes("gap-3 items-center w-full"):
                    # Component name
                    ui.label(f"{comp_name}").classes("text-sm w-20")

                    # Weight slider
                    init_val = float(cached_w.get(comp_name, _cfg_weights.get(comp_name, 0.1)))
                    slider = ui.number(
                        label=None, value=init_val, min=0.0, max=1.0, step=0.01,
                        format="%.2f",
                    ).classes("w-20").props("dense")
                    slider.on_value_change(lambda e, cn=comp_name: _on_weight_changed(cn, e.value))
                    _weight_sliders[comp_name] = slider

                    # CV badge
                    ui.label(f"CV={cv:.2f} {badge_text}").classes(f"text-xs text-{badge_color}-500 w-28")

                    # Effective contribution (live-updated)
                    container = ui.column().classes("flex-grow")
                    _eff_bar_containers[comp_name] = container

                # Description caption (muted, below the main row)
                desc = _comp_descriptions.get(comp_name, "")
                ui.label(desc).classes("text-xs text-gray-500 dark:text-gray-500 ml-24")

        # ── Render ALL effective contribution bars AFTER all sliders exist ──
        # (B.1 fix: computing one bar before others' sliders exist causes 100%)
        _redraw_all_eff_bars()

        # ── Histogram (combined single-row 7-panel PNG) ─────────────────────
        ui.separator().classes("my-2")
        hist_path = _render_composite_histogram()
        if hist_path:
            ui.image(hist_path).classes("max-w-full")
            # Caption explaining histograms
            with ui.row().classes("gap-2 items-start mt-1"):
                ui.icon("info", size="16px").props("color=grey")
                with ui.tooltip().classes("text-xs max-w-md whitespace-pre-wrap"):
                    ui.label(
                        "How to read these histograms:\n"
                        "Each shows the distribution of pairwise distances for that component "
                        "across all tracks (before calibration/weighting).\n\n"
                        "• Concentrated near zero with a right tail → most track pairs are "
                        "similar, with a few outliers — good for smooth transitions.\n"
                        "• Wide spread or multi-peaked → tracks vary a lot — can create more "
                        "dramatic contrasts.\n"
                        "• Bimodal → the playlist may have two distinct sub-groups on that "
                        "dimension.\n\n"
                        "Note: 'key' is naturally discrete (12 Camelot wheel positions), so "
                        "its histogram looks like a bar chart of evenly-spaced values — "
                        "unlike the other 6 continuous components."
                    )
        else:
            ui.label("Histogram not available (matplotlib missing?)").classes("text-sm text-gray-500")

        # ── Advanced: Penalties & Annealing Parameters (collapsible) ────────
        with ui.expansion("Advanced: Penalties & Annealing Parameters", value=False).classes("w-full mt-2"):
            _penalty_params = _build_penalties_and_sa_from_cache_or_settings()
            _adv_descriptions = {
                "artist_penalty": "Extra distance added when two tracks share the same artist. "
                                  "Higher value spreads out an artist's tracks more across the playlist.",
                "album_penalty": "Extra distance added when two tracks share the same album. "
                                 "Higher value spreads out same-album tracks more.",
                "duration_tolerance": "THRESHOLD (not a scaling factor): relative duration difference below "
                                      "this value incurs ZERO penalty. Only when two consecutive tracks differ "
                                      "in duration by MORE than this fraction is a penalty applied "
                                      "(penalty = min(rel_diff × 0.5, 1.0) × 0.10). "
                                      "Higher tolerance = penalty only kicks in for LARGER duration gaps.",
                "iterations_multiplier": "Controls how many SA iterations run per restart, "
                                         "scaled by track count (iterations = track_count × this value). "
                                         "Higher = slower but more thorough search.",
                "n_runs": "Number of independent SA restarts with different random starting orders. "
                          "Higher = more likely to find the global optimum, but slower.",
                "T_start": "Simulated annealing starting temperature. "
                           "Higher values allow the algorithm to explore worse solutions early on.",
                "T_end": "Simulated annealing ending temperature. "
                         "As temperature cools to this value, the algorithm settles into a fixed order. "
                         "Rarely needs tuning.",
            }

            with ui.row().classes("gap-4 items-start flex-wrap"):
                # Penalties column
                with ui.column().classes("gap-1"):
                    ui.label("Penalties").classes("text-sm font-bold")
                    for key in ["artist_penalty", "album_penalty", "duration_tolerance"]:
                        init_val = float(_penalty_params.get(key, 0))
                        with ui.row().classes("gap-2 items-center"):
                            inp = ui.number(
                                label=key.replace("_", " ").title(),
                                value=init_val, min=0.0, max=2.0, step=0.01,
                                format="%.3f" if key == "duration_tolerance" else "%.2f",
                            ).classes("w-36").props("dense")
                            _adv_inputs[key] = inp
                            with ui.tooltip().classes("text-xs max-w-sm"):
                                ui.label(_adv_descriptions.get(key, ""))
                # SA params column
                with ui.column().classes("gap-1"):
                    ui.label("Simulated Annealing").classes("text-sm font-bold")
                    for key in ["iterations_multiplier", "n_runs", "T_start", "T_end"]:
                        init_val = float(_penalty_params.get(key, 0))
                        if key == "T_end":
                            step, fmt, vmin, vmax = 1e-4, "%.0e", 1e-8, 1e-2
                        elif key == "T_start":
                            step, fmt, vmin, vmax = 0.1, "%.1f", 0.1, 10.0
                        elif key == "n_runs":
                            step, fmt, vmin, vmax = 1, "%.0f", 10, 500
                        else:
                            step, fmt, vmin, vmax = 1, "%.0f", 100, 2000
                        with ui.row().classes("gap-2 items-center"):
                            inp = ui.number(
                                label=key.replace("_", " ").title(),
                                value=init_val, min=vmin, max=vmax, step=step,
                                format=fmt,
                            ).classes("w-44").props("dense")
                            _adv_inputs[key] = inp
                            with ui.tooltip().classes("text-xs max-w-sm"):
                                ui.label(_adv_descriptions.get(key, ""))

        # ── Save & Enable Sorting ───────────────────────────────────────────
        ui.separator().classes("my-2")
        ui.button("Save & Enable Sorting", on_click=_on_save_stats).props("color=blue")


def _render_composite_histogram() -> str | None:
    """Render 7 histograms as one combined 2×4 PNG. Returns path or None."""
    try:
        import numpy as np
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
    fig, axes = plt.subplots(1, 7, figsize=(14, 2.2))
    # axes is a 1D array when nrows=1
    axes_flat = axes if hasattr(axes, "__len__") else [axes]

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
                    fontsize=8, color="gray")
        ax.set_title(comp_name, fontsize=8)
        ax.tick_params(labelsize=6)

    fig.tight_layout()
    fig.savefig(str(png_path), dpi=120)
    plt.close(fig)
    return str(png_path)


def _redraw_one_eff_bar(comp_name: str):
    """Recompute + re-render the effective-contribution bar for ONE component."""
    if comp_name not in _eff_bar_containers:
        return
    container = _eff_bar_containers[comp_name]
    if container is None:
        return
    container.clear()

    # compute effective contribution for ALL components (needed for %)
    total = 0.0
    effs = {}
    for cn in _comp_order:
        w = float(_weight_sliders[cn].value) if cn in _weight_sliders else 0.0
        effs[cn] = _obs_max_cache.get(cn, 0.0) * w
        total += effs[cn]

    eff = effs.get(comp_name, 0.0)
    pct = (eff / total * 100) if total > 0 else 0
    bar_width = max(int(pct * 1.5), 2)  # scale to ~150px max, min 2px visibility

    with container:
        with ui.row().classes("gap-2 items-center no-wrap"):
            ui.label(f"{eff:.4f}").classes("text-xs font-mono w-16 text-right")
            ui.label(f"({pct:5.1f}%)").classes("text-xs font-mono w-16")
            ui.html(
                f'<div style="background:#4A90D9;height:8px;width:{bar_width}px;'
                f'border-radius:2px;"></div>'
            ).classes("self-center")


def _redraw_all_eff_bars():
    """Re-render all effective-contribution bars (called on any weight change)."""
    for cn in _comp_order:
        _redraw_one_eff_bar(cn)


# ── Debounce: avoid re-rendering on every .01 step during slider drag ────────
_debounce_timers: dict[str, "ui.timer | None"] = {}


def _on_weight_changed(comp_name: str, value: float):
    """Live-update effective contribution bars on weight slider change."""
    global _debounce_timers
    if _stats_panel is None:
        return

    # Cancel any existing debounce timer for this component
    old_timer = _debounce_timers.get(comp_name)
    if old_timer is not None:
        try:
            old_timer.deactivate()
            old_timer.delete()
        except Exception:
            pass

    # Debounce: re-render after 200ms of no changes — also update summary line (B.2)
    def _on_debounce(cm: str):
        _redraw_all_eff_bars()
        _update_logs_summary()
    timer = ui.timer(0.2, lambda cn=comp_name: _on_debounce(cn), once=True)
    _debounce_timers[comp_name] = timer


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

    # Collect current advanced param values from UI inputs
    # n_runs and iterations_multiplier must be int, not float (range() requires int)
    if _adv_inputs:
        current_params = {}
        for key, inp in _adv_inputs.items():
            val = float(inp.value)
            if key in ("iterations_multiplier", "n_runs"):
                val = int(val)
            current_params[key] = val
        _stats_result.penalties_and_sa_params = current_params

    from playlist_arranger.sorting.stats_analysis import save_stats_cache
    try:
        save_stats_cache(_stats_result)
    except Exception as exc:
        logger.exception("Failed to save stats cache")
        ui.notify(f"Failed to save: {exc}", type="negative")
        return

    ui.notify("Statistics saved, weights locked in — ready to sort", type="positive")
    _update_logs_summary()  # B.2: refresh summary after save
    _refresh_buttons()
    _render_stats_panel_summary()


def _render_stats_panel_summary():
    """Show a compact summary instead of the full panel with sliders."""
    global _stats_panel

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
    global _start_sort_btn, _spinner, _logs_expansion, _insert_last_btn
    if _start_sort_btn is not None:
        _start_sort_btn.set_enabled(True)
    if _spinner is not None:
        _spinner.set_visibility(False)
    if failure and _logs_expansion is not None:
        _logs_expansion.value = True
    # Re-validate insert button after sort completes
    _refresh_buttons()


# ── Insert N Last Tracks async handler ──────────────────────────────────────

async def _on_insert_last_n():
    """Run greedy insertion of the last N tracks in a background thread."""
    global _insert_last_btn, _insert_n_input, _spinner, _sorted_track_uris
    global _results_container

    pl_id = getattr(_state, "selected_playlist_id", None)
    if not pl_id:
        ui.notify("No playlist selected", type="warning")
        return
    if not _playlist_tracks:
        ui.notify("No tracks loaded", type="warning")
        return

    # Defensive validation (button should already be disabled otherwise)
    if not _validate_insert_n():
        ui.notify("N must be a positive integer", type="warning")
        return
    n = int(_insert_n_input.value)

    if _insert_last_btn is not None:
        _insert_last_btn.set_enabled(False)
    if _spinner is not None:
        _spinner.set_visibility(True)

    _clear_results()

    from playlist_arranger.database import db as _db
    try:
        db_dict = _db.load_all()
    except Exception as exc:
        logger.exception("Failed to load DB for insert")
        ui.notify(f"Failed to load track database: {exc}", type="negative")
        if _insert_last_btn is not None:
            _insert_last_btn.set_enabled(True)
        if _spinner is not None:
            _spinner.set_visibility(False)
        return

    from playlist_arranger.sorting.insert import insert_last_n_tracks

    try:
        result_tracks, total_cost = await asyncio.to_thread(
            insert_last_n_tracks,
            _playlist_tracks,
            n,
            db_dict,
            _stats_result,
        )
    except Exception as exc:
        logger.exception("Insert N last tracks failed")
        ui.notify(f"Insert failed: {exc}", type="negative")
        if _insert_last_btn is not None:
            _insert_last_btn.set_enabled(True)
        if _spinner is not None:
            _spinner.set_visibility(False)
        return

    if _spinner is not None:
        _spinner.set_visibility(False)

    # Build ordered_descs for result rendering (matching _on_start_sorting convention)
    ordered_descs = []
    for t in result_tracks:
        tid = t.get("id", "")
        desc = {"track_id": tid, "name": t.get("name", "?"), "artist": t.get("artist", "?")}
        ordered_descs.append(desc)

    # Convert to URIs for save buttons
    _sorted_track_uris = []
    _dropped_no_id = 0
    for t in result_tracks:
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
            _render_sorted_results(ordered_descs, total_cost)

    _refresh_save_buttons()
    _refresh_buttons()
    ui.notify(f"Insert complete — {n} track(s) merged (cost: {total_cost:.4f})", type="positive")


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
    global _save_new_btn, _save_current_btn, _save_spinner
    with ui.row().classes("gap-2 items-center mt-4"):
        _save_new_btn = ui.button("Save into New Playlist", on_click=_on_save_new_playlist).props("color=blue")
        _save_current_btn = ui.button("Save into Current Playlist", on_click=_on_save_current_playlist).props("color=orange")
        _save_spinner = ui.spinner(size="sm")
        _save_spinner.set_visibility(False)
    _refresh_save_buttons()


# ── Page entry point ─────────────────────────────────────────────────────────

def build_smart_sorting():
    global _sort_select, _track_list_container, _anchors_list_container
    global _analyze_stats_btn, _start_sort_btn, _analyze_hint
    global _logs_expansion, _logs_content, _logs_summary, _histogram_image, _stats_panel
    global _results_container, _spinner
    global _playlist_tracks, _playlist_name, _sorted_track_uris
    global _log_timer
    global _save_new_btn, _save_current_btn, _save_spinner
    global _insert_last_btn, _insert_n_input

    # ── Stale-reference cleanup ────────────────────────────────────────────
    _sort_select = None
    _track_list_container = None
    _anchors_list_container = None
    _analyze_stats_btn = None
    _start_sort_btn = None
    _analyze_hint = None
    _logs_expansion = None
    _logs_content = None
    _logs_summary = None
    _histogram_image = None
    _stats_panel = None
    _results_container = None
    _spinner = None
    _save_new_btn = None
    _save_current_btn = None
    _save_spinner = None
    _insert_last_btn = None
    _insert_n_input = None
    if _log_timer is not None:
        try:
            _log_timer.cancel()
        except Exception:
            logger.debug("Failed to cancel old _log_timer — already disconnected")
        _log_timer = None

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

    # ── 4. Analyze Statistics + Run Sorting + Insert N Last Tracks ─────────
    with ui.row().classes("gap-2 items-center mt-2"):
        _analyze_stats_btn = ui.button(
            "Analyze Statistics",
            on_click=_on_analyze_statistics,
        ).props("color=teal")
        _start_sort_btn = ui.button(
            "Run Sorting",
            on_click=_on_start_sorting,
        ).props("color=green")
        _insert_last_btn = ui.button(
            "Insert N Last Tracks",
            on_click=_on_insert_last_n,
        ).props("color=blue")
        _insert_n_input = ui.number(
            label="N", value=1, min=1, step=1,
            format="%.0f",
        ).classes("w-20").props("dense")
        _insert_n_input.on_value_change(lambda e: _refresh_buttons())
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