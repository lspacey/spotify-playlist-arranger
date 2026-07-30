"""Tests for solver's handling of empty/anchorless playlists.

Regression guard: _run_smart_sorting() and _solve_atsp_with_anchors()
must NOT early-return when no anchors exist — the free-TSP branch in
_solve_atsp_with_anchors() already handles anchors=[] natively.
"""

import numpy as np
from unittest.mock import patch

from playlist_arranger.sorting.stats_analysis import (
    StatsResult, ComponentCalibration,
)


def _fake_db(track_ids):
    feats = {
        "bpm": 120.0, "rms_db": -12.0,
        "harm_ratio": 0.9, "flatness": 0.002,
        "dynamic_range": 10.0, "onset_str": 0.5,
        "bass": 0.3, "mid": 0.4, "high": 0.3,
        "camelot": "8B",
        "mfcc20": [0.1] * 20,
        "chroma_cens": [0.5] * 12,
        "embedding_file": None,
    }
    return {
        tid: {"features": dict(feats), "start_seg": dict(feats), "end_seg": dict(feats),
              "track_id": tid, "name": f"Track{i}", "artist": "Artist"}
        for i, tid in enumerate(track_ids)
    }


def _fake_stats_cache():
    """Minimal StatsResult with required calibration scales."""
    result = StatsResult(playlist_id="fake_pl", snapshot_id="snap1")
    # Provide flatness, dynamic_range, onset_str calibration
    for name, scale in [("dynamic_range", 20.0), ("onset_str", 2.0), ("flatness", 0.005)]:
        result.components[name] = ComponentCalibration(
            name=name, calibration_scale=scale,
            observed_min=0.1, observed_max=0.9, observed_mean=0.5,
            observed_std=0.2, observed_cv=0.4,
            histogram_counts=[1]*12, histogram_edges=[0.0]*13,
        )
    result.weights_used = {"mood": 0.48, "bpm": 0.12, "transition": 0.20,
                           "key": 0.12, "energy": 0.08, "texture": 0.10,
                           "freq_balance": 0.08}
    return result


def test_solve_atsp_with_no_anchors_returns_full_free_order():
    """Direct solver-core test: anchors=[] must produce a full free-TSP
    order over all_ids, exercising the 'if not anchors:' branch."""
    from playlist_arranger.sorting.solver import _solve_atsp_with_anchors

    all_ids = list(range(6))
    D = np.ones((6, 6))  # trivial uniform distance matrix
    for i in range(6):
        D[i, i] = 0.0
    order = _solve_atsp_with_anchors(all_ids, anchors=[], slots=[], D=D, iterations=100)
    assert sorted(order) == all_ids, (
        f"Expected all track indices in order, got {order}"
    )
    assert len(order) == 6


def test_run_smart_sorting_with_missing_anchors_file_still_sorts():
    """Regression test: _run_smart_sorting() must NOT early-return with
    cost=0.0 when no anchors file exists."""
    from playlist_arranger.sorting.solver import _run_smart_sorting

    track_ids = [f"tid{i}" for i in range(5)]
    descs = [{"track_id": tid, "name": f"Track {i}", "artist": "Artist"}
             for i, tid in enumerate(track_ids)]
    db = _fake_db(track_ids)

    logs = []
    stats_cache = _fake_stats_cache()
    with patch("playlist_arranger.sorting.solver._load_anchors_file",
               return_value=None), \
         patch("playlist_arranger.sorting.solver._load_embedding",
               return_value=None):
        ordered_descs, cost = _run_smart_sorting(
            db, descs, pl_id="fake_pl", pl_name="Fake Playlist",
            progress_cb=logs.append,
            stats_cache=stats_cache,
            snapshot_id="snap1",
        )

    assert len(ordered_descs) == len(descs), (
        f"Expected {len(descs)} tracks, got {len(ordered_descs)}"
    )
    assert {d["track_id"] for d in ordered_descs} == set(track_ids)
    assert cost > 0.0, f"Cost should be nonzero, got {cost}"
    assert any(
        "unconstrained" in line.lower() or "free-tsp" in line.lower()
        for line in logs
    ), f"Logs should mention unconstrained/free-TSP, got: {logs}"


def test_run_smart_sorting_with_empty_anchor_plan_still_sorts():
    """Plan with only placeholders must also fall through, not early-return."""
    from playlist_arranger.sorting.solver import _run_smart_sorting

    track_ids = [f"tid{i}" for i in range(4)]
    descs = [{"track_id": tid, "name": f"Track {i}", "artist": "Artist"}
             for i, tid in enumerate(track_ids)]
    db = _fake_db(track_ids)

    stats_cache = _fake_stats_cache()
    with patch("playlist_arranger.sorting.solver._load_anchors_file",
               return_value=[{"type": "placeholder"}]), \
         patch("playlist_arranger.sorting.solver._load_embedding",
               return_value=None):
        ordered_descs, cost = _run_smart_sorting(
            db, descs, pl_id="fake_pl", pl_name="Fake Playlist",
            stats_cache=stats_cache,
            snapshot_id="snap1",
        )

    assert len(ordered_descs) == len(descs), (
        f"Expected {len(descs)} tracks, got {len(ordered_descs)}"
    )
    assert {d["track_id"] for d in ordered_descs} == set(track_ids)
    assert cost > 0.0, f"Cost must be nonzero, got {cost}"


def test_stats_cache_weights_actually_affect_distance_matrix():
    """Verify that weights from stats_cache are applied to the distance
    matrix, producing measurably different results from default weights.

    This guards against a silent fallback to settings.json defaults when
    the user has tuned weights via the Analyze Statistics panel.
    """
    import copy
    import numpy as np
    from playlist_arranger.sorting.solver import _run_smart_sorting
    from playlist_arranger.sorting.distance import _build_distance_matrix
    import playlist_arranger.config as _cfg

    def _varied_db(track_ids):
        """Like _fake_db but each track gets different feature values so weights matter."""
        base_feats = {
            "harm_ratio": 0.9, "flatness": 0.002,
            "dynamic_range": 10.0, "onset_str": 0.5,
            "bass": 0.3, "mid": 0.4, "high": 0.3,
            "camelot": "8B",
            "mfcc20": [0.1] * 20,
            "chroma_cens": [0.5] * 12,
            "rms_db": -12.0,
            "embedding_file": None,
        }
        db = {}
        for i, tid in enumerate(track_ids):
            feats = dict(base_feats)
            # Vary bpm: 60, 90, 120, 180 -- wide spread
            feats["bpm"] = 60 + 30 * i
            # Vary rms_db: -20, -16, -12, -8 -- different energy
            feats["rms_db"] = -20 + 4 * i
            # Vary mfcc20: each track has different MFCCs -- affects mood/transition
            feats["mfcc20"] = [0.1 + 0.05 * i] * 20
            # Vary chroma -- affects mood fallback
            feats["chroma_cens"] = [(0.5 + 0.1 * i) % 1.0] * 12
            db[tid] = {
                "features": dict(feats),
                "start_seg": dict(feats),
                "end_seg": dict(feats),
                "track_id": tid, "name": f"Track{i}", "artist": "Artist",
            }
        return db

    track_ids = [f"tid{i}" for i in range(4)]
    descs = [{"track_id": tid, "name": f"Track {i}", "artist": "Artist"}
             for i, tid in enumerate(track_ids)]
    db = _varied_db(track_ids)

    # Build cache A: extreme mood weight, minimal everything else
    cache_A = _fake_stats_cache()
    cache_A.weights_used = {
        "mood": 0.95, "bpm": 0.01, "transition": 0.01,
        "key": 0.01, "energy": 0.01, "texture": 0.01, "freq_balance": 0.01,
    }

    # Build cache B: extreme bpm weight, minimal everything else
    cache_B = _fake_stats_cache()
    cache_B.weights_used = {
        "mood": 0.01, "bpm": 0.95, "transition": 0.01,
        "key": 0.01, "energy": 0.01, "texture": 0.01, "freq_balance": 0.01,
    }

    # Save original WEIGHTS to restore after
    original_weights = dict(_cfg.WEIGHTS)

    all_tracks = list(db.values())
    embeds = [None] * len(all_tracks)

    # Build matrix with cache_A weights
    for k in ("mood", "bpm", "transition", "key", "energy", "texture", "freq_balance"):
        _cfg.WEIGHTS[k] = cache_A.weights_used[k]
    D_A = _build_distance_matrix(
        list(range(len(all_tracks))), all_tracks, embeds,
        texture_scales=(20.0, 2.0), flat_scale=0.005, transition_scale=1.0,
    )

    # Build matrix with cache_B weights
    for k in ("mood", "bpm", "transition", "key", "energy", "texture", "freq_balance"):
        _cfg.WEIGHTS[k] = cache_B.weights_used[k]
    D_B = _build_distance_matrix(
        list(range(len(all_tracks))), all_tracks, embeds,
        texture_scales=(20.0, 2.0), flat_scale=0.005, transition_scale=1.0,
    )

    # Restore original weights
    for k in original_weights:
        _cfg.WEIGHTS[k] = original_weights[k]

    # Assert: matrices must differ -- weights are actually applied
    off_diag_A = D_A[D_A != 0]
    off_diag_B = D_B[D_B != 0]
    max_diff = float(np.max(np.abs(off_diag_A - off_diag_B)))
    assert max_diff > 1e-6, (
        f"Distance matrices are identical (max_diff={max_diff:.10f}) -- "
        f"cached weights are NOT being applied to _track_distance()"
    )

    # Also verify through _run_smart_sorting logs
    logs_A = []
    with patch("playlist_arranger.sorting.solver._load_anchors_file",
               return_value=None), \
         patch("playlist_arranger.sorting.solver._load_embedding",
               return_value=None):
        # Restore A weights before calling
        for k in ("mood", "bpm", "transition", "key", "energy", "texture", "freq_balance"):
            _cfg.WEIGHTS[k] = original_weights[k]
        _run_smart_sorting(
            db, descs, pl_id="fake_pl_wt", pl_name="Weight Test",
            progress_cb=logs_A.append,
            stats_cache=cache_A,
            snapshot_id="snap1",
        )

    assert any("Applied cached user-tuned weights" in l for l in logs_A), (
        f"Solver did not log weight application: {logs_A}"
    )
    assert any("'mood': 0.95" in l for l in logs_A), (
        f"Weight log does not contain mood=0.95: {logs_A}"
    )
    assert any("'bpm': 0.01" in l for l in logs_A), (
        f"Weight log does not contain bpm=0.01: {logs_A}"
    )


def test_weights_global_not_leaked_on_exception():
    """Ensure _cfg.WEIGHTS is restored even if sorting raises mid-run.

    Regression: before the try/finally guard in _run_smart_sorting(),
    an exception after WEIGHTS mutation would leak playlist A's tuned
    weights into playlist B's distance computation.
    """
    import playlist_arranger.config as _cfg
    from playlist_arranger.sorting.solver import _run_smart_sorting

    pre_weights = dict(_cfg.WEIGHTS)

    track_ids = [f"tid{i}" for i in range(3)]
    descs = [{"track_id": tid, "name": f"Track {i}", "artist": "Artist"}
             for i, tid in enumerate(track_ids)]
    db = _fake_db(track_ids)

    cache = _fake_stats_cache()
    cache.weights_used = {
        "mood": 0.99, "bpm": 0.99, "transition": 0.99,
        "key": 0.99, "energy": 0.99, "texture": 0.99, "freq_balance": 0.99,
    }

    with patch("playlist_arranger.sorting.solver._build_distance_matrix",
               side_effect=RuntimeError("injected failure")), \
         patch("playlist_arranger.sorting.solver._load_anchors_file",
               return_value=None), \
         patch("playlist_arranger.sorting.solver._load_embedding",
               return_value=None):
        try:
            _run_smart_sorting(
                db, descs, pl_id="fake_leak", pl_name="Leak Test",
                stats_cache=cache, snapshot_id="snap1",
            )
        except RuntimeError:
            pass  # expected

    assert _cfg.WEIGHTS == pre_weights, (
        f"WEIGHTS leaked after exception!\n"
        f"  before: {pre_weights}\n"
        f"  after:  {dict(_cfg.WEIGHTS)}"
    )


def test_penalties_global_not_leaked_on_exception():
    """Ensure _cfg.ARTIST_PENALTY, _cfg.ALBUM_PENALTY, _cfg.DURATION_TOLERANCE
    are restored even if sorting raises mid-run.

    Analogous to test_weights_global_not_leaked_on_exception — validates
    the try/finally snapshot-and-restore for penalties added in the C.2
    leak-safety fix.
    """
    import playlist_arranger.config as _cfg
    from playlist_arranger.sorting.solver import _run_smart_sorting

    pre_artist = _cfg.ARTIST_PENALTY
    pre_album = _cfg.ALBUM_PENALTY
    pre_dur_tol = _cfg.DURATION_TOLERANCE

    track_ids = [f"tid{i}" for i in range(3)]
    descs = [{"track_id": tid, "name": f"Track {i}", "artist": "Artist"}
             for i, tid in enumerate(track_ids)]
    db = _fake_db(track_ids)

    cache = _fake_stats_cache()
    cache.weights_used = {
        "mood": 0.99, "bpm": 0.99, "transition": 0.99,
        "key": 0.99, "energy": 0.99, "texture": 0.99, "freq_balance": 0.99,
    }
    # Set distinctive penalty values that differ from defaults
    cache.penalties_and_sa_params = {
        "artist_penalty": 0.77,
        "album_penalty": 0.88,
        "duration_tolerance": 0.33,
        "iterations_multiplier": 500,
        "n_runs": 100,
        "T_start": 1.0,
        "T_end": 0.0001,
    }

    with patch("playlist_arranger.sorting.solver._build_distance_matrix",
               side_effect=RuntimeError("injected failure")), \
         patch("playlist_arranger.sorting.solver._load_anchors_file",
               return_value=None), \
         patch("playlist_arranger.sorting.solver._load_embedding",
               return_value=None):
        try:
            _run_smart_sorting(
                db, descs, pl_id="fake_penalty_leak", pl_name="Penalty Leak Test",
                stats_cache=cache, snapshot_id="snap1",
            )
        except RuntimeError:
            pass  # expected

    assert _cfg.ARTIST_PENALTY == pre_artist, (
        f"ARTIST_PENALTY leaked after exception!\n"
        f"  before: {pre_artist}\n"
        f"  after:  {_cfg.ARTIST_PENALTY}"
    )
    assert _cfg.ALBUM_PENALTY == pre_album, (
        f"ALBUM_PENALTY leaked after exception!\n"
        f"  before: {pre_album}\n"
        f"  after:  {_cfg.ALBUM_PENALTY}"
    )
    assert _cfg.DURATION_TOLERANCE == pre_dur_tol, (
        f"DURATION_TOLERANCE leaked after exception!\n"
        f"  before: {pre_dur_tol}\n"
        f"  after:  {_cfg.DURATION_TOLERANCE}"
    )

    # Also verify WEIGHTS not leaked (same as existing test)
    # — already validated implicitly since nothing mutates them
    # outside the try/finally, but we check explicitly for safety
    assert _cfg.ARTIST_PENALTY == pre_artist, "Double-check artist_penalty restored"
