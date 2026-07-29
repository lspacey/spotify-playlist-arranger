"""Per-playlist audio-feature calibration and cache.

Computes calibration scales for every distance component based on the
CURRENT playlist's feature distributions (not global constants).  Results
are cached keyed by (playlist_id, snapshot_id) so that the workflow runs
only once per playlist version.

Do NOT import any UI framework (matplotlib, NiceGUI) — this module is pure
data/logic.  Histogram data is returned as raw ``(counts, edges)`` tuples,
ready for rendering by any charting library.
"""

from __future__ import annotations

import datetime
import json
import logging
import pathlib
from dataclasses import dataclass, field

import numpy as np

import playlist_arranger.config as _cfg
from playlist_arranger.sorting.distance import _robust_range, _cos_dist

logger = logging.getLogger(__name__)

# ── Signal-quality thresholds (editable constants, not magic numbers) ────────
CV_GOOD_THRESHOLD = 0.20    # CV above this → "good discriminative signal" (green)
CV_MODERATE_THRESHOLD = 0.10  # CV below this → "flat — little effect" (red)
# Between the two → "moderate" (yellow)
HISTOGRAM_BINS = 12           # default bin count for each component histogram


# ── Component metadata (used by both analysis and solver) ─────────────────────

@dataclass
class ComponentCalibration:
    """Calibration scale and observed stats for one distance component."""
    name: str                          # e.g. "mood", "bpm", "flatness"
    calibration_scale: float = 1.0     # divisor for raw diff BEFORE clamping to [0,1]
    observed_min: float = 0.0
    observed_max: float = 0.0
    observed_mean: float = 0.0
    observed_std: float = 0.0
    observed_cv: float = 0.0
    raw_values: list[float] = field(default_factory=list)
    histogram_counts: list[int] = field(default_factory=list)
    histogram_edges: list[float] = field(default_factory=list)
    mood_mode: str = ""                # "embedding" | "chroma_fallback" — only for mood component


@dataclass
class StatsResult:
    """All computed calibration scales + histogram data for a playlist."""
    playlist_id: str
    snapshot_id: str
    analysed_at: str = ""
    components: dict[str, ComponentCalibration] = field(default_factory=dict)
    weights_used: dict[str, float] = field(default_factory=dict)
    last_used_for_sort_at: str | None = None


# ── Cache I/O ─────────────────────────────────────────────────────────────────

def _stats_cache_path(playlist_id: str, snapshot_id: str) -> pathlib.Path:
    safe_id = playlist_id.replace("/", "_").replace("\\", "_")
    safe_snap = snapshot_id.replace("/", "_").replace("\\", "_") if snapshot_id else "nosnap"
    return _cfg.CACHE_DIR_DEFAULT / f"{safe_id}_{safe_snap}_stats.json"


def save_stats_cache(result: StatsResult) -> str:
    """Persist ``StatsResult`` to cache JSON.  Returns the file path.

    Also prunes any other cached stats files for the SAME playlist_id
    with a different snapshot_id, preventing unbounded cache growth.
    """
    _cfg.CACHE_DIR_DEFAULT.mkdir(parents=True, exist_ok=True)
    cache_path = _stats_cache_path(result.playlist_id, result.snapshot_id)

    # ── Prune old stats files for this playlist_id ─────────────────────────
    safe_id = result.playlist_id.replace("/", "_").replace("\\", "_")
    import glob
    pattern = str(_cfg.CACHE_DIR_DEFAULT / f"{safe_id}_*_stats.json")
    for old_path in glob.glob(pattern):
        if old_path != str(cache_path):
            try:
                pathlib.Path(old_path).unlink()
                logger.debug("Pruned stale stats cache: %s", old_path)
            except OSError:
                pass

    data: dict = {
        "playlist_id": result.playlist_id,
        "snapshot_id": result.snapshot_id,
        "analysed_at": result.analysed_at,
        "weights_used": result.weights_used,
        "last_used_for_sort_at": result.last_used_for_sort_at,
        "components": {},
    }
    for name, comp in result.components.items():
        entry = {
            "calibration_scale": comp.calibration_scale,
            "observed_min": comp.observed_min,
            "observed_max": comp.observed_max,
            "observed_mean": comp.observed_mean,
            "observed_std": comp.observed_std,
            "observed_cv": comp.observed_cv,
            "histogram_counts": comp.histogram_counts,
            "histogram_edges": comp.histogram_edges,
        }
        if comp.mood_mode:
            entry["mood_mode"] = comp.mood_mode
        data["components"][name] = entry
    cache_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Stats cache saved to %s", cache_path)
    return str(cache_path)


def load_stats_cache(playlist_id: str, snapshot_id: str) -> StatsResult | None:
    """Load a previously-saved ``StatsResult`` from cache.

    Returns ``None`` if:
    - no cache file exists
    - ``snapshot_id`` doesn't match (stale cache)
    - the JSON is missing required components (schema mismatch)
    """
    cache_path = _stats_cache_path(playlist_id, snapshot_id)
    if not cache_path.exists():
        logger.debug("No stats cache for %s (snapshot %s)", playlist_id[:8], snapshot_id[:8] if snapshot_id else "?")
        return None
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Corrupted stats cache %s — ignoring", cache_path)
        return None

    if raw.get("playlist_id") != playlist_id:
        logger.warning("Stats cache playlist_id mismatch: %s", raw.get("playlist_id"))
        return None
    if raw.get("snapshot_id") != snapshot_id:
        logger.info("Stats cache snapshot_id changed (stale): %s != %s", raw.get("snapshot_id"), snapshot_id)
        return None
    components_raw = raw.get("components")
    if not isinstance(components_raw, dict) or len(components_raw) == 0:
        logger.warning("Stats cache %s has no component data — treating as stale", cache_path)
        return None

    result = StatsResult(
        playlist_id=playlist_id,
        snapshot_id=snapshot_id,
        analysed_at=raw.get("analysed_at", ""),
        weights_used=raw.get("weights_used", {}),
        last_used_for_sort_at=raw.get("last_used_for_sort_at"),
    )
    for name, cdata in components_raw.items():
        if not isinstance(cdata, dict):
            continue
        result.components[name] = ComponentCalibration(
            name=name,
            calibration_scale=cdata.get("calibration_scale", 1.0),
            observed_min=cdata.get("observed_min", 0.0),
            observed_max=cdata.get("observed_max", 0.0),
            observed_mean=cdata.get("observed_mean", 0.0),
            observed_std=cdata.get("observed_std", 0.0),
            observed_cv=cdata.get("observed_cv", 0.0),
            histogram_counts=cdata.get("histogram_counts", []),
            histogram_edges=cdata.get("histogram_edges", []),
            mood_mode=cdata.get("mood_mode", ""),
        )
    logger.info("Loaded stats cache from %s (%d components)", cache_path, len(result.components))
    return result


# ── Analysis engine ───────────────────────────────────────────────────────────

def _extract_feature_values(all_tracks: list) -> dict[str, list[float]]:
    """Collect raw per-track feature values from DB entries."""
    feats: dict[str, list[float]] = {
        "bpm": [], "rms_db": [], "harm_ratio": [], "flatness": [],
        "dynamic_range": [], "onset_str": [],
        "bass": [], "mid": [], "high": [],
    }
    for t in all_tracks:
        fa = t.get("features") or {}
        se = t.get("start_seg") or {}
        ee = t.get("end_seg") or {}
        for sec in (fa, se, ee):
            for key in feats:
                if key in sec:
                    feats[key].append(float(sec[key]))
    return feats


def _compute_raw_mfcc_cos_dist(all_tracks: list) -> list[float]:
    """Compute raw MFCC cosine distances across all (i,j), i≠j — UNCLAMPED."""
    n = len(all_tracks)
    raw_vals = []
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            ta = all_tracks[i]; tb = all_tracks[j]
            fa = ta.get("features") or {}; fb = tb.get("features") or {}
            end_a = ta.get("end_seg", fa); start_b = tb.get("start_seg", fb)
            mfcc_a = (end_a.get("mfcc20") or end_a.get("mfcc13")
                      or fa.get("mfcc20") or fa.get("mfcc13"))
            mfcc_b = (start_b.get("mfcc20") or start_b.get("mfcc13")
                      or fb.get("mfcc20") or fb.get("mfcc13"))
            if mfcc_a and mfcc_b:
                va = np.array(mfcc_a, dtype=np.float32)
                vb = np.array(mfcc_b, dtype=np.float32)
                nc = min(len(va), len(vb))
                raw_vals.append(_cos_dist(va[:nc], vb[:nc]))
    return raw_vals


def _compute_component_values(
    all_tracks: list, embeddings: list,
    calibration: dict[str, float],
) -> dict[str, list[float]]:
    """Compute raw sub-distance values for every component across all (i,j), i≠j."""
    from playlist_arranger.sorting.distance import _camelot_distance

    n = len(all_tracks)
    comps: dict[str, list[float]] = {
        "mood": [], "bpm": [], "transition": [], "key": [],
        "energy": [], "texture": [], "freq_balance": [],
        "harm_ratio": [], "flatness": [], "dynamic_range": [], "onset_str": [],
    }
    dyn_scale = calibration.get("dyn_scale", 20.0)
    onset_scale = calibration.get("onset_scale", 2.0)
    flat_scale = calibration.get("flat_scale", 0.01)
    trans_scale = calibration.get("transition_scale", 0.25)

    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            ta = all_tracks[i]; tb = all_tracks[j]
            fa = ta.get("features") or {}; fb = tb.get("features") or {}
            end_a = ta.get("end_seg", fa); start_b = tb.get("start_seg", fb)
            emb_a = embeddings[i] if i < len(embeddings) else None
            emb_b = embeddings[j] if j < len(embeddings) else None

            # mood
            if emb_a is not None and emb_b is not None:
                d_mood = min(_cos_dist(emb_a, emb_b) / 2.0, 1.0)
            else:
                ca = fa.get("chroma_cens") or fa.get("chroma_vals")
                cb = fb.get("chroma_cens") or fb.get("chroma_vals")
                if ca and cb:
                    nc = min(len(ca), len(cb))
                    d_mood = min(_cos_dist(
                        np.array(ca[:nc], dtype=np.float32),
                        np.array(cb[:nc], dtype=np.float32)) / 2.0, 1.0)
                else:
                    d_mood = 0.5
            comps["mood"].append(d_mood)

            # bpm
            bpm_a = end_a.get("bpm", fa.get("bpm", 120))
            bpm_b = start_b.get("bpm", fb.get("bpm", 120))
            comps["bpm"].append(min(abs(bpm_a - bpm_b) / 200.0, 1.0))

            # transition (MFCC cosine — per-playlist calibrated)
            mfcc_a = (end_a.get("mfcc20") or end_a.get("mfcc13")
                      or fa.get("mfcc20") or fa.get("mfcc13"))
            mfcc_b = (start_b.get("mfcc20") or start_b.get("mfcc13")
                      or fb.get("mfcc20") or fb.get("mfcc13"))
            if mfcc_a and mfcc_b:
                va = np.array(mfcc_a, dtype=np.float32)
                vb = np.array(mfcc_b, dtype=np.float32)
                nc = min(len(va), len(vb))
                comps["transition"].append(min(_cos_dist(va[:nc], vb[:nc]) / trans_scale, 1.0))
            else:
                comps["transition"].append(0.5)

            # key
            cam_a = end_a.get("camelot", fa.get("camelot", "?"))
            cam_b = start_b.get("camelot", fb.get("camelot", "?"))
            comps["key"].append(_camelot_distance(cam_a, cam_b))

            # energy
            rms_a = end_a.get("rms_db", fa.get("rms_db", -20))
            rms_b = start_b.get("rms_db", fb.get("rms_db", -20))
            comps["energy"].append(min(abs(rms_a - rms_b) / 60.0, 1.0))

            # texture sub-components
            harm_a = float(end_a.get("harm_ratio", fa.get("harm_ratio", 0.5)))
            harm_b = float(start_b.get("harm_ratio", fb.get("harm_ratio", 0.5)))
            comps["harm_ratio"].append(min(abs(harm_a - harm_b), 1.0))

            flat_a = float(end_a.get("flatness", fa.get("flatness", 0.5)))
            flat_b = float(start_b.get("flatness", fb.get("flatness", 0.5)))
            comps["flatness"].append(min(abs(flat_a - flat_b) / flat_scale, 1.0))

            dyn_a = float(end_a.get("dynamic_range", fa.get("dynamic_range", 10.0)))
            dyn_b = float(start_b.get("dynamic_range", fb.get("dynamic_range", 10.0)))
            comps["dynamic_range"].append(min(abs(dyn_a - dyn_b) / dyn_scale, 1.0))

            onset_a = float(end_a.get("onset_str", fa.get("onset_str", 1.0)))
            onset_b = float(start_b.get("onset_str", fb.get("onset_str", 1.0)))
            comps["onset_str"].append(min(abs(onset_a - onset_b) / onset_scale, 1.0))

            d_texture = 0.25 * comps["harm_ratio"][-1] + 0.25 * comps["flatness"][-1] \
                + 0.25 * comps["dynamic_range"][-1] + 0.25 * comps["onset_str"][-1]
            comps["texture"].append(d_texture)

            # freq_balance
            bass_a = float(end_a.get("bass", fa.get("bass", 0.33)))
            mid_a = float(end_a.get("mid", fa.get("mid", 0.33)))
            high_a = float(end_a.get("high", fa.get("high", 0.33)))
            bass_b = float(start_b.get("bass", fb.get("bass", 0.33)))
            mid_b = float(start_b.get("mid", fb.get("mid", 0.33)))
            high_b = float(start_b.get("high", fb.get("high", 0.33)))
            sum_a = bass_a + mid_a + high_a
            sum_b = bass_b + mid_b + high_b
            if sum_a > 1e-9:
                bass_a /= sum_a; mid_a /= sum_a; high_a /= sum_a
            else:
                bass_a = mid_a = high_a = 1.0/3.0
            if sum_b > 1e-9:
                bass_b /= sum_b; mid_b /= sum_b; high_b /= sum_b
            else:
                bass_b = mid_b = high_b = 1.0/3.0
            d_freq = np.sqrt((bass_a-bass_b)**2 + (mid_a-mid_b)**2 + (high_a-high_b)**2)
            comps["freq_balance"].append(min(d_freq / np.sqrt(2.0), 1.0))

    return comps


def analyze_playlist_stats(
    playlist_id: str,
    snapshot_id: str,
    all_tracks: list,
    embeddings: list,
    weights: dict[str, float] | None = None,
) -> StatsResult:
    """Run a full per-component calibration analysis on a playlist.

    Parameters
    ----------
    playlist_id : str
        Spotify playlist ID.
    snapshot_id : str
        Spotify snapshot_id for cache invalidation.
    all_tracks : list
        Track records from the DB (must have ``features``, ``start_seg``,
        ``end_seg`` dicts).
    embeddings : list
        MERT embedding arrays (same order as *all_tracks*), or ``None``
        entries where embeddings are unavailable.  Pass ``[]`` to skip.
    weights : dict[str, float] | None
        Current user-configured weights (for recording in the cache).
        Defaults to ``config.WEIGHTS`` if ``None``.

    Returns
    -------
    StatsResult
    """
    if weights is None:
        weights = dict(_cfg.WEIGHTS)

    # ── Step 1: calibrate per-playlist feature scales ─────────────────────
    feats = _extract_feature_values(all_tracks)

    dyn_scale = _robust_range(feats["dynamic_range"]) if len(feats["dynamic_range"]) >= 2 else 20.0
    onset_scale = _robust_range(feats["onset_str"]) if len(feats["onset_str"]) >= 2 else 2.0
    dyn_scale = max(dyn_scale, 0.5)
    onset_scale = max(onset_scale, 0.1)

    flat_scale = _robust_range(feats["flatness"], floor=0.002) if len(feats["flatness"]) >= 2 else 0.01
    flat_scale = max(flat_scale, 0.002)

    # ── Step 2: calibrate transition scale from raw MFCC cos_dist ──────────
    raw_transition = _compute_raw_mfcc_cos_dist(all_tracks)
    transition_scale = _robust_range(raw_transition, floor=0.01) if len(raw_transition) >= 2 else 0.25
    transition_scale = max(transition_scale, 0.01)

    calibration = {
        "dyn_scale": float(dyn_scale),
        "onset_scale": float(onset_scale),
        "flatness_scale": float(flat_scale),
        "flat_scale": float(flat_scale),
        "transition_scale": float(transition_scale),
    }

    # ── Step 3: compute raw sub-distance values for every pair ────────────
    comp_vals = _compute_component_values(all_tracks, embeddings, calibration)

    # ── Step 4: build ComponentCalibration for each component ────────────
    result = StatsResult(
        playlist_id=playlist_id,
        snapshot_id=snapshot_id,
        analysed_at=datetime.datetime.now().isoformat(),
        weights_used=dict(weights),
    )

    has_embeddings = any(e is not None for e in embeddings)
    mood_mode = "embedding" if has_embeddings else "chroma_fallback"

    for comp_name in ["mood", "bpm", "transition", "key", "energy", "texture", "freq_balance",
                      "harm_ratio", "flatness", "dynamic_range", "onset_str"]:
        vals = comp_vals.get(comp_name)
        if vals is None or len(vals) == 0:
            continue
        arr = np.array(vals, dtype=np.float64)
        mean_v = float(np.mean(arr))
        std_v = float(np.std(arr))
        cv_v = std_v / mean_v if mean_v > 1e-9 else 0.0
        hist_counts, hist_edges = np.histogram(arr, bins=HISTOGRAM_BINS)
        result.components[comp_name] = ComponentCalibration(
            name=comp_name,
            calibration_scale=calibration.get(f"{comp_name}_scale", 1.0),
            mood_mode=mood_mode if comp_name == "mood" else "",
            observed_min=float(np.min(arr)),
            observed_max=float(np.max(arr)),
            observed_mean=mean_v,
            observed_std=std_v,
            observed_cv=cv_v,
            raw_values=vals,
            histogram_counts=[int(c) for c in hist_counts],
            histogram_edges=[float(e) for e in hist_edges],
        )

    return result