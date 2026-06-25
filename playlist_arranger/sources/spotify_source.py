"""Spotify integration: auth, playlists, playback, reordering."""

import os
import time
import logging

logger = logging.getLogger(__name__)

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth

    HAS_SPOTIPY = True
except ImportError:
    HAS_SPOTIPY = False

from playlist_arranger.config import (
    SPOTIFY_SCOPE,
    REDIRECT_URI,
    CACHE_DIR_DEFAULT,
)


def is_configured() -> bool:
    """Returns True if Spotify credentials are set in env."""
    return bool(
        os.getenv("SPOTIPY_CLIENT_ID") and os.getenv("SPOTIPY_CLIENT_SECRET")
    )


def _spotify_request_with_retries(sp, method, path, payload=None, max_retries=5):
    """Spotify API call with retries for 429/5xx errors. Refreshes token on each retry."""
    import requests as _req

    url = f"https://api.spotify.com/v1/{path.lstrip('/')}"
    backoff = 1.0
    for attempt in range(max_retries):
        try:
            # Refresh token on every attempt (may have expired)
            token = sp.auth_manager.get_access_token(as_dict=False)
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }

            if method.upper() == "POST":
                resp = _req.post(url, headers=headers, json=payload, timeout=30)
            elif method.upper() == "PUT":
                resp = _req.put(url, headers=headers, json=payload, timeout=30)
            elif method.upper() == "GET":
                resp = _req.get(url, headers=headers, timeout=30)
            else:
                raise ValueError(f"Unsupported method: {method}")

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 5)) + 1
                time.sleep(wait)
                continue
            if resp.status_code in (500, 502, 503, 504):
                time.sleep(backoff)
                backoff = min(backoff * 2, 20)
                continue
            if not resp.ok:
                return None, f"{resp.status_code} {resp.text[:200]}"
            return resp.json() if resp.text else {}, None
        except Exception:
            time.sleep(backoff)
            backoff = min(backoff * 2, 20)
    return None, "max retries exceeded"


def init_spotify(progress_cb=None):
    """Initialize Spotify client. progress_cb(msg) for UI feedback."""
    if not HAS_SPOTIPY:
        raise ImportError("spotipy package not installed")

    if progress_cb:
        progress_cb("Connecting to Spotify API...")

    sp = spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            scope=SPOTIFY_SCOPE,
            redirect_uri=REDIRECT_URI,
            open_browser=True,
        )
    )
    user = sp.current_user()
    if progress_cb:
        progress_cb(f"Authenticated as: {user['display_name']} ({user['id']})")
    return sp, user["id"]


def get_own_playlists(sp, user_id):
    """Fetch only playlists owned by the current user (paginates fully)."""
    playlists = []
    limit = 50
    offset = 0
    while True:
        result = sp.current_user_playlists(limit=limit, offset=offset)
        items = result.get("items") or []
        for pl in items:
            if pl and pl.get("owner", {}).get("id") == user_id:
                playlists.append(pl)
        if not result.get("next"):
            break
        offset += limit
        time.sleep(0.2)
    return playlists


def get_playlist_tracks(sp, playlist_id):
    """Fetch all tracks from a playlist. Skips local files, episodes, and null items."""
    tracks = []
    limit = 100
    offset = 0

    while True:
        try:
            result = sp.playlist_items(playlist_id, limit=limit, offset=offset)
        except Exception as exc:
            raise RuntimeError(f"API error fetching tracks: {exc}") from exc

        items = result.get("items") or []

        for item in items:
            if not item:
                continue
            t = item.get("item")
            if not t:
                continue
            if item.get("is_local"):
                continue
            if t.get("type") != "track":
                continue
            tid = t.get("id")
            if not tid:
                continue
            tracks.append(
                {
                    "id": tid,
                    "name": t.get("name", "Unknown"),
                    "artist": ", ".join(
                        a["name"] for a in (t.get("artists") or [])
                    ),
                    "album": (t.get("album") or {}).get("name", "Unknown"),
                    "duration_ms": t.get("duration_ms", 0),
                }
            )

        if not result.get("next"):
            break
        offset += limit
        time.sleep(0.3)

    return tracks


def play_track_on_device(sp, track_uri, device_id=None):
    """Start playback of a specific track URI on the given Spotify device."""
    try:
        if device_id:
            sp.start_playback(device_id=device_id, uris=[track_uri])
        else:
            sp.start_playback(uris=[track_uri])
    except Exception as exc:
        raise RuntimeError(f"Playback failed: {exc}") from exc


def reorder_playlist(sp, playlist_id, ordered_uris):
    """
    Reorder a playlist: PUT first 100 URIs (full replace), then POST remaining
    chunks of 100 with time.sleep(0.3) between each.
    """
    if not ordered_uris:
        return True, None

    # PUT first 100 (full replace)
    first_chunk = ordered_uris[:100]
    _, err = _spotify_request_with_retries(
        sp, "PUT", f"playlists/{playlist_id}/items", {"uris": first_chunk}
    )
    if err:
        return False, err

    # POST remaining chunks
    rest_chunks = [
        ordered_uris[i : i + 100] for i in range(100, len(ordered_uris), 100)
    ]
    for chunk in rest_chunks:
        _, err2 = _spotify_request_with_retries(
            sp, "POST", f"playlists/{playlist_id}/items", {"uris": chunk}
        )
        if err2:
            return False, err2
        time.sleep(0.3)

    return True, None


def create_playlist(sp, name, uris):
    """Create new playlist and add tracks in chunks."""
    new_pl, err = _spotify_request_with_retries(
        sp,
        "POST",
        "me/playlists",
        payload={"name": name, "public": False},
    )
    if err or not new_pl:
        return None, err

    for i in range(0, len(uris), 100):
        chunk = uris[i : i + 100]
        _, err2 = _spotify_request_with_retries(
            sp,
            "POST",
            f"playlists/{new_pl['id']}/items",
            payload={"uris": chunk},
        )
        if err2:
            return new_pl, err2
        time.sleep(0.3)

    return new_pl, None