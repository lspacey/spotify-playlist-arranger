"""Regression test: get_playlist_tracks() must read item.get('track'), not item.get('item')."""

from unittest.mock import MagicMock
from playlist_arranger.sources.spotify_source import get_playlist_tracks


def _fake_playlist_items_response(n=3):
    """Mimics the REAL Spotify playlist_items() response shape — track
    object nested under the 'track' key, not 'item'."""
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
    wrapper level) must still be correctly skipped — verifies Task 2's
    guidance that item.get('is_local') is correct as-is."""
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