"""Simulated Annealing ATSP solver with anchors.

All UI-agnostic: accepts a plain ``on_progress(str) -> None`` callback for
logging and reads SA parameters from ``config.load_settings()`` at call time.
"""

import math
import random
import copy
import logging

from playlist_arranger.sorting.distance import (
    _load_embedding,
    _build_distance_matrix,
    _SORTING_CACHE,
    _compute_distance_stats,
    _dump_distance_csv,
    _render_distance_histogram,
)
from playlist_arranger.sorting.anchors import _load_anchors_file

logger = logging.getLogger(__name__)

# ── Shared state for diagnostics (read by UI after solver returns) ───────────
_LAST_DISTANCE_HISTOGRAM_PATH: str | None = None


def _update_last_histogram_path(path: str | None) -> None:
    global _LAST_DISTANCE_HISTOGRAM_PATH
    _LAST_DISTANCE_HISTOGRAM_PATH = path


def get_last_histogram_path() -> str | None:
    """Return the last histogram PNG path, or None if none was generated.

    Called from ``smart_sorting.py`` after ``_run_smart_sorting`` returns.
    """
    return _LAST_DISTANCE_HISTOGRAM_PATH


def _path_cost(order: list, D) -> float:
    return sum(D[order[i], order[i + 1]] for i in range(len(order) - 1))


def _nearest_neighbor_init(free_ids: list, start: int, D) -> list:
    if not free_ids:
        return []
    remaining = set(free_ids)
    current = start
    path = []
    while remaining:
        best = min(remaining, key=lambda x: D[current, x])
        path.append(best)
        remaining.discard(best)
        current = best
    return path


def _solve_atsp_with_anchors(
    all_ids, anchors, slots, D, iterations=None, T_start=1.0, T_end=1e-4
):
    """Simulated annealing ATSP solver that respects anchor constraints."""
    n = len(all_ids)
    if iterations is None:
        iterations = max(n * 500, 5000)

    free_ids = [x for x in all_ids if x not in set(anchors)]

    if not anchors:
        if not free_ids:
            return all_ids
        shuffled = list(free_ids)
        random.shuffle(shuffled)
        if random.random() < 0.5:
            order = _nearest_neighbor_init(shuffled[1:], shuffled[0], D)
            order = [shuffled[0]] + order
        else:
            order = shuffled
        current_cost = _path_cost(order, D)
        best_order = list(order)
        best_cost = current_cost
        cooling = (T_end / T_start) ** (1.0 / max(iterations, 1))
        T = T_start
        for _ in range(iterations):
            if len(order) < 2:
                break
            i, j = sorted(random.sample(range(len(order)), 2))
            order[i : j + 1] = order[i : j + 1][::-1]
            nc = _path_cost(order, D)
            delta = nc - current_cost
            if delta < 0 or random.random() < math.exp(-delta / T):
                current_cost = nc
                if nc < best_cost:
                    best_order = list(order)
                    best_cost = nc
            else:
                order[i : j + 1] = order[i : j + 1][::-1]
            T *= cooling
        return best_order

    n_slots = len(anchors) + 1
    slot_open = (list(slots) + [False] * n_slots)[:n_slots]
    open_slots = [i for i, v in enumerate(slot_open) if v]

    if not open_slots:
        return list(anchors)

    shuffled_free = list(free_ids)
    random.shuffle(shuffled_free)
    slot_contents = [[] for _ in range(n_slots)]
    for idx, fid in enumerate(shuffled_free):
        slot_contents[open_slots[idx % len(open_slots)]].append(fid)

    for si in open_slots:
        if random.random() < 0.5:
            start_anchor = (
                anchors[si - 1]
                if si > 0
                else (slot_contents[si][0] if slot_contents[si] else 0)
            )
            slot_contents[si] = _nearest_neighbor_init(
                slot_contents[si], start_anchor, D
            )
        else:
            random.shuffle(slot_contents[si])

    def build_order():
        result = []
        for si in range(n_slots):
            result.extend(slot_contents[si])
            if si < len(anchors):
                result.append(anchors[si])
        return result

    current_cost = _path_cost(build_order(), D)
    best_slots = copy.deepcopy(slot_contents)
    best_cost = current_cost
    cooling = (T_end / T_start) ** (1.0 / max(iterations, 1))
    T = T_start

    for _ in range(iterations):
        if random.random() < 0.6 and open_slots:
            si = random.choice(open_slots)
            sl = slot_contents[si]
            if len(sl) < 2:
                T *= cooling
                continue
            i, j = sorted(random.sample(range(len(sl)), 2))
            sl[i : j + 1] = sl[i : j + 1][::-1]
            nc = _path_cost(build_order(), D)
            delta = nc - current_cost
            if delta < 0 or random.random() < math.exp(-delta / T):
                current_cost = nc
                if nc < best_cost:
                    best_slots = copy.deepcopy(slot_contents)
                    best_cost = nc
            else:
                sl[i : j + 1] = sl[i : j + 1][::-1]
        elif len(open_slots) >= 2 and free_ids:
            si = random.choice(open_slots)
            sj = random.choice([s for s in open_slots if s != si])
            if not slot_contents[si]:
                T *= cooling
                continue
            idx = random.randrange(len(slot_contents[si]))
            tid = slot_contents[si].pop(idx)
            ins = random.randrange(len(slot_contents[sj]) + 1)
            slot_contents[sj].insert(ins, tid)
            nc = _path_cost(build_order(), D)
            delta = nc - current_cost
            if delta < 0 or random.random() < math.exp(-delta / T):
                current_cost = nc
                if nc < best_cost:
                    best_slots = copy.deepcopy(slot_contents)
                    best_cost = nc
            else:
                slot_contents[sj].pop(ins)
                slot_contents[si].insert(idx, tid)
        T *= cooling

    slot_contents[:] = best_slots
    return build_order()


def _run_smart_sorting(db: dict, descs: list, pl_id: str, pl_name: str,
                       progress_cb=None, settings=None,
                       stats_cache=None, snapshot_id=None):
    """
    Run SA sorting, return (ordered_descs, cost) tuple.
    Pure logic — no UI/no console.

    Parameters
    ----------
    db : dict
        Track database keyed by track_id.
    descs : list
        Track descriptor dicts (must have ``track_id``, ``name``, ``artist``).
    pl_id : str
        Spotify playlist ID.
    pl_name : str
        Playlist name (for logging only).
    progress_cb : callable(str) -> None, optional
        Synchronous progress callback.  Called from the worker thread; the
        caller is responsible for thread-safe delivery to the UI.
    settings : ``config.Settings``, optional
        If None, ``config.load_settings()`` is called to read SA parameters
        (iterations multiplier, n_runs, T_start, T_end).
    stats_cache : ``StatsResult`` | None, optional
        Pre-loaded per-playlist calibration stats.  If None, the solver
        refuses to run (raises RuntimeError) — sorting requires valid
        calibration data per the stats-analysis workflow.
    snapshot_id : str | None
        Used to re-save the cache with ``last_used_for_sort_at`` timestamp.
    """
    if settings is None:
        from playlist_arranger.config import load_settings
        settings = load_settings()

    def _log(msg: str) -> None:
        logger.info("%s", msg)
        if progress_cb:
            progress_cb(msg)

    # ── Validate stats cache ──────────────────────────────────────────────
    if stats_cache is None:
        raise RuntimeError(
            f"No stats analysis available for playlist {pl_id[:12]} — "
            f"run 'Analyze Statistics' first before sorting."
        )
    if "flatness" not in stats_cache.components:
        raise RuntimeError(
            f"Stats cache for {pl_id[:12]} is missing flatness calibration — "
            f"re-run 'Analyze Statistics'."
        )

    # ── Apply cached calibration scales ───────────────────────────────────
    dyn_scale_val = stats_cache.components.get("dynamic_range")
    onset_scale_val = stats_cache.components.get("onset_str")
    flat_scale_val = stats_cache.components.get("flatness")
    transition_scale_val = stats_cache.components.get("transition")

    dyn_scale = float(dyn_scale_val.calibration_scale) if dyn_scale_val else 20.0
    onset_scale = float(onset_scale_val.calibration_scale) if onset_scale_val else 2.0
    flat_scale = float(flat_scale_val.calibration_scale) if flat_scale_val else 0.01
    transition_scale = float(transition_scale_val.calibration_scale) if transition_scale_val else 0.25

    _log(
        f"Using per-playlist calibration: dyn_scale={dyn_scale:.2f}, "
        f"onset_scale={onset_scale:.2f}, flat_scale={flat_scale:.4f}"
    )

    # ── Apply cached weights (user-tuned from stats UI) ───────────────────
    import playlist_arranger.config as _cfg_mod
    cached_weights = stats_cache.weights_used
    if cached_weights:
        for k in ("mood", "bpm", "transition", "key", "energy", "texture", "freq_balance"):
            if k in cached_weights:
                _cfg_mod.WEIGHTS[k] = cached_weights[k]
        _log(f"Applied cached user-tuned weights: {dict(_cfg_mod.WEIGHTS)}")
    else:
        # Fallback to config defaults — sync from settings
        _cfg_mod.sync_weights_from_settings(settings)

    plan = _load_anchors_file(pl_id)
    if not plan:
        plan = [{"type": "placeholder"}]

    n_anchors = sum(1 for e in plan if e["type"] == "anchor")
    if n_anchors == 0:
        _log(
            "No anchors in plan — running unconstrained free-TSP sort over "
            "all tracks (single open slot, no anchor pinning)."
        )
        plan = [{"type": "placeholder"}]
        # Deliberately NOT returning — fall through. The subsequent
        # index-array construction below naturally produces anchors_idx=[]
        # and slots=[True] for this plan, which _solve_atsp_with_anchors()
        # already handles via its `if not anchors:` free-TSP branch.

    # Build index arrays
    desc_by_id = {d["track_id"]: d for d in descs}
    track_ids = [d["track_id"] for d in descs]
    tid_to_idx = {tid: i for i, tid in enumerate(track_ids)}

    anchors_idx = [
        tid_to_idx[e["track_id"]] for e in plan if e["type"] == "anchor"
    ]
    slots = []
    ap = [j for j, e in enumerate(plan) if e["type"] == "anchor"]
    if ap:
        slots = [
            any(plan[k]["type"] == "placeholder" for k in range(0, ap[0]))
        ]
        for i in range(len(ap) - 1):
            slots.append(
                any(
                    plan[k]["type"] == "placeholder"
                    for k in range(ap[i] + 1, ap[i + 1])
                )
            )
        slots.append(
            any(
                plan[k]["type"] == "placeholder"
                for k in range(ap[-1] + 1, len(plan))
            )
        )

    # Load track data from DB + embeddings
    all_tracks = [db[tid] for tid in track_ids if tid in db]
    if len(all_tracks) != len(track_ids):
        _log("Some tracks not in DB — sorting may be degraded.")
        valid_ids = [
            t.get("track_id", tid)
            for tid, t in zip(track_ids, all_tracks)
        ]
        new_tid_to_idx = {tid: i for i, tid in enumerate(valid_ids)}
        filtered_anchors = []
        new_plan = []
        for e in plan:
            if (
                e["type"] == "anchor"
                and e["track_id"] in new_tid_to_idx
            ):
                filtered_anchors.append(new_tid_to_idx[e["track_id"]])
                new_plan.append(e)
            elif e["type"] == "placeholder":
                new_plan.append(e)
        anchors_idx = filtered_anchors
        slots = []
        ap_new = [j for j, e in enumerate(new_plan) if e["type"] == "anchor"]
        if ap_new:
            slots = [
                any(
                    new_plan[k]["type"] == "placeholder"
                    for k in range(0, ap_new[0])
                )
            ]
            for i in range(len(ap_new) - 1):
                slots.append(
                    any(
                        new_plan[k]["type"] == "placeholder"
                        for k in range(ap_new[i] + 1, ap_new[i + 1])
                    )
                )
            slots.append(
                any(
                    new_plan[k]["type"] == "placeholder"
                    for k in range(ap_new[-1] + 1, len(new_plan))
                )
            )
        all_tracks = [db[tid] for tid in valid_ids]
        track_ids = valid_ids
        tid_to_idx = new_tid_to_idx

    embeddings = [_load_embedding(tid, db) for tid in track_ids]

    n_total = len(all_tracks)

    # ── Build distance matrix with per-playlist calibration ───────────────
    # Scales already loaded from stats cache above — do NOT re-calibrate.
    texture_scales = (dyn_scale, onset_scale)
    # Include flat_scale in cache key so flatness calibration change invalidates
    scale_key = (round(dyn_scale, 3), round(onset_scale, 3), round(flat_scale, 6))
    cache_key = (tuple(track_ids), scale_key)

    cached = _SORTING_CACHE.get(pl_id)
    if cached and cached.get("track_ids") == cache_key:
        D = cached["D"]
        _log(f"Using cached distance matrix ({n_total}×{n_total})")
    else:
        _log(
            f"Building distance matrix for {n_total} tracks ({n_total*n_total} pairs)..."
        )
        D = _build_distance_matrix(
            list(range(n_total)), all_tracks, embeddings,
            texture_scales=texture_scales,
            flat_scale=flat_scale,
            transition_scale=transition_scale,
        )
        _SORTING_CACHE[pl_id] = {
            "D": D,
            "track_ids": cache_key,
            "all_tracks": all_tracks,
            "embeddings": embeddings,
        }
    _log(
        f"Calibration applied: dyn_scale={dyn_scale:.2f}, "
        f"onset_scale={onset_scale:.2f}, flat_scale={flat_scale:.4f}"
    )

    # ── Distance matrix diagnostics ──────────────────────────────────────────
    stats = _compute_distance_stats(D)
    _log(
        f"Distance matrix stats: min={stats['min']:.2f} max={stats['max']:.2f} "
        f"mean={stats['mean']:.2f} std={stats['std']:.2f} CV={stats['cv']:.2f} "
        f"(n={stats['n']})"
    )

    track_names = [
        all_tracks[i].get("name", "?") if i < len(all_tracks) else "?"
        for i in range(len(track_ids))
    ]
    csv_path = _dump_distance_csv(D, track_names, track_ids, pl_id)
    if csv_path:
        _log(f"Distance matrix dumped to {csv_path}")

    hist_path = _render_distance_histogram(D, pl_id)
    if hist_path:
        _update_last_histogram_path(hist_path)

    all_indices = list(range(len(all_tracks)))
    iters = max(n_total * settings.sa_iterations_multiplier, 5000)
    N_RUNS = settings.sa_n_runs
    T_start = settings.sa_T_start
    T_end = settings.sa_T_end

    _log(f"SA: {N_RUNS} runs × {iters} iterations (T={T_start}→{T_end})")
    best_order, best_cost = None, float("inf")
    for run in range(N_RUNS):
        candidate = _solve_atsp_with_anchors(
            all_indices, anchors_idx, slots, D,
            iterations=iters, T_start=T_start, T_end=T_end,
        )
        cost = _path_cost(candidate, D)
        if cost < best_cost:
            best_cost, best_order = cost, candidate
        if (run + 1) % 20 == 0 or run == 0:
            _log(f"  Run {run+1}/{N_RUNS}  best={best_cost:.4f}")

    ordered = best_order
    _log(f"Best cost: {best_cost:.4f}")

    # Build ordered list
    ordered_descs = []
    anchor_set = set(anchors_idx)
    for idx in ordered:
        tid = track_ids[idx]
        d = desc_by_id.get(
            tid, {"track_id": tid, "name": "?", "artist": "?"}
        )
        ordered_descs.append(d)

    # ── Re-save cache with sort timestamp for audit trail ─────────────────
    if snapshot_id:
        try:
            import datetime as _dt
            from playlist_arranger.sorting.stats_analysis import save_stats_cache
            stats_cache.last_used_for_sort_at = _dt.datetime.now().isoformat()
            save_stats_cache(stats_cache)
            _log(f"Stats cache updated with sort timestamp for snapshot {snapshot_id[:12]}")
        except Exception:
            logger.exception("Failed to update stats cache sort timestamp")

    return ordered_descs, float(best_cost)
