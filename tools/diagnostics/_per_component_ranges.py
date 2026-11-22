"""One-shot: compute raw min/max/mean for each of the 7 distance sub-components
on the real 61-track playlist, BEFORE weighting.

NOT part of the test suite — not auto-discovered by pytest.
Run manually:  python tools/diagnostics/_per_component_ranges.py
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent.resolve()))

import numpy as np

from playlist_arranger.sorting.distance import (
    _cos_dist,
    _camelot_distance,
    _load_embedding,
    _calibrate_texture_scales,
)
from playlist_arranger.database import db as _db

PL_ID = "5ny37mvOmLQ7kwjV19B3Dc"
CACHE_FILE = pathlib.Path(
    "cache/5ny37mvOmLQ7kwjV19B3Dc-AAAAztHygx9kuqte7KbYx7vQf1dkwhSb.tracks.json"
)


def _extract_d_mood(fa, fb, end_a, start_b, emb_a, emb_b):
    """Replicate the mood sub-distance computation from _track_distance."""
    if emb_a is not None and emb_b is not None:
        return min(_cos_dist(emb_a, emb_b) / 2.0, 1.0)
    ca = fa.get("chroma_cens") or fa.get("chroma_vals")
    cb = fb.get("chroma_cens") or fb.get("chroma_vals")
    if ca and cb:
        n = min(len(ca), len(cb))
        return min(
            _cos_dist(
                np.array(ca[:n], dtype=np.float32),
                np.array(cb[:n], dtype=np.float32),
            ) / 2.0, 1.0)
    return 0.5


def _extract_d_bpm(end_a, start_b, fa, fb):
    bpm_a = end_a.get("bpm", fa.get("bpm", 120))
    bpm_b = start_b.get("bpm", fb.get("bpm", 120))
    return min(abs(bpm_a - bpm_b) / 200.0, 1.0)


def _extract_d_transition(end_a, start_b, fa, fb):
    mfcc_a = end_a.get("mfcc20") or end_a.get("mfcc13") or fa.get("mfcc20") or fa.get("mfcc13")
    mfcc_b = start_b.get("mfcc20") or start_b.get("mfcc13") or fb.get("mfcc20") or fb.get("mfcc13")
    if mfcc_a and mfcc_b:
        va = np.array(mfcc_a, dtype=np.float32)
        vb = np.array(mfcc_b, dtype=np.float32)
        n = min(len(va), len(vb))
        return min(_cos_dist(va[:n], vb[:n]) / 2.0, 1.0)
    return 0.5


def _extract_d_key(end_a, start_b, fa, fb):
    cam_a = end_a.get("camelot", fa.get("camelot", "?"))
    cam_b = start_b.get("camelot", fb.get("camelot", "?"))
    return _camelot_distance(cam_a, cam_b)


def _extract_d_energy(end_a, start_b, fa, fb):
    rms_a = end_a.get("rms_db", fa.get("rms_db", -20))
    rms_b = start_b.get("rms_db", fb.get("rms_db", -20))
    return min(abs(rms_a - rms_b) / 60.0, 1.0)


def _extract_d_texture(end_a, start_b, fa, fb, dyn_scale, onset_scale):
    harm_a = float(end_a.get("harm_ratio", fa.get("harm_ratio", 0.5)))
    harm_b = float(start_b.get("harm_ratio", fb.get("harm_ratio", 0.5)))
    d_harm = min(abs(harm_a - harm_b), 1.0)

    flat_a = float(end_a.get("flatness", fa.get("flatness", 0.5)))
    flat_b = float(start_b.get("flatness", fb.get("flatness", 0.5)))
    d_flat = min(abs(flat_a - flat_b), 1.0)

    dyn_a = float(end_a.get("dynamic_range", fa.get("dynamic_range", 10.0)))
    dyn_b = float(start_b.get("dynamic_range", fb.get("dynamic_range", 10.0)))
    d_dyn = min(abs(dyn_a - dyn_b) / dyn_scale, 1.0)

    onset_a = float(end_a.get("onset_str", fa.get("onset_str", 1.0)))
    onset_b = float(start_b.get("onset_str", fb.get("onset_str", 1.0)))
    d_onset = min(abs(onset_a - onset_b) / onset_scale, 1.0)

    d_texture = 0.25 * d_harm + 0.25 * d_flat + 0.25 * d_dyn + 0.25 * d_onset
    return d_texture, d_harm, d_flat, d_dyn, d_onset


def _extract_d_freq_balance(end_a, start_b, fa, fb):
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
    return min(d_freq / np.sqrt(2.0), 1.0)


# ── Also extract raw feature ranges to understand underlying data ───────────

def extract_raw_features(all_tracks):
    """Extract per-feature raw ranges across the playlist."""
    features = {
        "bpm": [], "rms_db": [], "camelot_present": 0, "camelot_missing": 0,
        "harm_ratio": [], "flatness": [], "dynamic_range": [],
        "onset_str": [], "bass": [], "mid": [], "high": [],
        "has_mfcc": 0, "has_chroma": 0, "has_embedding": 0,
    }
    for t in all_tracks:
        fa = t.get("features") or {}
        se = t.get("start_seg") or {}
        ee = t.get("end_seg") or {}
        for sec in (fa, se, ee):
            if "bpm" in sec: features["bpm"].append(float(sec["bpm"]))
            if "rms_db" in sec: features["rms_db"].append(float(sec["rms_db"]))
            if "harm_ratio" in sec: features["harm_ratio"].append(float(sec["harm_ratio"]))
            if "flatness" in sec: features["flatness"].append(float(sec["flatness"]))
            if "dynamic_range" in sec: features["dynamic_range"].append(float(sec["dynamic_range"]))
            if "onset_str" in sec: features["onset_str"].append(float(sec["onset_str"]))
            if "bass" in sec: features["bass"].append(float(sec["bass"]))
            if "mid" in sec: features["mid"].append(float(sec["mid"]))
            if "high" in sec: features["high"].append(float(sec["high"]))
        cam = fa.get("camelot", "?")
        if cam != "?":
            features["camelot_present"] += 1
        else:
            features["camelot_missing"] += 1
        if (fa.get("mfcc20") or fa.get("mfcc13") or
            ee.get("mfcc20") or ee.get("mfcc13")):
            features["has_mfcc"] += 1
        if fa.get("chroma_cens") or fa.get("chroma_vals"):
            features["has_chroma"] += 1
        if t.get("embedding_file"):
            features["has_embedding"] += 1
    return features


def main():
    if not CACHE_FILE.exists():
        print(f"ERROR: Cache file not found: {CACHE_FILE}")
        return 1
    tracks = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    db_dict = _db.load_all()
    track_ids = [t["id"] for t in tracks]
    all_tracks = [db_dict[tid] for tid in track_ids if tid in db_dict]
    n = len(all_tracks)
    print(f"Playlist: {n} tracks in DB")

    # ── Raw feature ranges ────────────────────────────────────────────────
    rf = extract_raw_features(all_tracks)
    print("\n=== Raw Feature Ranges Across Playlist ===")
    for key in ["bpm", "rms_db", "harm_ratio", "flatness", "dynamic_range", "onset_str"]:
        if rf[key]:
            vals = rf[key]
            print(f"  {key:>16s}: min={min(vals):.4f} max={max(vals):.4f} "
                  f"range={max(vals)-min(vals):.4f} mean={np.mean(vals):.4f}")
    print(f"  {'camelot':>16s}: {rf['camelot_present']} present, {rf['camelot_missing']} missing")
    print(f"  {'has_mfcc':>16s}: {rf['has_mfcc']}/{n}")
    print(f"  {'has_chroma':>16s}: {rf['has_chroma']}/{n}")
    print(f"  {'has_embedding':>16s}: {rf['has_embedding']}/{n}")
    # bass/mid/high
    for key in ["bass", "mid", "high"]:
        if rf[key]:
            vals = rf[key]
            print(f"  {key:>16s}: min={min(vals):.4f} max={max(vals):.4f} "
                  f"range={max(vals)-min(vals):.4f} sum_mean={np.mean(vals):.4f}")

    embeddings = [_load_embedding(tid, db_dict) for tid in track_ids]
    dyn_scale, onset_scale = _calibrate_texture_scales(all_tracks)
    print(f"\nTexture calibration: dyn_scale={dyn_scale:.2f} onset_scale={onset_scale:.2f}")

    # ── Compute per-component distributions over all (i,j) pairs, i != j ───
    comps = {
        "mood": [], "bpm": [], "transition": [], "key": [],
        "energy": [], "texture": [], "freq_balance": [],
        # sub-components of texture
        "harm_ratio": [], "flatness": [], "dynamic_range": [], "onset_str": [],
    }

    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            ta = all_tracks[i]; tb = all_tracks[j]
            fa = ta.get("features") or {}; fb = tb.get("features") or {}
            end_a = ta.get("end_seg", fa); start_b = tb.get("start_seg", fb)
            emb_a = embeddings[i]; emb_b = embeddings[j]

            d_mood = _extract_d_mood(fa, fb, end_a, start_b, emb_a, emb_b)
            d_bpm = _extract_d_bpm(end_a, start_b, fa, fb)
            d_transition = _extract_d_transition(end_a, start_b, fa, fb)
            d_key = _extract_d_key(end_a, start_b, fa, fb)
            d_energy = _extract_d_energy(end_a, start_b, fa, fb)
            d_texture, d_harm, d_flat, d_dyn, d_onset = _extract_d_texture(
                end_a, start_b, fa, fb, dyn_scale, onset_scale)
            d_freq = _extract_d_freq_balance(end_a, start_b, fa, fb)

            comps["mood"].append(d_mood)
            comps["bpm"].append(d_bpm)
            comps["transition"].append(d_transition)
            comps["key"].append(d_key)
            comps["energy"].append(d_energy)
            comps["texture"].append(d_texture)
            comps["freq_balance"].append(d_freq)
            comps["harm_ratio"].append(d_harm)
            comps["flatness"].append(d_flat)
            comps["dynamic_range"].append(d_dyn)
            comps["onset_str"].append(d_onset)

    # ── Report table ──────────────────────────────────────────────────────
    weights = {
        "mood": 0.48, "bpm": 0.12, "transition": 0.20, "key": 0.12,
        "energy": 0.08, "texture": 0.10, "freq_balance": 0.08,
    }

    print("\n=== Per-Component Raw Sub-Distance Distributions (pre-weight) ===")
    print(f"{'Component':>14s} | {'Raw min':>8s} | {'Raw max':>8s} | {'Raw range':>9s} | "
          f"{'Raw mean':>8s} | {'Weight':>6s} | {'Effective max':>12s} | {'% of total max':>13s}")
    print("-" * 110)

    total_max = 0.0
    rows = []
    for comp_name in ["mood", "bpm", "transition", "key", "energy", "texture", "freq_balance"]:
        vals = comps[comp_name]
        w = weights[comp_name]
        raw_min = min(vals)
        raw_max = max(vals)
        raw_range = raw_max - raw_min
        raw_mean = np.mean(vals)
        effective_max = raw_max * w
        rows.append((comp_name, raw_min, raw_max, raw_range, raw_mean, w, effective_max))
        total_max += effective_max

    rows.sort(key=lambda r: r[6])  # sort by effective max
    for comp_name, raw_min, raw_max, raw_range, raw_mean, w, effective_max in rows:
        pct = effective_max / total_max * 100 if total_max > 0 else 0
        print(f"{comp_name:>14s} | {raw_min:8.4f} | {raw_max:8.4f} | {raw_range:9.4f} | "
              f"{raw_mean:8.4f} | {w:6.3f} | {effective_max:12.4f} | {pct:12.1f}%")

    print(f"\nTotal max possible sum (sum of effective max): {total_max:.4f}")

    # ── Texture sub-components ─────────────────────────────────────────────
    print("\n=== Texture Sub-Components (pre-weight) ===")
    for sub in ["harm_ratio", "flatness", "dynamic_range", "onset_str"]:
        vals = comps[sub]
        print(f"  {sub:>16s}: min={min(vals):.4f} max={max(vals):.4f} "
              f"range={max(vals)-min(vals):.4f} mean={np.mean(vals):.4f} "
              f"std={np.std(vals):.4f}")

    # ── Flag assessment ────────────────────────────────────────────────────
    print("\n=== Normalization Assessment ===")
    issues = []
    max_eff = max(r[6] for r in rows)
    min_eff = min(r[6] for r in rows)
    for comp_name, raw_min, raw_max, raw_range, raw_mean, w, effective_max in rows:
        ratio_to_max = effective_max / max_eff if max_eff > 0 else 1.0
        if ratio_to_max < 0.15:
            issues.append(f"  ** {comp_name}: effective max {effective_max:.4f} is only "
                          f"{ratio_to_max:.1%} of the largest contributor ({max_eff:.4f}). "
                          f"The weight {w:.3f} overstates its actual influence.")

    if issues:
        print("Components with disproportionate effective contribution:")
        for issue in issues:
            print(issue)
    else:
        print("All components have comparable effective ranges — weights are meaningful.")

    return 0


if __name__ == "__main__":
    sys.exit(main())