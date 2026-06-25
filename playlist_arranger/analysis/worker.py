"""Save track worker — extracts features, computes MERT embedding, saves to DB."""

import datetime
import numpy as np
import pathlib

from playlist_arranger import config
from playlist_arranger.audio.features import (
    _trim_silence,
    _extract_features,
    _extract_features_full,
)
from playlist_arranger.audio.mert import _mert_embedding
from playlist_arranger.database import db as _db
from playlist_arranger.audio.capture import actual_sr


def save_track_worker(
    track_info,
    playlist_name,
    playlist_uri,
    y_full,
    y_start_snap=None,
    status_cb=None,
):
    """
    Extract features + MERT embedding, save to DB.
    track_info is a dict with 'id', 'name', 'artist', 'album', 'duration_ms'.
    """
    tid = track_info["id"]
    if status_cb:
        status_cb(f"Extracting features: {track_info['name'][:30]}...")

    y_main = _trim_silence(y_full, sr=actual_sr, top_db=40)
    full_feats = _extract_features_full(y_main, sr=actual_sr)
    y_start = (
        y_start_snap
        if (y_start_snap is not None and len(y_start_snap) >= actual_sr)
        else y_full[: actual_sr * config.SEG_SECONDS]
    )
    y_end = y_full[-actual_sr * config.SEG_SECONDS :]
    start_feats = _extract_features(y_start, sr=actual_sr)
    end_feats = _extract_features(y_end, sr=actual_sr)

    if status_cb:
        status_cb(f"Computing MERT embedding: {track_info['name'][:30]}...")
    emb = _mert_embedding(y_main, sr=actual_sr)
    emb_file = None
    if emb is not None:
        emb_path = config.EMBEDS_DIR_DEFAULT / f"{tid}.npy"
        np.save(str(emb_path), np.array(emb, dtype=np.float32))
        emb_file = str(emb_path.relative_to(config.BASE_DIR))

    entry = {
        "track_id": tid,
        "name": track_info["name"],
        "artist": track_info["artist"],
        "album": track_info["album"],
        "duration_ms": track_info["duration_ms"],
        "saved_at": datetime.datetime.now().isoformat(),
        "features": full_feats,
        "start_seg": start_feats,
        "end_seg": end_feats,
        "embedding_file": emb_file,
        "embedding_dim": 768 if emb else None,
    }
    _db.save_track(tid, entry)

    if status_cb:
        emb_str = "+ MERT" if emb else "(no MERT)"
        status_cb(f"Saved: {track_info['name'][:30]} {emb_str}")