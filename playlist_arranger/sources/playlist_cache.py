"""Cache file read/write and stale-detection for playlist track data.

Extracted from ``playlist_source.py`` (2026-07-29 refactor — pattern #18).
Uses the same ``configure()`` dependency-injection pattern as
``analysis/batch_analyzer.py`` and ``ui/playlist_highlight.py``.
"""

import logging
import json
import pathlib
import glob as _glob

from playlist_arranger.config import CACHE_DIR_DEFAULT

logger = logging.getLogger(__name__)

# ── Injected dependencies (wired by playlist_source.py at module init) ────────
_state = None                     # playlist_arranger.ui.state module ref
_get_playlist_tracks_fn = None    # spotify_source.get_playlist_tracks


def configure(state_module, get_playlist_tracks_fn):
    """Wire runtime dependencies from ``playlist_source.py`` at init time.

    *state_module* is the ``playlist_arranger.ui.state`` module reference
    (not its ``.sp`` attribute — that changes at runtime and would be
    captured as a stale snapshot).
    """
    global _state, _get_playlist_tracks_fn
    _state = state_module
    _get_playlist_tracks_fn = get_playlist_tracks_fn


def load_cached_playlist_tracks(playlist_id: str) -> list:
    """Return cached track list for *playlist_id*, or fetch from Spotify.

    Reads/writes ``cache/<playlist_id>-<snapshot_id>.tracks.json``.
    Detects and auto-deletes stale (0-track / missing-uri / corrupted)
    cache files, then falls through to the Spotify API.
    """
    logger.info("Expanding playlist %s", playlist_id[:8])
    try:
        pl_data = _state.sp.playlist(playlist_id, fields="snapshot_id,name")
        snapshot_id = pl_data.get("snapshot_id", "")
        pl_name = pl_data.get("name", "?")
    except Exception:
        snapshot_id = ""
        pl_name = "?"
    logger.info("Playlist %s: %s (snapshot=%s)", playlist_id[:8], pl_name,
                snapshot_id[:8] if snapshot_id else "?")

    if snapshot_id:
        cache_file = CACHE_DIR_DEFAULT / f"{playlist_id}-{snapshot_id}.tracks.json"
        if cache_file.exists():
            try:
                tracks = json.loads(cache_file.read_text(encoding="utf-8"))
                count = len(tracks)
                missing_uri = sum(
                    1 for t in tracks
                    if not (t.get("uri") or "").startswith("spotify:track:")
                )
                if missing_uri > 0:
                    logger.warning(
                        "STALE CACHE SCHEMA: %s — %d/%d tracks missing valid 'uri' "
                        "field (cache predates uri field being added to "
                        "get_playlist_tracks()). Deleting stale cache to force re-fetch.",
                        cache_file, missing_uri, count,
                    )
                    try:
                        cache_file.unlink()
                    except Exception:
                        pass
                    # Fall through to re-fetch from Spotify below
                elif count == 0:
                    logger.warning(
                        "CACHE EMPTY: %s contains 0 tracks — playlist %s may have been "
                        "cached during a transient error. Deleting stale cache to force "
                        "re-fetch.", cache_file, playlist_id[:8]
                    )
                    try:
                        cache_file.unlink()
                    except Exception:
                        pass
                    # Fall through to re-fetch from Spotify below
                else:
                    logger.info("Loaded %d tracks from cache: %s (%s)", count, cache_file, pl_name)
                    return tracks
            except Exception:
                logger.warning("Corrupted cache file %s, re-fetching", cache_file)
                try:
                    cache_file.unlink()
                except Exception:
                    pass
                # Fall through to re-fetch from Spotify below

    logger.info("Fetching tracks from Spotify API for playlist %s (%s)", playlist_id[:8], pl_name)
    tracks = _get_playlist_tracks_fn(_state.sp, playlist_id)
    track_count = len(tracks)
    logger.info("Spotify API returned %d playable tracks for playlist %s (%s)",
                track_count, playlist_id[:8], pl_name)

    if not tracks:
        logger.warning("EMPTY RESULT: 0 playable tracks for playlist %s (%s)",
                       playlist_id[:8], pl_name)

    # Remove any stale cache files (previous snapshot) before writing new one
    pattern = str(CACHE_DIR_DEFAULT / f"{playlist_id}-*.tracks.json")
    for old_file in _glob.glob(pattern):
        try:
            pathlib.Path(old_file).unlink()
        except Exception:
            pass
    if snapshot_id:
        cache_file = CACHE_DIR_DEFAULT / f"{playlist_id}-{snapshot_id}.tracks.json"
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(tracks, ensure_ascii=False), encoding="utf-8")
            logger.info("Cached %d tracks to %s", len(tracks), cache_file)
        except Exception as e:
            logger.warning("Failed to write cache file: %s", e)
    return tracks