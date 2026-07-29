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
)
from playlist_arranger.sorting.anchors import _load_anchors_file

logger = logging.getLogger(__name__)


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
                       progress_cb=None, settings=None):
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
    """
    if settings is None:
        from playlist_arranger.config import load_settings
        settings = load_settings()

    def _log(msg: str) -> None:
        logger.info("%s", msg)
        if progress_cb:
            progress_cb(msg)

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

    # ── Calibrate texture normalization scales per playlist ──────────────
    # Scales are computed from the CURRENT playlist's feature distributions
    # (not a global constant) so that a 2 dB dynamic_range difference counts
    # more in a playlist with a narrow spread than in one spanning 30 dB.
    from playlist_arranger.sorting.distance import _calibrate_texture_scales

    dyn_scale, onset_scale = _calibrate_texture_scales(all_tracks)

    # Cache key must encompass the calibration scales because they change the
    # distance matrix D.  Round scales to 3 decimal places to avoid cache
    # misses from floating-point noise while still being playlist-sensitive.
    # The track_ids tuple already changes per playlist, and scales are
    # recomputed per playlist selection, so a separate `scale_key` ensures
    # cache invalidation if the same track set is recalibrated differently.
    scale_key = (round(dyn_scale, 3), round(onset_scale, 3))
    texture_scales = (dyn_scale, onset_scale)
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
        )
        _SORTING_CACHE[pl_id] = {
            "D": D,
            "track_ids": cache_key,
            "all_tracks": all_tracks,
            "embeddings": embeddings,
        }
    _log(
        f"Calibrated normalization: dynamic_range scale={dyn_scale:.2f} dB, "
        f"onset_str scale={onset_scale:.2f}"
    )

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

    return ordered_descs, float(best_cost)
