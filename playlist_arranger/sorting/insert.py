"""Greedy insertion of recently-added tracks into an already-sorted playlist.

Lightweight alternative to re-running the full ATSP/SA solver — useful when
a user manually appends N new tracks to an already-sorted playlist and wants
to fold them in without disturbing the relative order of existing tracks.

Uses the same ``_track_distance`` function and calibration-scales pattern
as the SA solver (``solver.py``), so results are consistent with what
"Run Sorting" would produce.
"""

import logging

from playlist_arranger.sorting.distance import (
    _track_distance,
    _load_embedding,
)
from playlist_arranger.ui.state import get_track_status, STATUS_OK
import playlist_arranger.config as _cfg

logger = logging.getLogger(__name__)


# ── Distance helper ──────────────────────────────────────────────────────────

def _build_distance_fn(db_dict, embeddings_by_id,
                       texture_scales, flat_scale, transition_scale):
    """Return a callable ``dist(tid_a: str, tid_b: str) -> float``.

    Uses the SAME ``_track_distance`` function and calibration parameters
    that the full SA solver uses internally.
    """
    def _dist(tid_a: str, tid_b: str) -> float:
        ta = db_dict.get(tid_a)
        tb = db_dict.get(tid_b)
        if ta is None or tb is None:
            return 1.0
        emb_a = embeddings_by_id.get(tid_a)
        emb_b = embeddings_by_id.get(tid_b)
        return _track_distance(
            ta, tb, emb_a, emb_b,
            texture_scales=texture_scales,
            flat_scale=flat_scale,
            transition_scale=transition_scale,
        )
    return _dist


# ── Delta-based total-length helpers ─────────────────────────────────────────

def _compute_total_and_neighbor_dists(track_ids, dist_fn):
    """Compute total pairwise distance and per-edge distance array.

    Returns ``(total: float, neighbor_dists: list[float])`` where
    ``neighbor_dists[i] = dist(track_ids[i], track_ids[i+1])``.
    """
    m = len(track_ids)
    if m < 2:
        return 0.0, []
    neighbor_dists = []
    total = 0.0
    for i in range(m - 1):
        d = dist_fn(track_ids[i], track_ids[i + 1])
        neighbor_dists.append(d)
        total += d
    return total, neighbor_dists


def _find_best_insertion_position(candidate_id, base_ids, dist_fn,
                                   current_total, neighbor_dists):
    """Find the position in *base_ids* that minimizes total distance.

    Uses O(1) delta computation per position — only the two boundary
    distances change when inserting at position *i*.

    Returns ``(best_pos: int, best_total: float)``.
    """
    m = len(base_ids)
    best_pos = 0
    best_total = float("inf")

    for i in range(m + 1):
        if i == 0:
            d_left = 0.0
            d_right = dist_fn(candidate_id, base_ids[0]) if m > 0 else 0.0
            d_remove = 0.0
        elif i == m:
            d_left = dist_fn(base_ids[-1], candidate_id)
            d_right = 0.0
            d_remove = 0.0
        else:
            d_left = dist_fn(base_ids[i - 1], candidate_id)
            d_right = dist_fn(candidate_id, base_ids[i])
            d_remove = neighbor_dists[i - 1]

        delta = d_left + d_right - d_remove
        new_total = current_total + delta

        if new_total < best_total:
            best_total = new_total
            best_pos = i

    return best_pos, best_total


def _rebuild_neighbor_dists(base_ids, dist_fn):
    """Recompute the full total and neighbor-dists array after an insertion."""
    return _compute_total_and_neighbor_dists(base_ids, dist_fn)


# ── Main entry point ─────────────────────────────────────────────────────────

def insert_last_n_tracks(
    playlist_tracks: list[dict],
    n: int,
    db_dict: dict,
    stats_cache,
) -> tuple[list[dict], float]:
    """Greedy sequential insertion of the last N tracks into the base order.

    Algorithm
    ---------
    1. ``candidates = playlist_tracks[-n:]`` — last N tracks in current order.
    2. Drop any candidate whose status != ``STATUS_OK``.
    3. ``base = playlist_tracks[:-n]``, then drop any NOT-OK track from base.
    4. For each remaining OK candidate (original relative order):
       a. Try every possible insertion position 0..len(base).
       b. Compute total playlist length delta in O(1) per position.
       c. Keep the position that yields the minimum total distance.
       d. Insert the candidate at that position — this becomes the new base
          for the next candidate.
    5. After all OK candidates are placed, find every track (by id) in the
       ORIGINAL ``playlist_tracks`` that is NOT in the final base result
       and append them in their original relative order.
    6. Return ``(ordered_track_list, total_cost)``.

    The distance function and calibration scales are identical to those used
    by the SA solver — weights, penalties, and per-playlist calibration
    divisors are applied from *stats_cache* before the computation and
    restored afterwards (same pattern as ``_run_smart_sorting``).
    """
    if n <= 0:
        return list(playlist_tracks), 0.0

    # ── Validate stats cache (same guard as solver) ────────────────────────
    if stats_cache is None:
        raise RuntimeError("stats_cache is required for insert_last_n_tracks")
    if "flatness" not in stats_cache.components:
        raise RuntimeError(
            "Stats cache is missing flatness calibration — "
            "re-run 'Analyze Statistics'."
        )

    # ── Save & apply cached weights / penalties (same pattern as solver) ──
    _saved_weights = dict(_cfg.WEIGHTS)
    _saved_artist_penalty = _cfg.ARTIST_PENALTY
    _saved_album_penalty = _cfg.ALBUM_PENALTY
    _saved_duration_tolerance = _cfg.DURATION_TOLERANCE

    cached_weights = stats_cache.weights_used
    if cached_weights:
        for k in ("mood", "bpm", "transition", "key", "energy", "texture", "freq_balance"):
            if k in cached_weights:
                _cfg.WEIGHTS[k] = cached_weights[k]

    cached_params = stats_cache.penalties_and_sa_params if stats_cache.penalties_and_sa_params else {}
    if cached_params:
        for key, attr_name in [("artist_penalty", "ARTIST_PENALTY"),
                               ("album_penalty", "ALBUM_PENALTY"),
                               ("duration_tolerance", "DURATION_TOLERANCE")]:
            if key in cached_params:
                setattr(_cfg, attr_name, cached_params[key])

    try:
        # ── Extract calibration scales from stats_cache ────────────────────
        dyn_scale_val = stats_cache.components.get("dynamic_range")
        onset_scale_val = stats_cache.components.get("onset_str")
        flat_scale_val = stats_cache.components.get("flatness")
        transition_scale_val = stats_cache.components.get("transition")

        dyn_scale = float(dyn_scale_val.calibration_scale) if dyn_scale_val else 20.0
        onset_scale = float(onset_scale_val.calibration_scale) if onset_scale_val else 2.0
        flat_scale = float(flat_scale_val.calibration_scale) if flat_scale_val else 0.01
        transition_scale = float(transition_scale_val.calibration_scale) if transition_scale_val else 0.25
        texture_scales = (dyn_scale, onset_scale)

        # ── Build id→track lookup for UI-level tracks ─────────────────────
        ui_track_by_id = {t["id"]: t for t in playlist_tracks if t.get("id")}

        # ── Load embeddings for all track IDs ──────────────────────────────
        all_tids = [t["id"] for t in playlist_tracks if t.get("id")]
        embeddings_by_id = {}
        for tid in all_tids:
            emb = _load_embedding(tid, db_dict)
            if emb is not None:
                embeddings_by_id[tid] = emb

        # ── Build distance function ────────────────────────────────────────
        dist_fn = _build_distance_fn(
            db_dict, embeddings_by_id,
            texture_scales=texture_scales,
            flat_scale=flat_scale,
            transition_scale=transition_scale,
        )

        total_n = len(playlist_tracks)
        actual_n = min(n, total_n)

        # ── Step 1: candidates = last N tracks ─────────────────────────────
        candidate_tracks = playlist_tracks[-actual_n:]

        # ── Step 2: drop NOT-OK candidates ─────────────────────────────────
        ok_candidates = [
            t for t in candidate_tracks
            if t.get("id") and get_track_status(t) == STATUS_OK
        ]

        # ── Step 3: base = everything except last N, drop NOT-OK ───────────
        base_tracks = playlist_tracks[:-actual_n]
        ok_base = [
            t for t in base_tracks
            if t.get("id") and get_track_status(t) == STATUS_OK
        ]

        # Work with track IDs internally
        base_ids = [t["id"] for t in ok_base]
        candidate_ids = [t["id"] for t in ok_candidates]

        if not candidate_ids:
            # Nothing to insert — return original order with cost
            if len(base_ids) >= 2:
                total, _ = _compute_total_and_neighbor_dists(base_ids, dist_fn)
            else:
                total = 0.0
            # Rebuild final order preserving original
            final_ids = base_ids + [
                t["id"] for t in playlist_tracks
                if t.get("id") and t["id"] not in set(base_ids)
            ]
            result_tracks = [
                ui_track_by_id[tid] for tid in final_ids if tid in ui_track_by_id
            ]
            return result_tracks, total

        # ── Step 4: greedy sequential insertion ────────────────────────────
        current_total, neighbor_dists = _compute_total_and_neighbor_dists(
            base_ids, dist_fn
        )

        for cid in candidate_ids:
            best_pos, current_total = _find_best_insertion_position(
                cid, base_ids, dist_fn, current_total, neighbor_dists
            )
            base_ids.insert(best_pos, cid)
            # Rebuild neighbor_dists for the next candidate
            current_total, neighbor_dists = _rebuild_neighbor_dists(
                base_ids, dist_fn
            )

        # ── Step 5: re-append tracks present in original but missing from result ─
        original_ids = [t["id"] for t in playlist_tracks if t.get("id")]
        base_id_set = set(base_ids)
        missing_ids = [tid for tid in original_ids if tid not in base_id_set]

        final_ids = base_ids + missing_ids

        # ── Convert back to UI-level track dicts ───────────────────────────
        result_tracks = [
            ui_track_by_id[tid] for tid in final_ids if tid in ui_track_by_id
        ]

        # Recompute final total for accuracy (includes NOT-OK tracks at end)
        if len(final_ids) >= 2:
            final_total, _ = _compute_total_and_neighbor_dists(final_ids, dist_fn)
        else:
            final_total = 0.0

        return result_tracks, final_total

    finally:
        # ── Restore global weights/penalties ──────────────────────────────
        _cfg.WEIGHTS.clear()
        _cfg.WEIGHTS.update(_saved_weights)
        _cfg.ARTIST_PENALTY = _saved_artist_penalty
        _cfg.ALBUM_PENALTY = _saved_album_penalty
        _cfg.DURATION_TOLERANCE = _saved_duration_tolerance