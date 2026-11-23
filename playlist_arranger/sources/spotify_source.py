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
    SPOTIFY_TRACE,
    PLAYBACK_CONFIRM_TIMEOUT_S,
    PLAYBACK_CONFIRM_INTERVAL_S,
    BATCH_START_CONFIRM_TIMEOUT_S,
    PLAYBACK_START_OFFSET_WARN_MS,
)


def is_configured() -> bool:
    """Returns True if Spotify credentials are set in env."""
    return bool(
        os.getenv("SPOTIPY_CLIENT_ID") and os.getenv("SPOTIPY_CLIENT_SECRET")
    )


def classify_spotify_error(
    status_code: int | None, message: str, reason: str | None = None
) -> str:
    """Map HTTP status / reason / exception info to a short error category string.

    Returns one of:
        "auth_expired"      (401)
        "premium_required"  (403 + reason=PREMIUM_REQUIRED)
        "device_restricted" (403 + reason in {RESTRICTION_VIOLATED, UNKNOWN},
                             or "Restriction violated" in the message)
        "forbidden"         (403 otherwise — e.g. Development Mode limits)
        "not_found"         (404)
        "rate_limited"      (429) — only when retries already exhausted
        "server_error"      (5xx, retries exhausted)
        "network_error"     (connection/timeout, unexpected requests exceptions)
        "unknown"           (fallback — anything unclassified)
    """
    if status_code is None:
        # No HTTP response → network-level issue
        return "network_error"
    if status_code == 401:
        return "auth_expired"
    if status_code == 403:
        if reason == "PREMIUM_REQUIRED":
            return "premium_required"
        if reason in ("RESTRICTION_VIOLATED", "UNKNOWN") or "restriction violated" in message.lower():
            return "device_restricted"
        return "forbidden"
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


def spotify_error_details(exc) -> tuple[int | None, str | None, str]:
    """Extract ``(http_status, reason, msg)`` from a Spotify/requests exception.

    ``spotipy.exceptions.SpotifyException`` exposes ``http_status``, ``reason``
    and ``msg`` directly, and the RuntimeError raised by ``play_track_on_device()``
    re-attaches those attributes.  For any other (plain) exception, fall back to
    string parsing for an HTTP status code and a ``reason=`` token.
    """
    http_status = getattr(exc, "http_status", None)
    reason = getattr(exc, "reason", None)
    msg = getattr(exc, "msg", None)

    if http_status is None:
        # Plain exception — try to recover an HTTP status / reason from the text.
        import re as _re

        text = str(exc)
        if not msg:
            msg = text
        m = _re.search(r"\b(4\d\d|5\d\d)\b", text)
        if m:
            try:
                http_status = int(m.group(1))
            except ValueError:
                http_status = None
        if reason is None:
            rm = _re.search(r"reason[=:]\s*['\"]?([A-Z_]+)", text)
            if rm:
                reason = rm.group(1)

    return http_status, reason, msg or str(exc)


ERROR_USER_MESSAGES = {
    "auth_expired": "Your Spotify session expired — please reconnect.",
    "premium_required": "Spotify Premium is required for programmatic playback.",
    "device_restricted": "The selected Spotify device does not accept remote playback commands — pick another device.",
    "forbidden": "Spotify rejected this request (403). The app's Development Mode may not permit this endpoint.",
    "not_found": "Playlist no longer exists or you lost access to it.",
    "rate_limited": "Spotify rate-limited the request — try again in a minute.",
    "server_error": "Spotify API is having issues — try again shortly.",
    "network_error": "Spotify API is having issues — try again shortly.",
    "unknown": None,  # use raw error message
}


PLAYLIST_ITEMS_PAGE_LIMIT = 50  # February 2026 Web API change: playlist items are now max 50/page


def _market(default: str | None = None) -> str | None:
    """Return the cached user market (state.spotify_market), or *default*.

    ``market="from_token"`` is no longer a documented value after the February
    2026 Web API changes, so the user's country is resolved once at auth time
    (see ``init_spotify()``) and cached on the state module.  Never return the
    literal ``"from_token"``.
    """
    try:
        from playlist_arranger.ui import state as _st

        market = getattr(_st, "spotify_market", None)
    except Exception:
        market = None
    return market if market else default


def short_id(value, n=8):
    """Return *value* truncated to *n* chars for compact logs.

    When ``SPOTIFY_TRACE`` is enabled, return the full value so device/track
    ids are never truncated in trace output.
    """
    if SPOTIFY_TRACE:
        return value
    return value[:n] if value else value


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
                err_reason = None
                err_msg = resp.text[:200]
                try:
                    err_json = resp.json()
                    if isinstance(err_json, dict) and isinstance(err_json.get("error"), dict):
                        err_reason = err_json["error"].get("reason")
                        err_msg = str(err_json["error"].get("message", err_msg))
                except Exception:
                    pass
                err_type = classify_spotify_error(resp.status_code, err_msg, reason=err_reason)
                return None, {
                    "message": f"{resp.status_code} {err_msg}",
                    "type": err_type,
                    "status_code": resp.status_code,
                    "reason": err_reason,
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

    # Resolve and cache the user's market (country) and product once at auth
    # time.  ``market="from_token"`` is no longer a documented value after the
    # February 2026 Web API changes, so the rest of the app reads these cached
    # fields instead of passing that literal.
    user_id = user.get("id")
    country = user.get("country")
    product = user.get("product")
    try:
        from playlist_arranger.ui import state as _st

        _st.spotify_market = country or None
        _st.spotify_product = product or None
    except Exception:
        logger.exception("Failed to cache Spotify user market/product on state module")

    logger.info("Spotify user: id=%s, country=%s, product=%s", user_id, country, product)
    if product is None:
        # February 2026 Web API change: GET /me no longer returns `country`,
        # `product` or `email`.  Keep this explicit rather than silently
        # dropping the Premium warning — the playback path still detects a
        # non-Premium account via 403 PREMIUM_REQUIRED.
        logger.info(
            "Spotify GET /me did not return 'product' (removed in the February 2026 "
            "Web API changes) — Premium preflight skipped; a non-Premium account is "
            "reported as 403 PREMIUM_REQUIRED on the first playback command."
        )
    elif product != "premium":
        logger.warning(
            "Spotify product is '%s' (not 'premium') — PUT /me/player/play will "
            "always fail with 403 PREMIUM_REQUIRED and batch analysis cannot work.",
            product,
        )

    return SpotifyCallProxy(sp), user_id


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


def _is_track_playable(item: dict, market: str = "?") -> tuple[bool, str]:
    """Check whether a Spotify track item is playable.

    Parameters
    ----------
    item : dict
        Raw item dict from ``playlist_items()`` response.  Expected to have
        a ``"track"`` key containing the nested track object.
    market : str
        The ``market`` value that was passed to the API call (e.g. ``"US"``).
        Used only for diagnostic log messages.

    Returns
    -------
    ``(playable: bool, reason: str)`` where *reason* is a short
    human-readable explanation when *playable* is ``False``
    (or ``"ok"`` when the track is playable).
    """
    # Spotify's REST API nests the track object under the 'item' key
    # (not 'track').  Spotipy passes the response through unchanged.
    track = item.get("track") or item.get("item")
    if track is None:
        return False, "track object is None"
    # is_local lives on the item wrapper, NOT inside the "track" sub-object.
    # Spotify's API returns the local-file flag at the top level of each
    # playlist item rather than on the track metadata object.
    if item.get("is_local"):
        return False, "is_local=True"
    if track.get("type") != "track":
        return False, f"type='{track.get('type', '?')}' (not 'track')"
    if track.get("is_playable") is False:
        return False, f"is_playable=False for market={market}"
    restrictions = track.get("restrictions")
    if restrictions:
        reason_strs = [r.get("reason", "?") for r in restrictions if isinstance(r, dict)]
        return False, f"restrictions: {', '.join(reason_strs) if reason_strs else str(restrictions)}"
    # With ``market`` supplied the ``is_playable`` field is ALWAYS
    # populated (True or False).  If it is absent despite the market
    # parameter, treat the track as playable — skipping it would be
    # incorrect because the absence is a data-quality issue, not a
    # deliberate unavailability signal.
    if "is_playable" not in track:
        tid = track.get("id", "?")
        tname = track.get("name", "?")[:60]
        logger = logging.getLogger(__name__)
        logger.debug(
            "is_playable MISSING for track '%s' (id=%s) — treating as playable "
            "(market=%s was supplied)",
            tname, tid[:12] if tid else "NONE", market,
        )
    return True, "ok"


def get_playlist_tracks(sp, playlist_id):
    """Fetch all tracks from a playlist, filtering unplayable items.

    Each skipped track is logged at DEBUG level with its name, ID, and
    the exact reason for skipping (e.g. "track is None", "is_local=True",
    "is_playable=False for market=US", "restrictions: market").

    Uses the cached user market (see ``_market()``) instead of the removed
    ``market="from_token"`` value, and the February 2026 page-size limit of 50.
    """
    tracks = []
    limit = PLAYLIST_ITEMS_PAGE_LIMIT
    offset = 0
    skipped_unplayable = 0
    market = _market()

    while True:
        try:
            kwargs = {"limit": limit, "offset": offset}
            if market:
                kwargs["market"] = market
            result = sp.playlist_items(playlist_id, **kwargs)
        except Exception as exc:
            status, reason, msg = spotify_error_details(exc)
            code = getattr(exc, "code", None)
            raise RuntimeError(
                f"API error fetching tracks: http_status={status}, code={code}, "
                f"reason={reason!r}, msg={msg!r}"
            ) from exc

        items = result.get("items") or []

        for item in items:
            if not item:
                continue
            t = item.get("track") or item.get("item")  # API returns 'item' key, not 'track'
            tname = (t or {}).get("name", "?")[:60] if t else "?"
            tid = (t or {}).get("id", "") if t else ""

            if not t:
                skipped_unplayable += 1
                logger.debug(
                    "Skipping playlist item with no track object (playlist=%s)",
                    playlist_id[:8] if playlist_id else "?",
                )
                continue
            if item.get("is_local"):
                skipped_unplayable += 1
                logger.debug(
                    "Skipping track '%s' (id=%s): is_local=True",
                    tname, tid[:12] if tid else "NONE",
                )
                continue
            if t.get("type") != "track":
                skipped_unplayable += 1
                logger.debug(
                    "Skipping track '%s' (id=%s): type='%s' (not 'track')",
                    tname, tid[:12] if tid else "NONE", t.get("type", "?"),
                )
                continue
            if not tid:
                skipped_unplayable += 1
                logger.debug(
                    "Skipping track '%s': no 'id' field",
                    tname,
                )
                continue
            playable, reason = _is_track_playable(item, market=market or "?")
            if not playable:
                skipped_unplayable += 1
                logger.debug(
                    "Skipping track '%s' (id=%s): %s",
                    tname, tid[:12] if tid else "NONE", reason,
                )
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


def _player_request(sp, method, path, params=None, payload=None):
    """Issue a raw Spotify Web API request for a player endpoint.

    This exists specifically to surface the 204/202 that the spotipy wrappers
    hide (a 204 means "command accepted", not "playback started").

    Returns ``(status_code, parsed_json_or_None, raw_text_truncated_to_500)``.
    Never logs the token or any Authorization header.
    """
    import requests as _req

    url = f"https://api.spotify.com/v1/{path.lstrip('/')}"
    token = sp.auth_manager.get_access_token(as_dict=False)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    method_u = method.upper()
    if method_u == "GET":
        resp = _req.get(url, headers=headers, params=params, timeout=30)
    elif method_u == "PUT":
        resp = _req.put(url, headers=headers, params=params, json=payload, timeout=30)
    elif method_u == "POST":
        resp = _req.post(url, headers=headers, params=params, json=payload, timeout=30)
    else:
        raise ValueError(f"Unsupported method: {method}")

    text = resp.text or ""
    try:
        data = resp.json() if text else None
    except Exception:
        data = None

    if SPOTIFY_TRACE:
        body_display = "<empty 204>" if resp.status_code == 204 else (text or "")[:500]
        logger.info(
            "player_request: %s %s params=%r payload=%r -> %d %s",
            method_u, path, params, payload, resp.status_code, body_display,
        )

    return resp.status_code, data, (text or "")[:500]


def playback_snapshot(sp):
    """Return a compact snapshot of current playback, or None when nothing plays.

    GET /me/player returns 204 when nothing is playing.  On 200, the response is
    reduced to the fields the confirmation logic and trace logs actually need.
    """
    status, data, text = _player_request(sp, "GET", "me/player")

    if status == 204:
        if SPOTIFY_TRACE:
            logger.info("playback_snapshot: 204 — Spotify reports no active playback")
        return None

    if status != 200 or not isinstance(data, dict):
        logger.warning("playback_snapshot: unexpected status %d — %s", status, (text or "")[:300])
        return None

    device = data.get("device") or {}
    item = data.get("item") or {}
    snap = {
        "is_playing": bool(data.get("is_playing")),
        "progress_ms": data.get("progress_ms"),
        "timestamp": data.get("timestamp"),
        "currently_playing_type": data.get("currently_playing_type"),
        "track_id": item.get("id"),
        "track_name": item.get("name"),
        "context_uri": (data.get("context") or {}).get("uri"),
        "device_id": device.get("id"),
        "device_name": device.get("name"),
        "device_type": device.get("type"),
        "device_is_active": device.get("is_active"),
        "device_is_restricted": device.get("is_restricted"),
        "device_is_private_session": device.get("is_private_session"),
        "device_volume_percent": device.get("volume_percent"),
        "disallows": (data.get("actions") or {}).get("disallows") or {},
    }

    if SPOTIFY_TRACE:
        logger.info("playback_snapshot: %r", snap)

    return snap


def confirm_playback(sp, device_id=None, expected_track_id=None, timeout_s=None, interval_s=None):
    """Poll ``playback_snapshot()`` until playback is confirmed or times out.

    Confirmed means: snapshot is not None, ``is_playing`` truthy,
    ``progress_ms > 0``, and (when given) ``track_id`` and ``device_id`` match.

    Returns ``(confirmed, last_snapshot)``.
    """
    timeout_s = timeout_s if timeout_s is not None else PLAYBACK_CONFIRM_TIMEOUT_S
    interval_s = interval_s if interval_s is not None else PLAYBACK_CONFIRM_INTERVAL_S

    start = time.time()
    attempt = 0
    last_snapshot = None

    while True:
        attempt += 1
        snap = playback_snapshot(sp)
        last_snapshot = snap
        logger.debug("confirm_playback: attempt %d snapshot=%r", attempt, snap)

        if snap is not None:
            is_playing = bool(snap.get("is_playing"))
            progress_ok = (snap.get("progress_ms") or 0) > 0
            track_ok = (expected_track_id is None) or (snap.get("track_id") == expected_track_id)
            device_ok = (device_id is None) or (snap.get("device_id") == device_id)
            if is_playing and progress_ok and track_ok and device_ok:
                logger.info(
                    "confirm_playback: CONFIRMED after %.1fs — device='%s', track='%s'",
                    time.time() - start, snap.get("device_name"), snap.get("track_name"),
                )
                if (snap.get("progress_ms") or 0) > PLAYBACK_START_OFFSET_WARN_MS:
                    logger.warning(
                        "play_track_on_device: track %s confirmed at progress_ms=%d (expected ~0) — "
                        "coverage may fall below MIN_COVERAGE_PCT and the track may be silently skipped",
                        snap.get("track_id"), snap.get("progress_ms"),
                    )
                return True, snap

        if time.time() - start >= timeout_s:
            break
        time.sleep(interval_s)

    # Timeout — report which of the four conditions failed.
    if last_snapshot is not None:
        conds = [
            f"is_playing={bool(last_snapshot.get('is_playing'))}",
            f"progress_ms={last_snapshot.get('progress_ms')}",
        ]
        if device_id is not None:
            conds.append(
                "device matched" if last_snapshot.get("device_id") == device_id else "device mismatch"
            )
        if expected_track_id is not None:
            conds.append(
                "track matched" if last_snapshot.get("track_id") == expected_track_id else "track mismatch"
            )
        detail = ", ".join(conds)
    else:
        detail = "no snapshot (204 — nothing playing)"

    logger.warning(
        "confirm_playback: TIMEOUT after %.1fs — %s, last_snapshot=%r",
        timeout_s, detail, last_snapshot,
    )
    return False, last_snapshot


# Small device-list cache (TTL ~2 s) so check_device() and play_track_on_device()
# can share one devices() call when they run back to back.
_DEVICE_LIST_CACHE = {"sp": None, "ts": 0.0, "devices": None}


def _get_device_list(sp):
    """Return the device list for *sp*, caching the result briefly."""
    now = time.time()
    if (
        _DEVICE_LIST_CACHE.get("sp") is sp
        and _DEVICE_LIST_CACHE.get("devices") is not None
        and (now - _DEVICE_LIST_CACHE.get("ts", 0.0)) < 2.0
    ):
        return _DEVICE_LIST_CACHE["devices"]
    devices = sp.devices().get("devices", [])
    _DEVICE_LIST_CACHE["sp"] = sp
    _DEVICE_LIST_CACHE["ts"] = now
    _DEVICE_LIST_CACHE["devices"] = devices
    return devices


def play_track_on_device(sp, track_uri, device_id=None, confirm=True) -> tuple:
    """Start playback of *track_uri* on *device_id* via a confirmation-driven ladder.

    Never treats the absence of an exception as success — after each player
    command the ladder polls GET /me/player and only reports success once the
    expected track is actually playing on the expected device.

    Steps (each logged at INFO):
      1. Preflight the device list (raise if the target device is missing or
         ``is_restricted``).
      2. If the target device is present but not ``is_active``, transfer to it
         (``play=False``) so the subsequent play command is not silently dropped.
      3. ``PUT me/player/play`` + confirm. Return ``("play", snapshot)``.
      3b. If the target track is loaded but paused, issue a bodyless
          ``PUT me/player/play`` (resume) + confirm. Return ``("resume", snapshot)``.
      4. ``PUT me/player`` (``play=True``) + replay + confirm. Return
         ``("transfer+play", snapshot)``.
      5. ``POST me/player/queue`` + ``POST me/player/next`` + confirm with a
         longer timeout. Return ``("queue+next", snapshot)``.
      6. Give up: raise ``RuntimeError`` carrying the last snapshot, the paths
         attempted, and the ``disallows`` object.

    When *confirm* is False the confirmation polling is skipped (for callers with
    their own confirmation loop).  Returns ``(path, final_snapshot)``.
    """

    def _track_id_from_uri(uri):
        if not uri:
            return None
        return uri.split(":", 2)[2] if uri.startswith("spotify:track:") else uri

    def _raise_playback_error(status, data, text):
        reason = None
        msg = text
        if isinstance(data, dict) and isinstance(data.get("error"), dict):
            reason = data["error"].get("reason")
            msg = str(data["error"].get("message", msg))
        category = classify_spotify_error(status, msg, reason=reason)
        err = RuntimeError(
            f"Playback command failed: category={category}, reason={reason!r}, "
            f"http_status={status}, msg={msg!r}"
        )
        err.http_status = status
        err.reason = reason
        err.msg = msg
        err.category = category
        raise err

    def _issue_play():
        status, data, text = _player_request(
            sp,
            "PUT",
            "me/player/play",
            params={"device_id": device_id} if device_id else None,
            payload={"uris": [track_uri]},
        )
        if status not in (204, 202):
            _raise_playback_error(status, data, text)

    expected_track_id = _track_id_from_uri(track_uri)

    def _confirm(timeout_s=None):
        if not confirm:
            return True, None
        return confirm_playback(sp, device_id, expected_track_id, timeout_s=timeout_s)

    # Step 1 — preflight device list.
    devices = _get_device_list(sp)
    if SPOTIFY_TRACE:
        for d in devices:
            logger.info(
                "play_track_on_device: device id=%s name=%r type=%s is_active=%s "
                "is_restricted=%s volume_percent=%s",
                d.get("id"), d.get("name"), d.get("type"),
                d.get("is_active"), d.get("is_restricted"), d.get("volume_percent"),
            )

    target = None
    for d in devices:
        if d.get("id") == device_id:
            target = d
            break
    if device_id is not None and target is None:
        raise RuntimeError(f"Target Spotify device '{device_id}' is not in the device list")
    if target is not None and target.get("is_restricted"):
        raise RuntimeError(
            f"Target Spotify device '{device_id}' (name={target.get('name')!r}) is "
            "restricted and accepts no Web API commands"
        )

    last_snapshot = None
    paths_attempted = []

    # Step 2 — activate if idle (most common fix for a silently dropped play).
    if target is not None and not target.get("is_active"):
        logger.info(
            "play_track_on_device: device '%s' is not active — transferring (play=False)",
            device_id,
        )
        _player_request(sp, "PUT", "me/player", payload={"device_ids": [device_id], "play": False})
        time.sleep(1.0)

    # Step 3 — play + confirm.
    paths_attempted.append("play")
    _issue_play()
    logger.info("play_track_on_device: play command accepted (204/202)")
    ok, last_snapshot = _confirm()
    if ok:
        logger.info("play_track_on_device: path='play' confirmed")
        return "play", last_snapshot

    # Step 3b — resume: the target track is already loaded on the target device
    # but paused (is_playing=False, resuming not disallowed).  A bodyless resume
    # is the correct action, not a transfer.
    snap = last_snapshot
    disallows = (snap or {}).get("disallows") or {}
    if (
        snap is not None
        and snap.get("track_id") == expected_track_id
        and (device_id is None or snap.get("device_id") == device_id)
        and not snap.get("is_playing")
        and not disallows.get("resuming")
    ):
        paths_attempted.append("resume")
        logger.info("play_track_on_device: track already loaded but paused — issuing bodyless resume")
        _player_request(
            sp, "PUT", "me/player/play",
            params={"device_id": device_id} if device_id else None,
        )
        ok, last_snapshot = _confirm()
        if ok:
            logger.info("play_track_on_device: path='resume' confirmed")
            return "resume", last_snapshot

    # Step 4 — transfer (force_play=True) + replay + confirm.
    paths_attempted.append("transfer+play")
    logger.info("play_track_on_device: play not confirmed — transferring (play=True) and retrying")
    _player_request(sp, "PUT", "me/player", payload={"device_ids": [device_id], "play": True})
    time.sleep(1.0)
    _issue_play()
    ok, last_snapshot = _confirm()
    if ok:
        logger.info("play_track_on_device: path='transfer+play' confirmed")
        return "transfer+play", last_snapshot

    # Step 5 — queue + next + confirm (longer timeout; this path is slower).
    paths_attempted.append("queue+next")
    logger.info("play_track_on_device: still not confirmed — trying queue+next")
    queue_params = {"uri": track_uri}
    if device_id:
        queue_params["device_id"] = device_id
    _player_request(sp, "POST", "me/player/queue", params=queue_params)
    _player_request(sp, "POST", "me/player/next", params={"device_id": device_id} if device_id else None)
    ok, last_snapshot = _confirm(timeout_s=PLAYBACK_CONFIRM_TIMEOUT_S * 2)
    if ok:
        logger.info("play_track_on_device: path='queue+next' confirmed")
        return "queue+next", last_snapshot

    # Step 6 — give up.
    disallows = (last_snapshot or {}).get("disallows") or {}
    logger.error(
        "play_track_on_device: giving up — paths_attempted=%r, disallows=%r, last_snapshot=%r",
        paths_attempted, disallows, last_snapshot,
    )
    raise RuntimeError(
        f"Playback never confirmed on device '{device_id}' for track '{track_uri}'. "
        f"paths_attempted={paths_attempted}, disallows={disallows}, "
        f"last_snapshot={last_snapshot}"
    )


def check_device(sp, device_id) -> tuple[bool, str]:
    """Preflight a Spotify device for Web API remote control.

    Returns ``(True, "ok")`` when *device_id* is present in the device list and
    not ``is_restricted`` (a restricted device cannot be controlled remotely and
    is a common source of 403 Restriction violated).  Returns ``(False, reason)``
    otherwise.  Reuses the cached device list from ``_get_device_list()``.
    """
    try:
        devices = _get_device_list(sp) if sp else []
    except Exception as exc:
        _, _, msg = spotify_error_details(exc)
        return False, f"Could not list Spotify devices: {msg}"

    for d in devices:
        if d.get("id") == device_id:
            if d.get("is_restricted"):
                return (
                    False,
                    "The selected Spotify device is restricted and cannot be "
                    "controlled remotely — pick another device.",
                )
            return True, "ok"

    return False, "The selected Spotify device is no longer available — pick another device."


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