"""Track distance computation for smart sorting."""

import numpy as np

import playlist_arranger.config as _cfg

# Weights dict is mutable — updates from settings propagate through the reference.
WEIGHTS = _cfg.WEIGHTS
CAMELOT_TO_IDX = _cfg.CAMELOT_TO_IDX
HOME_DIR = _cfg.HOME_DIR

# ARTIST_PENALTY and ALBUM_PENALTY are read via _cfg in _track_distance()
# to always pick up the latest value after settings save updates.


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


def _robust_range(values, lo_pct=5, hi_pct=95, floor=1e-6) -> float:
    """Compute the p5-to-p95 range of a list of floats, clamped to `floor`.

    Robust to outliers — extreme values beyond the 5th/95th percentile do not
    inflate the normalization scale, which keeps distance values meaningful
    when a playlist contains just one extreme track.
    """
    if len(values) < 2:
        return floor
    lo, hi = np.percentile(values, [lo_pct, hi_pct])
    return max(hi - lo, floor)


def _calibrate_texture_scales(all_tracks: list) -> tuple[float, float]:
    """Compute per-playlist normalization scales for dynamic_range and onset_str.

    Returns (dyn_range_scale, onset_range_scale).  Each scale is the robust
    p5–p95 range across all tracks in the playlist.  Falls back to the
    empirically-chosen defaults (20.0, 2.0) when too few data points exist.

    Reads the same feature dicts that ``_track_distance`` uses:
    ``features``, ``start_seg``, ``end_seg``.
    """
    dyn_vals = []
    onset_vals = []

    for t in all_tracks:
        for section in ("features", "start_seg", "end_seg"):
            sec = t.get(section) or {}
            if "dynamic_range" in sec:
                dyn_vals.append(float(sec["dynamic_range"]))
            if "onset_str" in sec:
                onset_vals.append(float(sec["onset_str"]))

    dyn_scale = _robust_range(dyn_vals) if len(dyn_vals) >= 2 else 20.0
    onset_scale = _robust_range(onset_vals) if len(onset_vals) >= 2 else 2.0

    # Floor to avoid division by near-zero (e.g. all tracks identical)
    dyn_scale = max(dyn_scale, 0.5)
    onset_scale = max(onset_scale, 0.1)

    return dyn_scale, onset_scale


def _track_distance(ta: dict, tb: dict, emb_a, emb_b,
                    texture_scales=None) -> float:
    """Compute distance between two tracks using all available features.

    Parameters
    ----------
    texture_scales : tuple[float, float] | None
        (dyn_range_scale, onset_range_scale) — per-playlist calibrated
        normalization divisors.  If None, defaults to (20.0, 2.0).
    """
    if texture_scales is None:
        dyn_scale, onset_scale = 20.0, 2.0
    else:
        dyn_scale, onset_scale = texture_scales

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

    # ─── Texture distance (harm_ratio, flatness, dynamic_range, onset_str) ────
    # All four use end_seg_a vs start_seg_b (splice pattern).
    # Each sub-component normalized to [0,1], then equally weighted (0.25 each).

    # harmonic ratio: [0,1] naturally, abs diff is already in [0,1]
    harm_a = float(end_a.get("harm_ratio", fa.get("harm_ratio", 0.5)))
    harm_b = float(start_b.get("harm_ratio", fb.get("harm_ratio", 0.5)))
    d_harm = min(abs(harm_a - harm_b), 1.0)

    # spectral flatness: [0,1] naturally
    flat_a = float(end_a.get("flatness", fa.get("flatness", 0.5)))
    flat_b = float(start_b.get("flatness", fb.get("flatness", 0.5)))
    d_flat = min(abs(flat_a - flat_b), 1.0)

    # dynamic range (dB): normalize by per-playlist robust p5-p95 range.
    # If not calibrated, dyn_scale defaults to 20.0 (empirical estimate).
    # Using a per-playlist range means a 2 dB difference is treated as more
    # significant in a playlist where the total spread is only 4 dB vs one
    # spanning 30 dB — matching how humans perceive contrast.
    dyn_a = float(end_a.get("dynamic_range", fa.get("dynamic_range", 10.0)))
    dyn_b = float(start_b.get("dynamic_range", fb.get("dynamic_range", 10.0)))
    d_dyn = min(abs(dyn_a - dyn_b) / dyn_scale, 1.0)

    # onset strength: normalize by per-playlist robust p5-p95 range.
    # If not calibrated, onset_scale defaults to 2.0 (empirical estimate).
    onset_a = float(end_a.get("onset_str", fa.get("onset_str", 1.0)))
    onset_b = float(start_b.get("onset_str", fb.get("onset_str", 1.0)))
    d_onset = min(abs(onset_a - onset_b) / onset_scale, 1.0)

    d_texture = 0.25 * d_harm + 0.25 * d_flat + 0.25 * d_dyn + 0.25 * d_onset

    # ─── Frequency balance (bass, mid, high vector distance) ──────────────────
    bass_a = float(end_a.get("bass", fa.get("bass", 0.33)))
    mid_a = float(end_a.get("mid", fa.get("mid", 0.33)))
    high_a = float(end_a.get("high", fa.get("high", 0.33)))
    bass_b = float(start_b.get("bass", fb.get("bass", 0.33)))
    mid_b = float(start_b.get("mid", fb.get("mid", 0.33)))
    high_b = float(start_b.get("high", fb.get("high", 0.33)))

    # Normalize each vector to unit sum (handle zero vectors)
    sum_a = bass_a + mid_a + high_a
    sum_b = bass_b + mid_b + high_b
    if sum_a > 1e-9:
        bass_a /= sum_a
        mid_a /= sum_a
        high_a /= sum_a
    if sum_b > 1e-9:
        bass_b /= sum_b
        mid_b /= sum_b
        high_b /= sum_b

    d_freq = np.sqrt((bass_a - bass_b) ** 2 + (mid_a - mid_b) ** 2 + (high_a - high_b) ** 2)
    d_freq_balance = min(d_freq / np.sqrt(2.0), 1.0)  # normalize to [0,1]

    base = (
        WEIGHTS["mood"] * d_mood
        + WEIGHTS["bpm"] * d_bpm
        + WEIGHTS["transition"] * d_transition
        + WEIGHTS["key"] * d_key
        + WEIGHTS["energy"] * d_energy
        + WEIGHTS["texture"] * d_texture
        + WEIGHTS["freq_balance"] * d_freq_balance
    )

    artist_a = _artist_tokens(ta.get("artist", ""))
    artist_b = _artist_tokens(tb.get("artist", ""))
    album_a = _norm_text(ta.get("album", ""))
    album_b = _norm_text(tb.get("album", ""))
    if album_a and album_b and album_a == album_b:
        base += _cfg.ALBUM_PENALTY
    elif artist_a and artist_b and (artist_a & artist_b):
        base += _cfg.ARTIST_PENALTY

    return min(base, 1.0 + _cfg.ALBUM_PENALTY)


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
    track_indices: list, all_tracks: list, embeddings: list,
    texture_scales=None,
) -> "np.ndarray":
    """Build pairwise distance matrix for a list of tracks.

    Parameters
    ----------
    texture_scales : tuple[float, float] | None
        Per-playlist calibration divisors for dynamic_range and onset_str.
        Passed through to ``_track_distance()``.  If None, uses defaults.
    """
    n = len(track_indices)
    D = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        for j in range(n):
            if i != j:
                D[i, j] = _track_distance(
                    all_tracks[track_indices[i]],
                    all_tracks[track_indices[j]],
                    embeddings[track_indices[i]],
                    embeddings[track_indices[j]],
                    texture_scales=texture_scales,
                )
    return D