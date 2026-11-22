"""Unit tests for playlist_arranger.sources.spotify_source module.

Covers ``_is_track_playable()`` and ``get_playlist_tracks()`` filtering
logic with mocked Spotify API responses.
"""

import sys
sys.path.insert(0, r"e:\Projects\Spotify_playlists\repository")

from unittest.mock import MagicMock
from playlist_arranger.sources.spotify_source import (
    _is_track_playable,
    get_playlist_tracks,
)


# ── _is_track_playable() unit tests ──────────────────────────────────────────

def test_is_track_playable_none_track():
    """track=None inside item should be unplayable."""
    playable, reason = _is_track_playable({"track": None})
    assert not playable
    assert "None" in reason


def test_is_track_playable_is_local_true():
    """is_local=True on the item wrapper (NOT inside track sub-object) should
    be unplayable — Spotify places the local-file flag at the item level."""
    playable, reason = _is_track_playable({"track": {"id": "x", "type": "track"}, "is_local": True})
    assert not playable
    assert "is_local" in reason


def test_is_track_playable_is_playable_false():
    """is_playable=False should be unplayable."""
    playable, reason = _is_track_playable(
        {"track": {"id": "x", "type": "track", "is_playable": False}},
        market="from_token",
    )
    assert not playable
    assert "is_playable=False" in reason
    assert "from_token" in reason


def test_is_track_playable_restricted():
    """Tracks with restrictions should be unplayable."""
    playable, reason = _is_track_playable(
        {"track": {"id": "x", "type": "track", "is_playable": True,
                    "restrictions": [{"reason": "market"}]}},
    )
    assert not playable
    assert "restrictions" in reason


def test_is_track_playable_empty_markets_no_is_playable_field():
    """When is_playable is missing despite market param, treat as playable
    (the absence is a data-quality issue, not deliberate unavailability).
    This is the KEY regression test for the bug being fixed."""
    playable, reason = _is_track_playable(
        {"track": {"id": "x", "type": "track", "available_markets": []}},
        market="from_token",
    )
    assert playable, (
        f"is_playable absent with market=from_token must NOT be skipped; "
        f"got reason={reason!r}"
    )
    assert reason == "ok"


def test_is_track_playable_normal_true():
    """Normal playable track should be playable."""
    playable, reason = _is_track_playable(
        {"track": {"id": "x", "type": "track", "is_playable": True,
                    "available_markets": ["US"]}},
    )
    assert playable
    assert reason == "ok"


def test_is_track_playable_cached_flat_dict_defaults_true():
    """A flat cached dict with the expected 'track' wrapper."""
    playable, reason = _is_track_playable(
        {"track": {"id": "x", "name": "Song", "type": "track",
                    "is_playable": True}},
    )
    assert playable
    assert reason == "ok"


def test_is_track_playable_market_passed_to_reason():
    """The market string is included in the skip reason for is_playable=False."""
    playable, reason = _is_track_playable(
        {"track": {"id": "a", "type": "track", "is_playable": False}},
        market="US",
    )
    assert not playable
    assert "market=US" in reason


# ── get_playlist_tracks() integration tests ──────────────────────────────────

def _fake_playlist_items_response(n=3):
    """Build synthetic playlist items for unit testing.

    The real Spotify Web API nests the track object under the ``"item"``
    key, but ``get_playlist_tracks()`` checks both ``item.get("track")``
    and ``item.get("item")`` — so using ``"track"`` here is a convenient
    test convention that exercises both code paths without changing
    existing test expectations."""
    items = []
    for i in range(n):
        items.append({
            "track": {
                "id": f"tid{i}",
                "name": f"Track {i}",
                "type": "track",
                "is_local": False,
                "is_playable": True,
                "artists": [{"name": "Artist"}],
                "album": {"name": "Album"},
                "duration_ms": 200000,
                "uri": f"spotify:track:tid{i}",
                "available_markets": ["US"],
            },
            "is_local": False,
            "added_at": "2026-01-01T00:00:00Z",
        })
    return {"items": items, "next": None}


def test_get_playlist_tracks_reads_track_key_not_item_key():
    """Regression test for the dormant bug where get_playlist_tracks()
    read item.get('item') instead of item.get('track'), causing 100% of
    tracks to be silently skipped as 'unplayable' whenever a real API
    fetch occurred (previously masked by cache hits)."""
    fake_sp = MagicMock()
    fake_sp.playlist_items.return_value = _fake_playlist_items_response(n=3)

    tracks = get_playlist_tracks(fake_sp, "fake_playlist_id")

    assert len(tracks) == 3, (
        f"Expected 3 playable tracks, got {len(tracks)} — "
        f"get_playlist_tracks() is likely still reading the wrong dict key"
    )
    assert {t["id"] for t in tracks} == {"tid0", "tid1", "tid2"}
    assert all(t["uri"].startswith("spotify:track:") for t in tracks)


def test_get_playlist_tracks_still_skips_local_tracks():
    """Sanity check: real local-file tracks (is_local=True at the
    wrapper level) must still be correctly skipped."""
    fake_sp = MagicMock()
    resp = _fake_playlist_items_response(n=2)
    # Mark first track as local at the WRAPPER level
    resp["items"][0]["is_local"] = True
    fake_sp.playlist_items.return_value = resp

    tracks = get_playlist_tracks(fake_sp, "fake_playlist_id")

    assert len(tracks) == 1, f"Expected 1 track (local skipped), got {len(tracks)}"
    assert tracks[0]["id"] == "tid1"


def test_get_playlist_tracks_skips_unplayable_via_is_playable_flag():
    """Sanity check: tracks explicitly marked is_playable=False must
    still be skipped correctly after the key fix."""
    fake_sp = MagicMock()
    resp = _fake_playlist_items_response(n=2)
    # Mark first track as unplayable at the TRACK level
    resp["items"][0]["track"]["is_playable"] = False
    fake_sp.playlist_items.return_value = resp

    tracks = get_playlist_tracks(fake_sp, "fake_playlist_id")

    assert len(tracks) == 1, f"Expected 1 track (unplayable skipped), got {len(tracks)}"
    assert tracks[0]["id"] == "tid1"


def test_playlist_with_missing_is_playable_not_zero_tracks():
    """KEY REGRESSION: When is_playable is missing from all items (e.g.
    market parameter not passed or edge case), tracks must NOT be
    filtered out — this was the exact regression being fixed."""
    fake_sp = MagicMock()
    items = []
    for i in range(3):
        item = {
            "track": {
                "id": f"miss{i}",
                "name": f"Missing Playable {i}",
                "type": "track",
                "is_local": False,
                "artists": [{"name": "Artist"}],
                "album": {"name": "Album"},
                "duration_ms": 200000,
                "uri": f"spotify:track:miss{i}",
            },
            "is_local": False,
        }
        items.append(item)
    fake_sp.playlist_items.return_value = {"items": items, "next": None}

    tracks = get_playlist_tracks(fake_sp, "missing_is_playable_pl")

    assert len(tracks) == 3, (
        f"Expected 3 tracks (is_playable absent must NOT filter), got {len(tracks)}"
    )


def test_playlist_with_item_key_instead_of_track_key():
    """REGRESSION: Spotify REST API returns tracks under the 'item' key
    (not 'track').  Spotipy 2.26.0 passes the raw response through
    unchanged.  The code must read item.get('track') OR item.get('item')
    because the real API uses 'item', not 'track'."""
    fake_sp = MagicMock()
    items = []
    for i in range(3):
        item = {
            "added_at": "2026-01-01T00:00:00Z",
            "added_by": {"id": "user"},
            "is_local": False,
            "primary_color": None,
            "video_thumbnail": {"url": None},
            "item": {   # ← Real API uses 'item' key, NOT 'track'
                "id": f"real{i}",
                "name": f"Real Track {i}",
                "type": "track",
                "is_local": False,
                "is_playable": True,
                "artists": [{"name": "Artist"}],
                "album": {"name": "Album"},
                "duration_ms": 200000,
                "uri": f"spotify:track:real{i}",
                "available_markets": ["US"],
            },
        }
        items.append(item)
    fake_sp.playlist_items.return_value = {"items": items, "next": None}

    tracks = get_playlist_tracks(fake_sp, "item_key_playlist")

    assert len(tracks) == 3, (
        f"Expected 3 tracks from items using 'item' key (real API shape), "
        f"got {len(tracks)} — get_playlist_tracks() is reading the wrong key"
    )
    assert {t["id"] for t in tracks} == {"real0", "real1", "real2"}


def test_playlist_with_track_null_items_skipped():
    """Items with track: null (removed from catalog) should be skipped."""
    fake_sp = MagicMock()
    items = [
        {
            "track": {
                "id": "good1", "name": "Good Track", "type": "track",
                "is_local": False, "is_playable": True,
                "artists": [{"name": "A"}], "album": {"name": "B"},
                "duration_ms": 200000, "uri": "spotify:track:good1",
            },
            "is_local": False,
        },
        {"track": None},  # removed from catalog
        {
            "track": {
                "id": "good2", "name": "Good Track 2", "type": "track",
                "is_local": False, "is_playable": True,
                "artists": [{"name": "C"}], "album": {"name": "D"},
                "duration_ms": 200000, "uri": "spotify:track:good2",
            },
            "is_local": False,
        },
    ]
    fake_sp.playlist_items.return_value = {"items": items, "next": None}

    tracks = get_playlist_tracks(fake_sp, "null_track_pl")

    assert len(tracks) == 2, f"Expected 2 tracks (null track skipped), got {len(tracks)}"
    assert {t["id"] for t in tracks} == {"good1", "good2"}


# ── Test runner ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    passed = 0
    failed = 0
    tests = [
        test_is_track_playable_none_track,
        test_is_track_playable_is_local_true,
        test_is_track_playable_is_playable_false,
        test_is_track_playable_restricted,
        test_is_track_playable_empty_markets_no_is_playable_field,
        test_is_track_playable_normal_true,
        test_is_track_playable_cached_flat_dict_defaults_true,
        test_is_track_playable_market_passed_to_reason,
        test_get_playlist_tracks_reads_track_key_not_item_key,
        test_get_playlist_tracks_still_skips_local_tracks,
        test_get_playlist_tracks_skips_unplayable_via_is_playable_flag,
        test_playlist_with_missing_is_playable_not_zero_tracks,
        test_playlist_with_item_key_instead_of_track_key,
        test_playlist_with_track_null_items_skipped,
    ]
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL: {t.__name__} — {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR: {t.__name__} — {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{passed+failed} passed, {failed} failed")
    sys.exit(1 if failed > 0 else 0)