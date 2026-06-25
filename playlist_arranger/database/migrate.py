"""Migrate from legacy tracks_db.json to SQLite."""

import pathlib
import json

from playlist_arranger.database import db as _db

# Fields to strip from legacy entries during migration (playlist-specific metadata)
_STRIP_FIELDS = {"playlist_name", "playlist_uri", "playlist", "playlist_id"}


def migrate_from_json(json_path: pathlib.Path) -> dict:
    """
    Read tracks_db.json, insert all records into SQLite.
    Strips playlist-specific fields (playlist_name, playlist_uri, etc.)
    Returns stats dict: {'imported': int, 'skipped': int, 'errors': int}
    """
    if not json_path.exists():
        print(f"JSON file not found: {json_path}")
        return {"imported": 0, "skipped": 0, "errors": 1}

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Error reading JSON: {e}")
        return {"imported": 0, "skipped": 0, "errors": 1}

    if not isinstance(data, dict):
        print("JSON data is not a dict — expected {track_id: {...}}")
        return {"imported": 0, "skipped": 0, "errors": 1}

    imported = 0
    skipped = 0
    errors = 0

    for track_id, entry in data.items():
        if not isinstance(entry, dict):
            skipped += 1
            continue
        # Strip playlist-specific legacy fields
        for field in _STRIP_FIELDS:
            entry.pop(field, None)
        # Ensure track_id field exists
        if "track_id" not in entry:
            entry["track_id"] = track_id
        # Also rename legacy spotify_id -> track_id if present
        if "spotify_id" in entry and "track_id" not in entry:
            entry["track_id"] = entry.pop("spotify_id")
        try:
            _db.save_track(track_id, entry)
            imported += 1
        except Exception as e:
            print(f"Error importing {track_id}: {e}")
            errors += 1

    print(
        f"Migration complete: {imported} imported, {skipped} skipped, {errors} errors"
    )
    return {"imported": imported, "skipped": skipped, "errors": errors}
