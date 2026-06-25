"""Cache store: atomic JSON writes, descriptions, results, backups."""

import pathlib
import json
import datetime

from playlist_arranger.config import CACHE_DIR_DEFAULT, ANCHORS_DIR_DEFAULT


def atomic_write_json(file_path: pathlib.Path, data: object):
    """Write JSON atomically: temp file first, then rename."""
    tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp_path.replace(file_path)


def _get_anchors_dir() -> pathlib.Path:
    """Get anchors directory (alias for backward compat)."""
    return ANCHORS_DIR_DEFAULT


def load_descriptions(playlist_id: str) -> list | None:
    """Load previously saved track descriptions from anchors/descriptions_<pl_id>.json."""
    desc_file = _get_anchors_dir() / f"descriptions_{playlist_id}.json"
    if desc_file.exists():
        try:
            data = json.loads(desc_file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "tracks" in data:
                return data["tracks"]
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return None


def save_descriptions(playlist_id: str, playlist_name: str, tracks: list, model: str):
    """Save track descriptions to anchors/descriptions_<pl_id>.json."""
    desc_file = _get_anchors_dir() / f"descriptions_{playlist_id}.json"
    data = {
        "playlist_id": playlist_id,
        "playlist_name": playlist_name,
        "model": model,
        "tracks": tracks,
    }
    atomic_write_json(desc_file, data)


def backup_exists(playlist_id: str) -> bool:
    """Check if a backup file exists for this playlist."""
    bk_file = _get_anchors_dir() / f"backup_{playlist_id}.json"
    return bk_file.exists()


def load_backup(playlist_id: str) -> dict | None:
    """Load backup data for a playlist."""
    bk_file = _get_anchors_dir() / f"backup_{playlist_id}.json"
    if bk_file.exists():
        try:
            return json.loads(bk_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def save_result(playlist_id, playlist_name, ordered_descs, cost):
    """Save sorting result to anchors/result_<pl_id>.json."""
    result_file = _get_anchors_dir() / f"result_{playlist_id}.json"
    result_data = {
        "playlist_id": playlist_id,
        "playlist_name": playlist_name,
        "saved_at": datetime.datetime.now().isoformat(),
        "cost": float(cost),
        "tracks": [
            {
                "track_id": d["track_id"],
                "name": d.get("name", ""),
                "artist": d.get("artist", ""),
                "bpm": d.get("bpm", 0),
                "camelot": d.get("camelot", ""),
            }
            for d in ordered_descs
        ],
    }
    atomic_write_json(result_file, result_data)