"""Global reactive state for the Playlist Arranger NiceGUI app."""

import logging
import pathlib
import threading

logger = logging.getLogger(__name__)

# ─── Playlist state ──────────────────────────────────────────────────────────
current_playlist_id: str | None = None
current_playlist_name: str | None = None
current_playlist_source: str | None = None  # "spotify" or "local"
current_tracks: list = []
current_descs: list = []
current_anchor_plan: list = []
current_sorted_descs: list = []

# ─── Anchors page state ───────────────────────────────────────────────────────
anchors_selected_playlist_id: str | None = None

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
analysis_queue_lock = threading.Lock()
analysis_current_track_id: str | None = None  # set ONLY when worker starts after coverage passes


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


# ─── Expected feature keys (from audio/features.py _extract_features_full) ─────
_EXPECTED_FEATURE_KEYS = frozenset({
    "bpm", "beat_reg", "centroid_hz", "rolloff_hz", "bandwidth_hz", "zcr",
    "onset_str", "bass", "mid", "high", "chroma_key", "chroma_idx",
    "chroma_vals", "mode", "camelot", "mfcc13", "flatness", "harm_ratio",
    "dynamic_range", "chroma_cens", "tempo_complexity", "mfcc20",
    "rms_db", "rms_norm",
})


def get_track_status(track: dict) -> str:
    """Unified per-track status check used by playlist tables, queue table, and TrackTable.

    Evaluates in priority order (first failure wins):
      1. Not in DB        → "✗ Not in DB"
      2. Corrupt DB entry → "✗ Corrupt DB entry"
      3. No embedding ref → "✗ No embedding"
      4. Embedding missing → "✗ Embedding file missing"
      5. No features       → "✗ Incomplete features"
      6. Missing feature keys → "✗ Incomplete features"
      7. Duration mismatch → "✗ Duration mismatch"
      All pass → "✓ OK"
    """
    from playlist_arranger.database import db as _db
    from playlist_arranger.config import load_settings, DURATION_TOLERANCE
    import pathlib as _pl

    tid = track.get("id", "")
    if not tid:
        return "✗ Missing ID"

    entry = _db.get_track(tid)

    # 1. Not in DB
    if entry is None:
        return "✗ Not in DB"

    # Edge case: entry is not a dict or is empty
    if not isinstance(entry, dict) or not entry:
        return "✗ Corrupt DB entry"

    # 3. No embedding reference
    emb_file = entry.get("embedding_file")
    if not emb_file:
        return "✗ No embedding"

    # 4. Embedding file path set but file missing/corrupt
    s = load_settings()
    emb_path = _pl.Path(s.embeds_dir / f"{tid}.npy")
    if not emb_path.exists():
        return "✗ Embedding file missing"

    # 5. No features dict
    features = entry.get("features")
    if not isinstance(features, dict) or not features:
        return "✗ Incomplete features"

    # 6. Check for complete feature key set
    existing_keys = set(features.keys())
    if not _EXPECTED_FEATURE_KEYS.issubset(existing_keys):
        return "✗ Incomplete features"

    # 7. Duration mismatch
    real_dur = track.get("duration_ms", 0)
    stored_dur = entry.get("duration_ms", 0)
    if real_dur > 0 and stored_dur > 0:
        diff = abs(stored_dur - real_dur) / real_dur
        if diff > DURATION_TOLERANCE:
            return "✗ Duration mismatch"

    return "✓ OK"


# Backward-compatible alias used by components/track_table.py and main.py
def get_track_needs_analysis(track_id: str, real_duration_ms=None) -> str | None:
    """Legacy wrapper — delegates to get_track_status() with minimal track dict."""
    track = {"id": track_id}
    if real_duration_ms is not None:
        track["duration_ms"] = real_duration_ms
    status = get_track_status(track)
    if status == "✓ OK":
        return None
    # Map new status strings to legacy reasons
    return status.replace("✗ ", "")
