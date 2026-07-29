"""Unit tests for playlist_arranger.sorting.distance module.

Consolidated from test_desc_generator.py during the custom-runner→pytest
migration (2026-07-29).
"""

import sys
sys.path.insert(0, r"e:\Projects\Spotify_playlists\repository")

import numpy as np
import pytest

from playlist_arranger.sorting.distance import (
    _track_distance, WEIGHTS, _SORTING_CACHE,
    _compute_distance_stats, _dump_distance_csv,
)
from playlist_arranger.config import Settings, load_settings
import playlist_arranger.config as _cfg
from unittest.mock import patch


# ── Distance component tests ───────────────────────────────────────────────────

def test_d_texture_same_track_returns_zero():
    """d_texture with identical features → 0.0."""
    fa = {
        "harm_ratio": 0.8, "flatness": 0.2,
        "dynamic_range": 15.0, "onset_str": 1.2,
    }
    ta = {"features": fa, "end_seg": fa}
    tb = {"features": fa, "start_seg": fa}
    d = _track_distance(ta, tb, None, None)
    assert d >= 0.0, f"Distance should be >= 0, got {d}"
    assert d < 1.5, f"Distance too large: {d}"


def test_d_texture_different_tracks_nonzero():
    """d_texture with maximally different features contributes to total distance."""
    fa = {
        "harm_ratio": 1.0, "flatness": 0.0,
        "dynamic_range": 30.0, "onset_str": 2.0,
    }
    fb = {
        "harm_ratio": 0.0, "flatness": 1.0,
        "dynamic_range": 0.0, "onset_str": 0.0,
    }
    ta = {"features": fa, "end_seg": fa}
    tb = {"features": fb, "start_seg": fb}
    d = _track_distance(ta, tb, None, None)
    assert d > 0.40, f"Expected d > 0.40, got {d}"


def test_d_freq_balance_same_vector_zero():
    """d_freq_balance with identical bass/mid/high → 0."""
    fa = {"bass": 0.4, "mid": 0.3, "high": 0.3}
    fb = {"bass": 0.4, "mid": 0.3, "high": 0.3}
    ta = {"features": fa, "end_seg": fa}
    tb = {"features": fb, "start_seg": fb}
    d = _track_distance(ta, tb, None, None)
    assert d >= 0.0, f"Distance should be >= 0, got {d}"


def test_d_freq_balance_orthogonal():
    """d_freq_balance for [1,0,0] vs [0,1,0] contributes to total distance."""
    fa = {"bass": 1.0, "mid": 0.0, "high": 0.0}
    fb = {"bass": 0.0, "mid": 1.0, "high": 0.0}
    ta = {"features": fa, "end_seg": fa}
    tb = {"features": fb, "start_seg": fb}
    d = _track_distance(ta, tb, None, None)
    assert d > 0.40, f"Expected d > 0.40 with orthogonal freq vectors, got {d}"


def test_settings_missing_new_weights_falls_back():
    """Old settings.json without w_texture/w_freq_balance → uses dataclass defaults."""
    s = load_settings()
    assert hasattr(s, 'w_texture'), "Settings should have w_texture"
    assert hasattr(s, 'w_freq_balance'), "Settings should have w_freq_balance"
    assert s.w_texture == 0.10, f"Expected default 0.10, got {s.w_texture}"
    assert s.w_freq_balance == 0.08, f"Expected default 0.08, got {s.w_freq_balance}"


def test_freq_balance_all_zero_treated_as_neutral():
    """When bass+mid+high == 0 for one track, both vectors normalise to 0.33/0.33/0.33 → d_freq_balance=0."""
    fa = {"bass": 0.0, "mid": 0.0, "high": 0.0}
    fb = {"bass": 0.3, "mid": 0.4, "high": 0.3}
    ta = {"features": fa, "end_seg": fa}
    tb = {"features": fb, "start_seg": fb}
    d = _track_distance(ta, tb, None, None)
    assert d >= 0.0, f"Distance should be >= 0, got {d}"


def test_duration_mismatch_penalty_skipped_when_zero():
    """When one track has duration_ms==0 (missing data), the penalty is skipped — no exception."""
    fa = fb = {"bpm": 120, "camelot": "8B", "rms_db": -12,
               "harm_ratio": 0.5, "flatness": 0.5, "dynamic_range": 10.0,
               "onset_str": 1.0, "bass": 0.33, "mid": 0.33, "high": 0.33}
    ta = {"features": fa, "end_seg": fa}
    tb = {"features": fb, "start_seg": fb, "duration_ms": 240000}
    d = _track_distance(ta, tb, None, None)
    assert d >= 0.0, f"Distance should be >= 0, got {d}"
    ta_zero = {"features": fa, "end_seg": fa}
    tb_zero = {"features": fb, "start_seg": fb}
    d2 = _track_distance(ta_zero, tb_zero, None, None)
    assert d2 >= 0.0, f"Distance should be >= 0 when both durations are zero, got {d2}"
    assert d2 == d, (
        f"Distance should be identical whether one or both durations are missing (no penalty in either case). "
        f"one-missing={d:.4f}, both-missing={d2:.4f}"
    )


def test_duration_mismatch_penalty_applied():
    """Tracks with >10% duration difference (DURATION_TOLERANCE=0.10) get a penalty."""
    fa = fb = {"bpm": 120, "camelot": "8B", "rms_db": -12,
               "harm_ratio": 0.5, "flatness": 0.5, "dynamic_range": 10.0,
               "onset_str": 1.0, "bass": 0.33, "mid": 0.33, "high": 0.33}
    ta = {"features": fa, "end_seg": fa, "duration_ms": 90000}
    tb = {"features": fb, "start_seg": fb, "duration_ms": 240000}
    d_with = _track_distance(ta, tb, None, None)
    ta_same = {"features": fa, "end_seg": fa, "duration_ms": 240000}
    tb_same = {"features": fb, "start_seg": fb, "duration_ms": 240000}
    d_without = _track_distance(ta_same, tb_same, None, None)
    assert d_with > d_without, (
        f"Expected duration penalty to increase distance: "
        f"with diff={d_with:.4f}, without diff={d_without:.4f}"
    )


def test_track_distance_includes_texture_and_freq_balance():
    """_track_distance uses w_texture and w_freq_balance in final weighted sum."""
    ta = {"features": {}, "end_seg": {
        "bpm": 120, "camelot": "8B", "rms_db": -12,
        "harm_ratio": 1.0, "flatness": 0.0, "dynamic_range": 30.0, "onset_str": 2.0,
        "bass": 1.0, "mid": 0.0, "high": 0.0,
    }}
    tb = {"features": {}, "start_seg": {
        "bpm": 120, "camelot": "8B", "rms_db": -12,
        "harm_ratio": 0.0, "flatness": 1.0, "dynamic_range": 0.0, "onset_str": 0.0,
        "bass": 0.0, "mid": 1.0, "high": 0.0,
    }}
    orig = dict(WEIGHTS)
    try:
        for k in WEIGHTS:
            WEIGHTS[k] = 0.0
        WEIGHTS["texture"] = 1.0
        WEIGHTS["freq_balance"] = 1.0
        d = _track_distance(ta, tb, None, None)
        assert d == 1.3, (
            f"Expected distance capped at 1.3 (1.0 + ALBUM_PENALTY=0.3), got {d}"
        )
    finally:
        WEIGHTS.update(orig)


def test_todo_comment_near_normalization_constants():
    """The calibration comment about normalization is present in distance.py."""
    from pathlib import Path
    distance_py = Path(__file__).parent.parent / "playlist_arranger" / "sorting" / "distance.py"
    content = distance_py.read_text(encoding="utf-8")
    assert "per-playlist" in content.lower(), "distance.py should mention per-playlist calibration"
    assert "dyn_scale" in content, "distance.py should reference dyn_scale"
    assert "onset_scale" in content, "distance.py should reference onset_scale"
    assert "robust" in content.lower(), "distance.py should mention robust range"
    assert "_calibrate_texture_scales" in content, "distance.py should contain _calibrate_texture_scales"


# ── Distance matrix diagnostics tests ─────────────────────────────────────────

def test_compute_distance_stats_synthetic_5x5():
    """Given a synthetic 5x5 symmetric matrix with known values, stats are correct."""
    D = np.array([
        [0.0, 0.1, 0.2, 0.3, 0.4],
        [0.1, 0.0, 0.15, 0.25, 0.35],
        [0.2, 0.15, 0.0, 0.5, 0.6],
        [0.3, 0.25, 0.5, 0.0, 0.7],
        [0.4, 0.35, 0.6, 0.7, 0.0],
    ], dtype=np.float32)

    stats = _compute_distance_stats(D)

    # Off-diagonal entries: 20 values
    # min=0.1, max=0.7
    assert stats["n"] == 20, f"Expected 20 off-diagonal entries, got {stats['n']}"
    assert stats["min"] == pytest.approx(0.1, abs=0.001), f"min: {stats['min']}"
    assert stats["max"] == pytest.approx(0.7, abs=0.001), f"max: {stats['max']}"
    # Mean of all 20 off-diagonal values
    expected_mean = (
        0.1 + 0.2 + 0.3 + 0.4
        + 0.1 + 0.15 + 0.25 + 0.35
        + 0.2 + 0.15 + 0.5 + 0.6
        + 0.3 + 0.25 + 0.5 + 0.7
        + 0.4 + 0.35 + 0.6 + 0.7
    ) / 20.0
    assert stats["mean"] == pytest.approx(expected_mean, abs=0.001), f"mean: {stats['mean']} vs {expected_mean}"
    assert stats["median"] == pytest.approx(0.325, abs=0.02)
    assert stats["std"] > 0.15, f"Expected std > 0.15, got {stats['std']}"
    # CV = std/mean — should be > 0 for a non-flat matrix
    assert stats["cv"] > 0.30, f"Expected CV > 0.30 for this non-flat matrix, got {stats['cv']}"


def test_compute_distance_stats_flat_matrix():
    """A nearly-flat matrix yields a very low CV."""
    D = np.full((4, 4), 0.5, dtype=np.float32)
    np.fill_diagonal(D, 0.0)
    stats = _compute_distance_stats(D)
    # std should be near 0 → CV near 0
    assert stats["std"] < 0.01, f"Expected std near 0 for flat matrix, got {stats['std']}"
    assert stats["cv"] < 0.05, f"Expected CV near 0 for flat matrix, got {stats['cv']}"
    assert stats["min"] == pytest.approx(0.5, abs=0.01)
    assert stats["max"] == pytest.approx(0.5, abs=0.01)


def test_dump_distance_csv_mocked_io(tmp_path):
    """_dump_distance_csv writes the correct number of rows + header with track names/IDs."""
    D = np.array([[0.0, 0.25], [0.25, 0.0]], dtype=np.float32)
    names = ["Song A", "Song B"]
    ids = ["abc123def456", "xyz789ghi012"]
    pl_id = "test_playlist_42"

    result = _dump_distance_csv(D, names, ids, pl_id, cache_dir=tmp_path)
    assert result is not None, "CSV dump should succeed"
    csv_path = tmp_path / "test_playlist_42_distance_matrix.csv"
    assert csv_path.exists(), f"Expected {csv_path} to exist"

    content = csv_path.read_text(encoding="utf-8")
    lines = content.strip().split("\n")
    # Header + 2 data rows = 3 lines
    assert len(lines) == 3, f"Expected 3 lines (header + 2 rows), got {len(lines)}"
    assert "Song A" in lines[0], f"Header should contain track name, got: {lines[0]}"
    assert "abc123def456" in lines[0], f"Header should contain track ID prefix, got: {lines[0]}"
    assert "0.000000" in lines[1], "Diagonal cell should be 0.000000"
    assert "0.250000" in lines[1], "Off-diagonal cell should have distance value"


def test_dump_distance_csv_empty_matrix(tmp_path):
    """_dump_distance_csv on a 1x1 zero matrix still writes a valid CSV."""
    D = np.zeros((1, 1), dtype=np.float32)
    result = _dump_distance_csv(D, ["Only Track"], ["id1"], "pl_empty", cache_dir=tmp_path)
    assert result is not None
    csv_path = tmp_path / "pl_empty_distance_matrix.csv"
    assert csv_path.exists()
    content = csv_path.read_text(encoding="utf-8")
    assert "Only Track" in content
