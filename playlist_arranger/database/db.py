"""SQLite backend replacing tracks_db.json."""

import sqlite3
import threading
import json

from playlist_arranger.config import Settings

_db_lock = threading.Lock()

# Module-level cached settings reference
_settings: Settings | None = None
_conn_cache: dict = {}


def _get_settings() -> Settings:
    global _settings
    if _settings is None:
        from playlist_arranger.config import load_settings

        _settings = load_settings()
    return _settings


def _get_conn() -> sqlite3.Connection:
    """Get or create a database connection with WAL mode."""
    s = _get_settings()
    db_path = str(s.db_path)
    thread_id = threading.get_ident()
    cache_key = (db_path, thread_id)

    if cache_key in _conn_cache:
        return _conn_cache[cache_key]

    # Ensure directory exists
    import pathlib

    pathlib.Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tracks (
            track_id TEXT PRIMARY KEY,
            data TEXT NOT NULL
        )
    """
    )
    conn.commit()
    _conn_cache[cache_key] = conn
    return conn


def reload_db() -> None:
    """Close all cached connections (used after settings path change)."""
    global _conn_cache
    for conn in _conn_cache.values():
        try:
            conn.close()
        except Exception:
            pass
    _conn_cache = {}


def get_track(track_id: str) -> dict | None:
    """Single lookup by primary key. Returns dict or None."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT data FROM tracks WHERE track_id = ?", (track_id,)
    ).fetchone()
    if row:
        return json.loads(row[0])
    return None


def save_track(track_id: str, entry: dict) -> None:
    """Atomic upsert for a single track."""
    with _db_lock:
        conn = _get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO tracks (track_id, data) VALUES (?, ?)",
            (track_id, json.dumps(entry, ensure_ascii=False)),
        )
        conn.commit()


def load_all() -> dict:
    """Returns {id: dict} for full DB scan (used by sorting)."""
    conn = _get_conn()
    rows = conn.execute("SELECT track_id, data FROM tracks").fetchall()
    return {track_id: json.loads(data) for track_id, data in rows}


def save_many(records: dict) -> None:
    """Bulk upsert via executemany."""
    with _db_lock:
        conn = _get_conn()
        items = [
            (tid, json.dumps(entry, ensure_ascii=False))
            for tid, entry in records.items()
        ]
        conn.executemany(
            "INSERT OR REPLACE INTO tracks (track_id, data) VALUES (?, ?)", items
        )
        conn.commit()


def delete_track(track_id: str) -> None:
    """Delete a single track by ID."""
    with _db_lock:
        conn = _get_conn()
        conn.execute("DELETE FROM tracks WHERE track_id = ?", (track_id,))
        conn.commit()


def vacuum() -> None:
    """Execute VACUUM."""
    conn = _get_conn()
    conn.execute("VACUUM")


def analyze() -> None:
    """Execute ANALYZE."""
    conn = _get_conn()
    conn.execute("ANALYZE")


def track_count() -> int:
    """Return total number of tracks in DB."""
    conn = _get_conn()
    row = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()
    return row[0] if row else 0