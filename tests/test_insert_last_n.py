"""Unit tests for playlist_arranger.sorting.insert module.

Tests for ``insert_last_n_tracks``: greedy insertion with status-based filtering,
re-appension of NOT-OK tracks, delta-based optimization correctness,
and order stability.
"""

import sys
sys.path.insert(0, r"e:\Projects\Spotify_playlists\repository")

import numpy as np
import pytest

from unittest.mock import patch
from playlist_arranger.ui.state import STATUS_OK, STATUS_NOT_IN_DB, STATUS_NO_EMBEDDING

# ── insert.py imports get_track_status at module level, so we must patch
# the reference IN insert.py, not the original in ui.state.
_INSERT_STATUS_PATH = "playlist_arranger.sorting.insert.get_track_status"


# ── Mock helpers ─────────────────────────────────────────────────────────────

def _mock_track(tid, name="Track", uri=None):
    """Create a minimal UI-level track dict for testing."""
    return {
        "id": tid,
        "name": name,
        "artist": "Artist",
        "uri": uri or f"spotify:track:{tid}",
    }


def _make_fake_dist_fn(distances):
    """Create a callable dist(tid_a, tid_b) -> float from a lookup dict."""
    def _fn(a, b):
        return distances.get((a, b), distances.get((b, a), 0.01))
    return _fn


# ── Tests: _compute_total_and_neighbor_dists ─────────────────────────────────

class TestComputeTotalAndNeighborDists:
    def test_empty(self):
        from playlist_arranger.sorting.insert import _compute_total_and_neighbor_dists
        total, nd = _compute_total_and_neighbor_dists([], lambda a, b: 0.1)
        assert total == 0.0
        assert nd == []

    def test_single(self):
        from playlist_arranger.sorting.insert import _compute_total_and_neighbor_dists
        total, nd = _compute_total_and_neighbor_dists(["a"], lambda a, b: 0.1)
        assert total == 0.0
        assert nd == []

    def test_two_tracks(self):
        from playlist_arranger.sorting.insert import _compute_total_and_neighbor_dists
        dist_fn = _make_fake_dist_fn({("a", "b"): 0.3})
        total, nd = _compute_total_and_neighbor_dists(["a", "b"], dist_fn)
        assert total == 0.3
        assert nd == [0.3]

    def test_three_tracks(self):
        from playlist_arranger.sorting.insert import _compute_total_and_neighbor_dists
        dist_fn = _make_fake_dist_fn({("a", "b"): 0.3, ("b", "c"): 0.5})
        total, nd = _compute_total_and_neighbor_dists(["a", "b", "c"], dist_fn)
        assert total == pytest.approx(0.8)
        assert nd == [0.3, 0.5]


# ── Tests: _find_best_insertion_position ─────────────────────────────────────

class TestFindBestInsertionPosition:
    def test_empty_base(self):
        from playlist_arranger.sorting.insert import _find_best_insertion_position
        dist_fn = _make_fake_dist_fn({})
        pos, total = _find_best_insertion_position("x", [], dist_fn, 0.0, [])
        assert pos == 0
        assert total == 0.0

    def test_single_base_before_cheaper(self):
        from playlist_arranger.sorting.insert import _find_best_insertion_position
        dist_fn = _make_fake_dist_fn({("x", "a"): 0.1, ("a", "x"): 0.9})
        pos, total = _find_best_insertion_position("x", ["a"], dist_fn, 0.0, [])
        assert pos == 0
        assert total == pytest.approx(0.1)

    def test_single_base_after_cheaper(self):
        from playlist_arranger.sorting.insert import _find_best_insertion_position
        dist_fn = _make_fake_dist_fn({("x", "a"): 0.9, ("a", "x"): 0.1})
        pos, total = _find_best_insertion_position("x", ["a"], dist_fn, 0.0, [])
        assert pos == 1
        assert total == pytest.approx(0.1)

    def test_middle_insertion(self):
        from playlist_arranger.sorting.insert import _find_best_insertion_position
        dist_fn = _make_fake_dist_fn({
            ("a", "b"): 0.5,
            ("x", "a"): 0.8, ("b", "x"): 0.8,
            ("a", "x"): 0.1, ("x", "b"): 0.1,
        })
        pos, total = _find_best_insertion_position(
            "x", ["a", "b"], dist_fn, 0.5, [0.5]
        )
        assert pos == 1
        assert total == pytest.approx(0.2)

    def test_delta_matches_naive_recompute(self):
        """Regression: delta-based O(1) computation must match full recompute."""
        from playlist_arranger.sorting.insert import (
            _find_best_insertion_position,
            _compute_total_and_neighbor_dists,
        )
        dist_fn = _make_fake_dist_fn({
            ("a", "b"): 0.3, ("b", "c"): 0.4, ("c", "d"): 0.2,
            ("x", "a"): 0.7, ("x", "b"): 0.1, ("x", "c"): 0.5, ("x", "d"): 0.3,
            ("a", "x"): 0.6, ("b", "x"): 0.1, ("c", "x"): 0.5, ("d", "x"): 0.8,
        })
        base = ["a", "b", "c", "d"]
        current_total, nd = _compute_total_and_neighbor_dists(base, dist_fn)

        pos, delta_total = _find_best_insertion_position(
            "x", base, dist_fn, current_total, nd
        )

        best_by_recompute = None
        best_pos_recompute = None
        for i in range(len(base) + 1):
            candidate_list = base[:i] + ["x"] + base[i:]
            total, _ = _compute_total_and_neighbor_dists(candidate_list, dist_fn)
            if best_by_recompute is None or total < best_by_recompute:
                best_by_recompute = total
                best_pos_recompute = i

        assert pos == best_pos_recompute
        assert delta_total == pytest.approx(best_by_recompute)


# ── Tests: insert_last_n_tracks (full algorithm) ─────────────────────────────

class TestInsertLastNTracks:
    @pytest.fixture
    def mock_db(self):
        """Return a mock DB dict with minimal track entries."""
        db = {}
        for i in range(20):
            tid = f"tid_{i:02d}"
            db[tid] = {
                "name": f"Track {i}",
                "artist": f"Artist {i % 4}",
                "features": {"bpm": 120, "camelot": "1A"},
                "embedding_file": f"embeddings/{tid}.npy",
            }
        return db

    @pytest.fixture
    def mock_stats_cache(self):
        """Return a mock StatsResult with minimal calibration data."""
        from playlist_arranger.sorting.stats_analysis import (
            StatsResult, ComponentCalibration,
        )
        sr = StatsResult(playlist_id="test", snapshot_id="snap")
        for name in ("mood", "bpm", "transition", "key", "energy",
                      "texture", "freq_balance", "dynamic_range",
                      "onset_str", "flatness"):
            sr.components[name] = ComponentCalibration(
                name=name, calibration_scale=1.0, observed_cv=0.3,
            )
        sr.weights_used = {
            "mood": 0.35, "bpm": 0.15, "transition": 0.25,
            "key": 0.15, "energy": 0.10, "texture": 0.10,
            "freq_balance": 0.08,
        }
        return sr

    def make_playlist_tracks(self, n_ok, n_not_ok=0):
        """Create a list of UI-level track dicts."""
        tracks = []
        for i in range(n_ok):
            tid = f"tid_{i:02d}"
            tracks.append(_mock_track(tid, name=f"OK {i}"))
        for i in range(n_not_ok):
            tid = f"tid_notok_{i:02d}"
            tracks.append(_mock_track(tid, name=f"NOTOK {i}"))
        return tracks

    # ── Status-filtering tests ────────────────────────────────────────────

    def test_n_zero_returns_original(self, mock_db, mock_stats_cache):
        """N=0 returns the original playlist unchanged with cost 0."""
        from playlist_arranger.sorting.insert import insert_last_n_tracks
        with patch(_INSERT_STATUS_PATH, return_value=STATUS_OK):
            with patch("playlist_arranger.sorting.insert._load_embedding",
                       return_value=np.array([0.0])):
                tracks = self.make_playlist_tracks(5)
                result, cost = insert_last_n_tracks(tracks, 0, mock_db, mock_stats_cache)
                assert result == tracks
                assert cost == 0.0

    def test_n_not_ok_candidates_dropped(self, mock_db, mock_stats_cache):
        """NOT-OK tracks in the candidate set should be dropped."""
        from playlist_arranger.sorting.insert import insert_last_n_tracks
        tracks = self.make_playlist_tracks(5, 2)
        n = 7

        def mock_status(t):
            if "notok" in (t.get("id") or ""):
                return STATUS_NOT_IN_DB
            return STATUS_OK

        with patch(_INSERT_STATUS_PATH, side_effect=mock_status):
            with patch("playlist_arranger.sorting.insert._load_embedding",
                       return_value=np.array([0.0])):
                result, _ = insert_last_n_tracks(tracks, n, mock_db, mock_stats_cache)

        not_ok_ids = [t["id"] for t in tracks if "notok" in (t.get("id") or "")]
        last_ids = [t["id"] for t in result[-len(not_ok_ids):]]
        assert last_ids == not_ok_ids

    def test_not_ok_base_tracks_dropped_from_base(self, mock_db, mock_stats_cache):
        """NOT-OK base tracks should be dropped from base before insertion."""
        from playlist_arranger.sorting.insert import insert_last_n_tracks
        tracks = [
            _mock_track("tid_00", name="OK0"),
            _mock_track("tid_notok", name="Bad"),
            _mock_track("tid_01", name="OK1"),
            _mock_track("tid_02", name="New"),
        ]

        def mock_status(t):
            if "notok" in (t.get("id") or ""):
                return STATUS_NO_EMBEDDING
            return STATUS_OK

        with patch(_INSERT_STATUS_PATH, side_effect=mock_status):
            with patch("playlist_arranger.sorting.insert._load_embedding",
                       return_value=np.array([0.0])):
                result, _ = insert_last_n_tracks(tracks, 1, mock_db, mock_stats_cache)

        not_ok_id = "tid_notok"
        result_ids = [t["id"] for t in result]
        assert result_ids[-1] == not_ok_id

    def test_missing_tracks_reappended_at_end(self, mock_db, mock_stats_cache):
        """Step 5: tracks in original but missing from result appended in order."""
        from playlist_arranger.sorting.insert import insert_last_n_tracks
        tracks = self.make_playlist_tracks(5, 2)
        n = 7

        def mock_status(t):
            if "notok" in (t.get("id") or ""):
                return STATUS_NO_EMBEDDING
            return STATUS_OK

        with patch(_INSERT_STATUS_PATH, side_effect=mock_status):
            with patch("playlist_arranger.sorting.insert._load_embedding",
                       return_value=np.array([0.0])):
                result, _ = insert_last_n_tracks(tracks, n, mock_db, mock_stats_cache)

        result_ids = set(t["id"] for t in result)
        original_ids = set(t["id"] for t in tracks)
        assert result_ids == original_ids

        not_ok_ids = ["tid_notok_00", "tid_notok_01"]
        last_two = [t["id"] for t in result[-2:]]
        assert last_two == not_ok_ids

    def test_order_stability_base_untouched(self, mock_db, mock_stats_cache):
        """Tracks in base should retain their relative order after insertion."""
        from playlist_arranger.sorting.insert import insert_last_n_tracks
        tracks = [
            _mock_track("tid_00", name="OK0"),
            _mock_track("tid_01", name="OK1"),
            _mock_track("tid_02", name="OK2"),
            _mock_track("tid_03", name="New"),
        ]

        with patch(_INSERT_STATUS_PATH, return_value=STATUS_OK):
            with patch("playlist_arranger.sorting.insert._load_embedding",
                       return_value=np.array([0.0])):
                result, _ = insert_last_n_tracks(tracks, 1, mock_db, mock_stats_cache)

        result_ids = [t["id"] for t in result]
        base_ids = ["tid_00", "tid_01", "tid_02"]
        base_positions = [result_ids.index(bid) for bid in base_ids]
        assert base_positions == sorted(base_positions), (
            f"Base track order violated: {result_ids}"
        )

    def test_all_not_ok_base_no_candidates(self, mock_db, mock_stats_cache):
        """When no tracks are STATUS_OK, all remain in original order."""
        from playlist_arranger.sorting.insert import insert_last_n_tracks
        tracks = self.make_playlist_tracks(0)
        for i in range(5):
            tracks.append(_mock_track(f"bad_{i:02d}"))

        def mock_status(t):
            return STATUS_NOT_IN_DB

        with patch(_INSERT_STATUS_PATH, side_effect=mock_status):
            with patch("playlist_arranger.sorting.insert._load_embedding",
                       return_value=np.array([0.0])):
                result, _ = insert_last_n_tracks(tracks, 3, mock_db, mock_stats_cache)

        assert [t["id"] for t in result] == [t["id"] for t in tracks]

    # ── Stats cache validation tests ───────────────────────────────────────

    def test_raises_if_no_stats_cache(self, mock_db):
        from playlist_arranger.sorting.insert import insert_last_n_tracks
        with pytest.raises(RuntimeError, match="stats_cache is required"):
            insert_last_n_tracks([], 1, mock_db, None)

    def test_raises_if_missing_flatness(self, mock_db):
        from playlist_arranger.sorting.insert import insert_last_n_tracks
        from playlist_arranger.sorting.stats_analysis import StatsResult
        bad_cache = StatsResult(playlist_id="test", snapshot_id="snap")
        with pytest.raises(RuntimeError, match="flatness calibration"):
            insert_last_n_tracks([], 1, mock_db, bad_cache)

    # ── Button enable/disable logic (UI-level test via _validate_insert_n) ──

    def test_validate_insert_n_positive(self):
        """_validate_insert_n returns True for valid positive integers."""
        from playlist_arranger.ui.pages.smart_sorting import _validate_insert_n
        with patch("playlist_arranger.ui.pages.smart_sorting._insert_n_input") as m:
            m.value = 3
            assert _validate_insert_n() is True
            m.value = 1.0
            assert _validate_insert_n() is True

    def test_validate_insert_n_invalid(self):
        """_validate_insert_n returns False for empty/zero/negative/non-integer."""
        from playlist_arranger.ui.pages.smart_sorting import _validate_insert_n

        with patch("playlist_arranger.ui.pages.smart_sorting._insert_n_input") as m:
            m.value = None
            assert _validate_insert_n() is False
        with patch("playlist_arranger.ui.pages.smart_sorting._insert_n_input") as m:
            m.value = 0
            assert _validate_insert_n() is False
        with patch("playlist_arranger.ui.pages.smart_sorting._insert_n_input") as m:
            m.value = -1
            assert _validate_insert_n() is False
        with patch("playlist_arranger.ui.pages.smart_sorting._insert_n_input") as m:
            m.value = 2.5
            assert _validate_insert_n() is False
        with patch("playlist_arranger.ui.pages.smart_sorting._insert_n_input") as m:
            m.value = "abc"
            assert _validate_insert_n() is False

        with patch("playlist_arranger.ui.pages.smart_sorting._insert_n_input", None):
            assert _validate_insert_n() is False

    # ── Integration: weights restored after run ────────────────────────────

    def test_weights_restored_after_insert(self, mock_db, mock_stats_cache):
        """Global config weights/penalties should be restored after insert."""
        import playlist_arranger.config as _cfg
        from playlist_arranger.sorting.insert import insert_last_n_tracks

        original_weights = dict(_cfg.WEIGHTS)
        original_artist = _cfg.ARTIST_PENALTY
        original_album = _cfg.ALBUM_PENALTY

        tracks = self.make_playlist_tracks(3)

        with patch(_INSERT_STATUS_PATH, return_value=STATUS_OK):
            with patch("playlist_arranger.sorting.insert._load_embedding",
                       return_value=np.array([0.0])):
                insert_last_n_tracks(tracks, 1, mock_db, mock_stats_cache)

        assert dict(_cfg.WEIGHTS) == original_weights
        assert _cfg.ARTIST_PENALTY == original_artist
        assert _cfg.ALBUM_PENALTY == original_album