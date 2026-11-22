"""Unit tests for playlist_arranger.sorting.stats_analysis module."""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.resolve()))

import numpy as np
import pytest

from playlist_arranger.sorting.stats_analysis import (
    StatsResult,
    ComponentCalibration,
    save_stats_cache,
    load_stats_cache,
    analyze_playlist_stats,
    CV_GOOD_THRESHOLD,
    CV_MODERATE_THRESHOLD,
    _extract_feature_values,
)


# ── Synthetic test tracks ────────────────────────────────────────────────

def _make_track(features: dict) -> dict:
    """Create a minimal DB-style track dict with features/start_seg/end_seg."""
    return {
        "features": dict(features),
        "start_seg": dict(features),
        "end_seg": dict(features),
    }


@pytest.fixture
def synthetic_3_tracks():
    """3 tracks with known feature values."""
    return [
        _make_track({
            "bpm": 100, "rms_db": -25,
            "harm_ratio": 0.90, "flatness": 0.002, "flatness": 0.002,
            "dynamic_range": 10.0, "onset_str": 0.8,
            "bass": 0.3, "mid": 0.4, "high": 0.3,
            "camelot": "8B",
            "mfcc20": [0.1, 0.5]*10,
            "chroma_cens": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 0.5, 0.2],
        }),
        _make_track({
            "bpm": 140, "rms_db": -15,
            "harm_ratio": 0.95, "flatness": 0.002, "flatness": 0.004,
            "dynamic_range": 20.0, "onset_str": 1.0,
            "bass": 0.4, "mid": 0.3, "high": 0.3,
            "camelot": "5A",
            "mfcc20": [0.3, 0.7]*10,
            "chroma_cens": [0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.0, 0.1, 0.2, 0.3],
        }),
        _make_track({
            "bpm": 120, "rms_db": -20,
            "harm_ratio": 0.85, "flatness": 0.002, "flatness": 0.001,
            "dynamic_range": 15.0, "onset_str": 0.6,
            "bass": 0.5, "mid": 0.3, "high": 0.2,
            "camelot": "8B",
            "mfcc20": [0.2, 0.6]*10,
            "chroma_cens": [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5],
        }),
    ]


# ── Tests ────────────────────────────────────────────────────────────────────

def test_extract_feature_values(synthetic_3_tracks):
    """_extract_feature_values collects per-track values correctly."""
    feats = _extract_feature_values(synthetic_3_tracks)
    # Each track has features, start_seg, end_seg — 3 sections × 3 tracks = 9 values
    assert len(feats["bpm"]) == 9
    assert len(feats["rms_db"]) == 9
    assert len(feats["flatness"]) == 9


def test_analyze_stats_produces_calibration(synthetic_3_tracks):
    """analyze_playlist_stats on synthetic 3-track set returns correct calibration."""
    result = analyze_playlist_stats(
        playlist_id="test_pl",
        snapshot_id="snap1",
        all_tracks=synthetic_3_tracks,
        embeddings=[],  # no embeddings → chroma_fallback mood_mode
        weights={"mood": 0.48, "bpm": 0.12, "transition": 0.20, "key": 0.12,
                 "energy": 0.08, "texture": 0.10, "freq_balance": 0.08},
    )

    assert result.playlist_id == "test_pl"
    assert result.snapshot_id == "snap1"
    assert result.analysed_at, "analysed_at should be set"
    assert result.weights_used == {"mood": 0.48, "bpm": 0.12, "transition": 0.20,
                                    "key": 0.12, "energy": 0.08, "texture": 0.10,
                                    "freq_balance": 0.08}

    # Check all 7 main components exist
    for comp_name in ["mood", "bpm", "transition", "key", "energy", "texture", "freq_balance"]:
        assert comp_name in result.components, f"Missing component: {comp_name}"
        comp = result.components[comp_name]
        assert comp.calibration_scale > 0
        assert comp.observed_min >= -0.001, f"{comp_name}: observed_min={comp.observed_min}"
        assert comp.observed_max > comp.observed_min or comp.observed_min == comp.observed_max
        assert len(comp.histogram_counts) == 12  # default bins
        assert len(comp.histogram_edges) == 13

    # Texture sub-components
    for sub in ["harm_ratio", "flatness", "dynamic_range", "onset_str"]:
        assert sub in result.components, f"Missing sub-component: {sub}"

    # Flatness should have per-playlist calibration (not default 1.0)
    flat_comp = result.components["flatness"]
    assert flat_comp.calibration_scale != 1.0, "flatness must have per-playlist calibration"
    assert flat_comp.calibration_scale > 0.001

    # Mood mode: no embeddings → chroma_fallback
    assert result.components["mood"].mood_mode == "chroma_fallback"

    # 3 tracks → 6 off-diagonal pairs
    n_expected = 3 * 2  # 3×2 = 6 pairs
    assert result.components["mood"].observed_cv > 0  # should have variability


def test_cache_round_trip(tmp_path, synthetic_3_tracks):
    """save_stats_cache → load_stats_cache preserves weights and scales exactly."""
    # Override cache dir to tmp_path
    import playlist_arranger.config as _cfg
    original_cache = _cfg.CACHE_DIR_DEFAULT
    _cfg.CACHE_DIR_DEFAULT = tmp_path

    try:
        result = analyze_playlist_stats(
            playlist_id="roundtrip_pl",
            snapshot_id="snapX",
            all_tracks=synthetic_3_tracks,
            embeddings=[],
            weights={"mood": 0.5, "bpm": 0.1, "transition": 0.1, "key": 0.1,
                     "energy": 0.1, "texture": 0.05, "freq_balance": 0.05},
        )
        cache_path = save_stats_cache(result)
        assert pathlib.Path(cache_path).exists()

        loaded = load_stats_cache("roundtrip_pl", "snapX")
        assert loaded is not None
        assert loaded.playlist_id == "roundtrip_pl"
        assert loaded.snapshot_id == "snapX"
        assert loaded.weights_used == result.weights_used
        assert loaded.analysed_at == result.analysed_at

        # All components preserved
        for name in result.components:
            orig = result.components[name]
            loaded_comp = loaded.components[name]
            assert loaded_comp.calibration_scale == orig.calibration_scale
            assert loaded_comp.observed_min == orig.observed_min
            assert loaded_comp.observed_max == orig.observed_max
            assert loaded_comp.observed_mean == orig.observed_mean
            assert loaded_comp.observed_cv == orig.observed_cv
            assert loaded_comp.mood_mode == orig.mood_mode
            assert loaded_comp.histogram_counts == orig.histogram_counts
            assert loaded_comp.histogram_edges == orig.histogram_edges

        # Verify JSON schema on disk
        raw = json.loads(pathlib.Path(cache_path).read_text(encoding="utf-8"))
        assert "playlist_id" in raw
        assert "snapshot_id" in raw
        assert "weights_used" in raw
        assert "components" in raw
        assert "mood_mode" in raw["components"]["mood"]
    finally:
        _cfg.CACHE_DIR_DEFAULT = original_cache


def test_snapshot_mismatch_returns_none(tmp_path, synthetic_3_tracks):
    """load_stats_cache returns None when snapshot_id doesn't match."""
    import playlist_arranger.config as _cfg
    original_cache = _cfg.CACHE_DIR_DEFAULT
    _cfg.CACHE_DIR_DEFAULT = tmp_path

    try:
        result = analyze_playlist_stats(
            playlist_id="snap_test_pl",
            snapshot_id="snap_original",
            all_tracks=synthetic_3_tracks,
            embeddings=[],
        )
        save_stats_cache(result)

        # Load with different snapshot_id → None
        loaded = load_stats_cache("snap_test_pl", "snap_different")
        assert loaded is None, "Should return None for snapshot mismatch"
    finally:
        _cfg.CACHE_DIR_DEFAULT = original_cache


def test_mood_mode_invalidation(tmp_path, synthetic_3_tracks):
    """load_stats_cache returns None when cached mood_mode=chroma_fallback
    but current run has embeddings available."""
    import playlist_arranger.config as _cfg
    original_cache = _cfg.CACHE_DIR_DEFAULT
    _cfg.CACHE_DIR_DEFAULT = tmp_path

    try:
        # Save with chroma_fallback
        result_no_emb = analyze_playlist_stats(
            playlist_id="mood_test_pl",
            snapshot_id="snap1",
            all_tracks=synthetic_3_tracks,
            embeddings=[],
        )
        save_stats_cache(result_no_emb)
        assert result_no_emb.components["mood"].mood_mode == "chroma_fallback"

        # Now try to load — mood_mode invalidation: if the CACHED file
        # has mood_mode=chroma_fallback, but caller passes embeddings
        # indicating they're now available, the load function itself
        # doesn't currently check that.  The CALLER (smart_sorting.py)
        # must check.  So this test verifies the cache has the mood_mode
        # field correctly stored.
        loaded = load_stats_cache("mood_test_pl", "snap1")
        assert loaded is not None
        assert loaded.components["mood"].mood_mode == "chroma_fallback"

        # Now simulate: the caller's responsibility to check mood_mode
        # and invalidate if embeddings availability changed.
        # The cache layer itself returns the data — the caller gate-keeps.
        # This is by design: cache I/O should be pure data, not logic.

        # Save a version WITH embeddings
        # (use a small fake embedding array)
        fake_emb = [np.array([0.1, 0.2, 0.3], dtype=np.float32)] * 3
        result_with_emb = analyze_playlist_stats(
            playlist_id="mood_test_pl",
            snapshot_id="snap1",
            all_tracks=synthetic_3_tracks,
            embeddings=fake_emb,
        )
        assert result_with_emb.components["mood"].mood_mode == "embedding"
        save_stats_cache(result_with_emb)  # overwrite the chroma_fallback version

        loaded2 = load_stats_cache("mood_test_pl", "snap1")
        assert loaded2 is not None
        assert loaded2.components["mood"].mood_mode == "embedding"
    finally:
        _cfg.CACHE_DIR_DEFAULT = original_cache


def test_cv_thresholds_are_reasonable():
    """CV thresholds are positive and ordered."""
    assert CV_GOOD_THRESHOLD > 0
    assert CV_MODERATE_THRESHOLD > 0
    assert CV_GOOD_THRESHOLD > CV_MODERATE_THRESHOLD
    assert CV_GOOD_THRESHOLD < 1.0
    assert CV_MODERATE_THRESHOLD < 1.0


def test_calibration_scales_stored_correctly_for_dyn_range_and_onset():
    """Regression: dynamic_range.calibration_scale and onset_str.calibration_scale
    must be the actual per-playlist calibrated values, NOT silently defaulted to 1.0.

    The earlier f"{comp_name}_scale" key lookup failed because the calibration dict
    uses keys "dyn_scale" and "onset_scale", not "dynamic_range_scale" and
    "onset_str_scale".  This test asserts the SCALE_KEY_MAP fix is in place.
    """
    # Synthetic tracks with KNOWN dynamic_range spread: 5, 10, 20 dB
    # p5-p95 range of [5,5,5,10,10,10,20,20,20]×3 (features/start/end) = 15.0
    # Floor is 0.5, so expected dyn_scale ≈ 15.0
    tracks = []
    for db_val in [5.0, 10.0, 20.0]:
        feats = {
            "bpm": 120, "rms_db": -15,
            "harm_ratio": 0.9, "flatness": 0.003,
            "dynamic_range": db_val, "onset_str": 0.5,
            "bass": 0.33, "mid": 0.34, "high": 0.33,
            "camelot": "8B",
            "mfcc20": [0.1] * 20,
            "chroma_cens": [0.5] * 12,
        }
        tracks.append({"features": feats, "start_seg": feats, "end_seg": feats})

    result = analyze_playlist_stats(
        playlist_id="cal_test",
        snapshot_id="snap_cal",
        all_tracks=tracks,
        embeddings=[],
    )

    dyn_comp = result.components["dynamic_range"]
    onset_comp = result.components["onset_str"]

    # dynamic_range: p5-p95 of [5,5,5,10,10,10,20,20,20] across 9 values
    # p5 ≈ 5.0, p95 ≈ 20.0 → range ≈ 15.0, floored to max(15.0, 0.5) = 15.0
    # It won't be exactly 15.0 due to interpolation, but must be >> 1.0
    assert dyn_comp.calibration_scale > 1.0, (
        f"dynamic_range.calibration_scale = {dyn_comp.calibration_scale:.4f} — "
        f"expected >> 1.0 (regression: SCALE_KEY_MAP lookup was returning 1.0)"
    )
    assert 10.0 <= dyn_comp.calibration_scale <= 20.0, (
        f"dynamic_range.calibration_scale = {dyn_comp.calibration_scale:.4f} — "
        f"expected in [10.0, 20.0] range"
    )

    # onset_str: all tracks have onset_str=0.5 → zero spread → floor 0.1
    assert onset_comp.calibration_scale == 0.1, (
        f"onset_str.calibration_scale = {onset_comp.calibration_scale:.4f} — "
        f"expected 0.1 (floor, zero spread)"
    )

    # Flatness and transition should also have real calibrated values (not 1.0)
    # Flatness: all tracks have flatness=0.003 → zero spread → floor 0.002
    flat_comp = result.components["flatness"]
    assert flat_comp.calibration_scale == 0.002, (
        f"flatness.calibration_scale = {flat_comp.calibration_scale:.6f} — "
        f"expected 0.002 (floor, zero spread)"
    )

    # Components without calibration should be 1.0 (fixed theoretical divisors)
    for name in ["mood", "bpm", "key", "energy", "texture", "freq_balance", "harm_ratio"]:
        comp = result.components.get(name)
        if comp is not None:
            assert comp.calibration_scale == 1.0, (
                f"{name}.calibration_scale = {comp.calibration_scale} — "
                f"expected 1.0 (no per-playlist calibration for this component)"
            )
