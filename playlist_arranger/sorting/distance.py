"""Track distance computation for smart sorting."""

import numpy as np

from playlist_arranger.config import (
    WEIGHTS,
    ARTIST_PENALTY,
    ALBUM_PENALTY,
    CAMELOT_TO_IDX,
    HOME_DIR,
)


def _norm_text(s: str) -> str:
    return " ".join(str(s or "").strip().lower().split())


def _artist_tokens(s: str) -> set:
    txt = str(s or "").lower().replace("&", ",").replace(";", ",")
    return {p.strip() for p in txt.split(",") if p.strip()}


def _cos_dist(a: "np.ndarray", b: "np.ndarray") -> float:
    n = np.linalg.norm(a) * np.linalg.norm(b)
    if n < 1e-9:
        return 1.0
    return float(1.0 - np.dot(a, b) / n)


def _camelot_distance(cam_a: str, cam_b: str) -> float:
    if cam_a == "?" or cam_b == "?":
        return 0.5
    ia = CAMELOT_TO_IDX.get(cam_a, -1)
    ib = CAMELOT_TO_IDX.get(cam_b, -1)
    if ia < 0 or ib < 0:
        return 0.5
    ring_a, num_a = ia // 12, ia % 12
    ring_b, num_b = ib // 12, ib % 12
    circ = min(abs(num_a - num_b), 12 - abs(num_a - num_b))
    ring_penalty = 0 if ring_a == ring_b else 1
    return min(circ + ring_penalty, 6) / 6.0


def _track_distance(ta: dict, tb: dict, emb_a, emb_b) -> float:
    """Compute distance between two tracks using all available features."""
    fa = ta.get("features") or {}
    fb = tb.get("features") or {}
    end_a = ta.get("end_seg", fa)
    start_b = tb.get("start_seg", fb)

    if emb_a is not None and emb_b is not None:
        d_mood = min(_cos_dist(emb_a, emb_b) / 2.0, 1.0)
    else:
        ca = fa.get("chroma_cens") or fa.get("chroma_vals")
        cb = fb.get("chroma_cens") or fb.get("chroma_vals")
        if ca and cb:
            n = min(len(ca), len(cb))
            d_mood = min(
                _cos_dist(
                    np.array(ca[:n], dtype=np.float32),
                    np.array(cb[:n], dtype=np.float32),
                )
                / 2.0,
                1.0,
            )
        else:
            d_mood = 0.5

    bpm_end_a = end_a.get("bpm", fa.get("bpm", 120))
    bpm_start_b = start_b.get("bpm", fb.get("bpm", 120))
    d_bpm = min(abs(bpm_end_a - bpm_start_b) / 200.0, 1.0)

    mfcc_ea = (
        end_a.get("mfcc20")
        or end_a.get("mfcc13")
        or fa.get("mfcc20")
        or fa.get("mfcc13")
    )
    mfcc_sb = (
        start_b.get("mfcc20")
        or start_b.get("mfcc13")
        or fb.get("mfcc20")
        or fb.get("mfcc13")
    )
    if mfcc_ea and mfcc_sb:
        va = np.array(mfcc_ea, dtype=np.float32)
        vb = np.array(mfcc_sb, dtype=np.float32)
        n = min(len(va), len(vb))
        d_transition = min(_cos_dist(va[:n], vb[:n]) / 2.0, 1.0)
    else:
        d_transition = 0.5

    cam_a = end_a.get("camelot", fa.get("camelot", "?"))
    cam_b = start_b.get("camelot", fb.get("camelot", "?"))
    d_key = _camelot_distance(cam_a, cam_b)

    rms_ea = end_a.get("rms_db", fa.get("rms_db", -20))
    rms_sb = start_b.get("rms_db", fb.get("rms_db", -20))
    d_energy = min(abs(rms_ea - rms_sb) / 60.0, 1.0)

    base = (
        WEIGHTS["mood"] * d_mood
        + WEIGHTS["bpm"] * d_bpm
        + WEIGHTS["transition"] * d_transition
        + WEIGHTS["key"] * d_key
        + WEIGHTS["energy"] * d_energy
    )

    artist_a = _artist_tokens(ta.get("artist", ""))
    artist_b = _artist_tokens(tb.get("artist", ""))
    album_a = _norm_text(ta.get("album", ""))
    album_b = _norm_text(tb.get("album", ""))
    if album_a and album_b and album_a == album_b:
        base += ALBUM_PENALTY
    elif artist_a and artist_b and (artist_a & artist_b):
        base += ARTIST_PENALTY

    return min(base, 1.0 + ALBUM_PENALTY)


def _load_embedding(tid: str, db: dict):
    """Load MERT embedding from disk."""
    entry = db.get(tid)
    if not entry:
        return None
    ef = entry.get("embedding_file")
    if not ef:
        return None
    p = HOME_DIR / ef
    return np.load(str(p)).astype(np.float32) if p.exists() else None


_SORTING_CACHE = (
    {}
)  # {playlist_id: {"D": ndarray, "track_ids": list, ...}}


def _build_distance_matrix(
    track_indices: list, all_tracks: list, embeddings: list
) -> "np.ndarray":
    """Build pairwise distance matrix for a list of tracks."""
    n = len(track_indices)
    D = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        for j in range(n):
            if i != j:
                # Bug fix: use track_indices to index into all_tracks
                D[i, j] = _track_distance(
                    all_tracks[track_indices[i]],
                    all_tracks[track_indices[j]],
                    embeddings[track_indices[i]],
                    embeddings[track_indices[j]],
                )
    return D