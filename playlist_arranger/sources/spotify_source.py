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


def classify_spotify_error(status_code: int | None, message: str) -> str:
    """Map HTTP status / exception info to a short error category string.

    Returns one of:
        "auth_expired"  (401, 403)
        "not_found"     (404)
        "rate_limited"  (429) — only when retries already exhausted
        "server_error"  (5xx, retries exhausted)
        "network_error" (connection/timeout, unexpected requests exceptions)
        "unknown"       (fallback — anything unclassified)
    """
    if status_code is None:
        # No HTTP response → network-level issue
        return "network_error"
    if 400 <= status_code < 500:
        if status_code in (401, 403):
            return "auth_expired"
        if status_code == 404:
            return "not_found"
        if status_code == 429:
            return "rate_limited"
    if 500 <= status_code < 600:
        return "server_error"
    # Non-HTTP fallback: the message string may contain exception info
    msg_lower = message.lower()
    if "timeout" in msg_lower or "connection" in msg_lower or "network" in msg_lower:
        return "network_error"
    return "unknown"


ERROR_USER_MESSAGES = {
    "auth_expired": "Your Spotify session expired — please reconnect.",
    "not_found": "Playlist no longer exists or you lost access to it.",
    "rate_limited": "Spotify rate-limited the request — try again in a minute.",
    "server_error": "Spotify API is having issues — try again shortly.",
    "network_error": "Spotify API is having issues — try again shortly.",
    "unknown": None,  # use raw error message
}


def _spotify_request_with_retries(sp, method, path, payload=None, max_retries=5):
    """Spotify API call with retries for 429/5xx errors.

    Returns (data, error_dict) where error_dict is ``{"message": str, "type": str}``
    or None on success.  The ``"type"`` key is a classify_spotify_error category.
    """
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
                err_type = classify_spotify_error(resp.status_code, resp.text[:200])
                return None, {
                    "message": f"{resp.status_code} {resp.text[:200]}",
                    "type": err_type,
                    "status_code": resp.status_code,
                }
            return resp.json() if resp.text else {}, None
        except Exception as exc:
            time.sleep(backoff)
            backoff = min(backoff * 2, 20)
            if attempt == max_retries - 1:
                return None, {
                    "message": str(exc),
                    "type": classify_spotify_error(None, str(exc)),
                    "status_code": None,
                }

    return None, {
        "message": "max retries exceeded",
        "type": classify_spotify_error(503, "max retries exceeded"),
        "status_code": 503,
    }


class SpotifyCallProxy:
    """Thin proxy wrapping spotipy.Spotify to track API call count."""

    def __init__(self, sp):
        self._sp = sp

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        attr = getattr(self._sp, name)
        if callable(attr):

            def wrapper(*args, **kwargs):
                # Count the call through a global counter managed by the UI
                try:
                    from playlist_arranger.ui import state as _api_state

                    setattr(_api_state, "api_calls", getattr(_api_state, "api_calls", 0) + 1)
                except Exception:
                    pass
                return attr(*args, **kwargs)

            return wrapper
        return attr


def init_spotify(progress_cb=None):
    """Initialize Spotify OAuth, returns (SpotifyCallProxy, user_id) tuple.

    Always returns a valid 2-tuple on success.  Any failure raises an
    exception so the caller (``do_connect()``) can handle it uniformly
    in its ``try/except`` block — no ``return None`` ambiguity.
    """
    if not HAS_SPOTIPY:
        raise RuntimeError("spotipy is not installed — Spotify features unavailable")

    client_id = os.getenv("SPOTIPY_CLIENT_ID", "")
    client_secret = os.getenv("SPOTIPY_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        raise RuntimeError(
            "Spotify client ID/secret not configured — set SPOTIPY_CLIENT_ID "
            "and SPOTIPY_CLIENT_SECRET in .env"
        )

    if progress_cb:
        progress_cb("Authenticating with Spotify...")

    cache_path = str(CACHE_DIR_DEFAULT / ".spotify_cache")
    auth = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=REDIRECT_URI,
        scope=SPOTIFY_SCOPE,
        cache_path=cache_path,
        open_browser=True,
    )
    sp = spotipy.Spotify(auth_manager=auth)
    user = sp.current_user()
    return SpotifyCallProxy(sp), user["id"]


def get_own_playlists(sp, user_id):
    """Get all playlists owned by user_id. Returns {id: name} dict."""
    playlists = []
    limit = 50
    offset = 0
    while True:
        result = sp.current_user_playlists(limit=limit, offset=offset)
        items = result.get("items") or []
        for pl in items:
            if pl.get("owner", {}).get("id") == user_id:
                playlists.append({"id": pl["id"], "name": pl["name"][:80]})
        if not result.get("next"):
            break
        offset += limit
        time.sleep(0.3)
    return playlists


def _is_track_playable(item: dict) -> bool:
    """Check whether a Spotify track item is playable."""
    track = item.get("track") or item
    if track.get("is_local"):
        return False
    if track.get("type") != "track":
        return False
    if track.get("is_playable") is False:
        return False
    restrictions = track.get("restrictions")
    if restrictions:
        return False
    if "is_playable" not in track and "item" in item:
        available_markets = track.get("available_markets")
        if available_markets is not None and len(available_markets) == 0:
            return False
    return True


def get_playlist_tracks(sp, playlist_id):
    """Fetch all tracks from a playlist."""
    tracks = []
    limit = 100
    offset = 0
    skipped_unplayable = 0

    while True:
        try:
            result = sp.playlist_items(playlist_id, limit=limit, offset=offset,
                                       market="from_token")
        except Exception as exc:
            raise RuntimeError(f"API error fetching tracks: {exc}") from exc

        items = result.get("items") or []

        for item in items:
            if not item:
                continue
            t = item.get("track")
            if not t:
                skipped_unplayable += 1
                continue
            if item.get("is_local"):  # top-level field on the wrapper item, NOT inside "track" — correct as-is
                skipped_unplayable += 1
                continue
            if t.get("type") != "track":
                skipped_unplayable += 1
                continue
            tid = t.get("id")
            if not tid:
                skipped_unplayable += 1
                continue
            if not _is_track_playable(item):
                skipped_unplayable += 1
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
                    "uri": t.get("uri", f"spotify:track:{tid}"),
                }
            )

        if not result.get("next"):
            break
        offset += limit
        time.sleep(0.3)

    if skipped_unplayable:
        logger.info("Skipped %d unplayable/unavailable track(s) from playlist %s",
                     skipped_unplayable, playlist_id[:8] if playlist_id else "?")
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


def reorder_playlist(sp, playlist_id, ordered_uris) -> dict:
    """Replace a playlist's track order with the given URIs.

    PUT first 100 URIs (full replace), then POST remaining chunks of 100
    with ``time.sleep(0.3)`` between each.

    Returns
    -------
    dict
        {
            "success": bool,
            "chunks_total": int,
            "chunks_completed": int,
            "tracks_saved": int,
            "error": dict | None,      # {"message": str, "type": str, "status_code": int|None}
            "failed_chunk_index": int | None,
        }
    """
    total = len(ordered_uris)
    if total == 0:
        return {
            "success": True,
            "chunks_total": 0,
            "chunks_completed": 0,
            "tracks_saved": 0,
            "error": None,
            "failed_chunk_index": None,
        }

    # PUT first 100 (full replace)
    first_chunk = ordered_uris[:100]
    _, err = _spotify_request_with_retries(
        sp, "PUT", f"playlists/{playlist_id}/items", {"uris": first_chunk}
    )
    if err:
        return {
            "success": False,
            "chunks_total": 1 + (max(0, total - 100) + 99) // 100,
            "chunks_completed": 0,
            "tracks_saved": 0,
            "error": err,
            "failed_chunk_index": 0,
        }

    # POST remaining chunks
    rest_chunks = [
        ordered_uris[i : i + 100] for i in range(100, total, 100)
    ]
    for idx, chunk in enumerate(rest_chunks):
        _, err2 = _spotify_request_with_retries(
            sp, "POST", f"playlists/{playlist_id}/items", {"uris": chunk}
        )
        if err2:
            return {
                "success": False,
                "chunks_total": 1 + len(rest_chunks),
                "chunks_completed": 1 + idx,  # PUT succeeded + idx prior POSTs
                "tracks_saved": min(100, total) + idx * 100,
                "error": err2,
                "failed_chunk_index": idx + 1,  # 0 = PUT, 1+ = POST chunk
            }
        time.sleep(0.3)

    return {
        "success": True,
        "chunks_total": 1 + len(rest_chunks),
        "chunks_completed": 1 + len(rest_chunks),
        "tracks_saved": total,
        "error": None,
        "failed_chunk_index": None,
    }


def create_playlist(sp, name, uris) -> dict:
    """Create a new playlist and add tracks in chunks.

    Returns
    -------
    dict
        {
            "success": bool,
            "playlist": dict | None,   # created playlist object (may be present even on failure)
            "chunks_total": int,
            "chunks_completed": int,
            "tracks_saved": int,
            "error": dict | None,      # {"message": str, "type": str, "status_code": int|None}
            "failed_chunk_index": int | None,
        }
    """
    new_pl, err = _spotify_request_with_retries(
        sp,
        "POST",
        "me/playlists",
        payload={"name": name, "public": False},
    )
    if err or not new_pl:
        return {
            "success": False,
            "playlist": new_pl if isinstance(new_pl, dict) else None,
            "chunks_total": 0,
            "chunks_completed": 0,
            "tracks_saved": 0,
            "error": err or {"message": "unknown playlist creation error", "type": "unknown"},
            "failed_chunk_index": None,
        }

    total_uris = len(uris)
    chunk_count = (total_uris + 99) // 100  # ceil division
    for i in range(0, total_uris, 100):
        chunk = uris[i : i + 100]
        _, err2 = _spotify_request_with_retries(
            sp,
            "POST",
            f"playlists/{new_pl['id']}/items",
            payload={"uris": chunk},
        )
        chunk_idx = i // 100
        if err2:
            return {
                "success": False,
                "playlist": new_pl,
                "chunks_total": chunk_count,
                "chunks_completed": chunk_idx,  # 0-based: 0 chunks if first fails
                "tracks_saved": i,  # tracks saved BEFORE this failed chunk
                "error": err2,
                "failed_chunk_index": chunk_idx,
            }
        time.sleep(0.3)

    return {
        "success": True,
        "playlist": new_pl,
        "chunks_total": chunk_count,
        "chunks_completed": chunk_count,
        "tracks_saved": total_uris,
        "error": None,
        "failed_chunk_index": None,
    }