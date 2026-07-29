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
