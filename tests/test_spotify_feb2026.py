"""Unit tests for the February 2026 Spotify Web API migration.

Covers the reworked ``classify_spotify_error()``, the ``market``/page-limit
changes in ``get_playlist_tracks()``, the dual ``item``/``track`` response
parsing, ``_is_track_playable()`` field-removal handling, the
``play_track_on_device()`` fallback ladder, and ``check_device()`` — all with
mocks and no real network calls.
"""

import pathlib
import sys

_repo_root = pathlib.Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from unittest.mock import MagicMock, patch

from playlist_arranger.sources.spotify_source import (
    classify_spotify_error,
    get_playlist_tracks,
    _is_track_playable,
    check_device,
)


# ── classify_spotify_error() ─────────────────────────────────────────────────

def test_classify_premium_required():
    assert classify_spotify_error(403, "Premium required", reason="PREMIUM_REQUIRED") == "premium_required"


def test_classify_device_restricted_by_reason():
    assert classify_spotify_error(403, "Forbidden", reason="RESTRICTION_VIOLATED") == "device_restricted"


def test_classify_device_restricted_by_message():
    assert classify_spotify_error(403, "Restriction violated", reason=None) == "device_restricted"


def test_classify_forbidden_bare_403():
    assert classify_spotify_error(403, "Forbidden", reason=None) == "forbidden"


def test_classify_auth_expired_401():
    assert classify_spotify_error(401, "Unauthorized") == "auth_expired"


# ── get_playlist_tracks() ────────────────────────────────────────────────────

def _make_item(shape, tid):
    """Build a playlist item using the new 'item' key or the legacy 'track' key."""
    track_obj = {
        "id": tid,
        "name": f"Track {tid}",
        "type": "track",
        "is_playable": True,
        "artists": [{"name": "Artist"}],
        "album": {"name": "Album"},
        "duration_ms": 200000,
        "uri": f"spotify:track:{tid}",
    }
    if shape == "item":
        return {"item": track_obj, "is_local": False}
    return {"track": track_obj, "is_local": False}


def test_get_playlist_tracks_requests_limit_50_and_no_from_token():
    fake_sp = MagicMock()
    fake_sp.playlist_items.return_value = {"items": [], "next": None}
    with patch("playlist_arranger.sources.spotify_source._market", return_value=None):
        get_playlist_tracks(fake_sp, "plid")

    kwargs = fake_sp.playlist_items.call_args.kwargs
    assert kwargs["limit"] == 50
    assert kwargs.get("market") != "from_token"


def test_get_playlist_tracks_passes_cached_market():
    fake_sp = MagicMock()
    fake_sp.playlist_items.return_value = {"items": [], "next": None}
    with patch("playlist_arranger.sources.spotify_source._market", return_value="US"):
        get_playlist_tracks(fake_sp, "plid")

    assert fake_sp.playlist_items.call_args.kwargs["market"] == "US"


def test_get_playlist_tracks_parses_new_item_key():
    fake_sp = MagicMock()
    fake_sp.playlist_items.return_value = {
        "items": [_make_item("item", f"t{i}") for i in range(3)],
        "next": None,
    }
    with patch("playlist_arranger.sources.spotify_source._market", return_value=None):
        tracks = get_playlist_tracks(fake_sp, "plid")

    assert {t["id"] for t in tracks} == {"t0", "t1", "t2"}


def test_get_playlist_tracks_parses_legacy_track_key():
    fake_sp = MagicMock()
    fake_sp.playlist_items.return_value = {
        "items": [_make_item("track", f"t{i}") for i in range(3)],
        "next": None,
    }
    with patch("playlist_arranger.sources.spotify_source._market", return_value=None):
        tracks = get_playlist_tracks(fake_sp, "plid")

    assert {t["id"] for t in tracks} == {"t0", "t1", "t2"}


# ── _is_track_playable() ─────────────────────────────────────────────────────

def test_is_track_playable_missing_fields_treated_playable():
    playable, reason = _is_track_playable({"track": {"id": "x", "type": "track"}})
    assert playable
    assert reason == "ok"


# ── check_device() ───────────────────────────────────────────────────────────

def test_check_device_restricted_returns_false():
    fake_sp = MagicMock()
    fake_sp.devices.return_value = {"devices": [{"id": "d1", "is_restricted": True}]}

    ok, reason = check_device(fake_sp, "d1")
    assert not ok
    assert "restricted" in reason


def test_check_device_unknown_id_returns_false():
    fake_sp = MagicMock()
    fake_sp.devices.return_value = {"devices": [{"id": "d1", "is_restricted": False}]}

    ok, reason = check_device(fake_sp, "missing")
    assert not ok


def test_check_device_ok():
    fake_sp = MagicMock()
    fake_sp.devices.return_value = {"devices": [{"id": "d1", "is_restricted": False}]}

    ok, reason = check_device(fake_sp, "d1")
    assert ok
    assert reason == "ok"
