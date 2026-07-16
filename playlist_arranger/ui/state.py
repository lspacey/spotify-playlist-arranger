"""Global reactive state for the Playlist Arranger NiceGUI app."""

import logging
import pathlib

logger = logging.getLogger(__name__)

# ─── Playlist state ──────────────────────────────────────────────────────────
current_playlist_id: str | None = None
current_playlist_name: str | None = None
current_playlist_source: str | None = None  # "spotify" or "local"
current_tracks: list = []
current_descs: list = []
current_anchor_plan: list = []
current_sorted_descs: list = []

# ─── Spotify state ──────────────────────────────────────────────────────────
sp = None  # spotipy.Spotify instance
spotify_device_id: str | None = None
spotify_user_id: str | None = None

# ─── Audio capture state ────────────────────────────────────────────────────
audio_capture_active: bool = False
pa = None
stream = None
audio_capture_device_index: int | None = None  # selected audio input device

# ─── Settings ───────────────────────────────────────────────────────────────
settings = None  # config.Settings instance, loaded lazily


def get_settings():
    """Lazy-load settings."""
    global settings
    if settings is None:
        from playlist_arranger.config import load_settings

        settings = load_settings()
    return settings


analysis_queue: list = []


def clear_playlist():
    """Reset current playlist state."""
    global current_playlist_id, current_playlist_name, current_playlist_source
    global current_tracks, current_descs, current_anchor_plan, current_sorted_descs
    current_playlist_id = None
    current_playlist_name = None
    current_playlist_source = None
    current_tracks = []
    current_descs = []
    current_anchor_plan = []
    current_sorted_descs = []


# ─── Analysis Queue persistence ─────────────────────────────────────────────
_QUEUE_FILE: str | None = None


def _get_queue_path() -> str:
    """Return path to the analysis queue JSON file."""
    global _QUEUE_FILE
    if _QUEUE_FILE is None:
        from playlist_arranger.config import CACHE_DIR_DEFAULT

        _QUEUE_FILE = str(CACHE_DIR_DEFAULT / "analysis_queue.json")
    return _QUEUE_FILE


def save_analysis_queue():
    """Persist the analysis queue to disk atomically."""
    from playlist_arranger.cache.store import atomic_write_json

    atomic_write_json(pathlib.Path(_get_queue_path()), analysis_queue)


def load_analysis_queue():
    """Load the analysis queue from disk (called on startup)."""
    global analysis_queue
    import pathlib as _pl

    qf = _pl.Path(_get_queue_path())
    if qf.exists():
        try:
            import json as _json

            data = _json.loads(qf.read_text(encoding="utf-8"))
            if isinstance(data, list):
                analysis_queue = data
        except Exception:
            logger.exception("Failed to load analysis queue from disk")


def has_playlist() -> bool:
    return current_playlist_id is not None and len(current_tracks) > 0


def has_descriptions() -> bool:
    return len(current_descs) > 0


def has_anchor_plan() -> bool:
    return len(current_anchor_plan) > 0


def get_track_needs_analysis(track_id: str, real_duration_ms=None) -> str | None:
    """
    Return a reason string if the track needs (re)analysis, or None if it is fine.
    Uses DB directly.
    """
    from playlist_arranger.database import db as _db
    from playlist_arranger.config import load_settings, DURATION_TOLERANCE

    entry = _db.get_track(track_id)
    if not entry:
        return "Missing in db"
    emb_file = entry.get("embedding_file")
    if not emb_file:
        return "Missing file"
    s = load_settings()
    emb_path = s.embeds_dir / f"{track_id}.npy"
    if not emb_path.exists():
        return "Missing file"
    if real_duration_ms is not None:
        stored_ms = entry.get("duration_ms", 0)
        if stored_ms > 0:
            diff = abs(stored_ms - real_duration_ms) / real_duration_ms
            if diff > DURATION_TOLERANCE:
                return "Wrong time"
    return None