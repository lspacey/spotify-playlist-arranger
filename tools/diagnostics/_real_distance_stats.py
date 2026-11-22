"""One-shot: compute distance matrix stats + CSV + histogram for a playlist.

NOT part of the test suite — not auto-discovered by pytest.
Run manually:  python tools/diagnostics/_real_distance_stats.py
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent.resolve()))

from playlist_arranger.sorting.distance import (
    _build_distance_matrix,
    _compute_distance_stats,
    _dump_distance_csv,
    _render_distance_histogram,
    _load_embedding,
    _SORTING_CACHE,
    _calibrate_texture_scales,
)
from playlist_arranger.database import db as _db

PL_ID = "5ny37mvOmLQ7kwjV19B3Dc"
CACHE_FILE = pathlib.Path(
    "cache/5ny37mvOmLQ7kwjV19B3Dc-AAAAztHygx9kuqte7KbYx7vQf1dkwhSb.tracks.json"
)


def main():
    # ── Load cached tracks ──────────────────────────────────────────────
    if not CACHE_FILE.exists():
        print(f"ERROR: Cache file not found: {CACHE_FILE}")
        return 1
    tracks = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    print(f"Loaded {len(tracks)} cached tracks")

    # ── Load DB ─────────────────────────────────────────────────────────
    db_dict = _db.load_all()
    track_ids = [t["id"] for t in tracks]
    all_tracks = [db_dict[tid] for tid in track_ids if tid in db_dict]
    missing = len(track_ids) - len(all_tracks)
    if missing:
        print(f"WARNING: {missing} track(s) not in DB")
    if len(all_tracks) == 0:
        print("ERROR: No tracks found in DB")
        return 1
    print(f"{len(all_tracks)} tracks in DB")

    # ── Load embeddings ─────────────────────────────────────────────────
    embeddings = [_load_embedding(tid, db_dict) for tid in track_ids]
    embedding_count = sum(1 for e in embeddings if e is not None)
    print(f"{embedding_count}/{len(embeddings)} embeddings loaded")

    # ── Clear stale sort cache ──────────────────────────────────────────
    _SORTING_CACHE.pop(PL_ID, None)

    # ── Build distance matrix ───────────────────────────────────────────
    dyn_scale, onset_scale = _calibrate_texture_scales(all_tracks)
    print(f"Calibration: dyn_scale={dyn_scale:.2f}, onset_scale={onset_scale:.2f}")

    n = len(all_tracks)
    D = _build_distance_matrix(
        list(range(n)), all_tracks, embeddings,
        texture_scales=(dyn_scale, onset_scale),
    )
    print(f"Distance matrix: {D.shape}")

    # ── Stats ───────────────────────────────────────────────────────────
    stats = _compute_distance_stats(D)
    print()
    print("=== REAL 61-track Distance Matrix Stats ===")
    print(f"  min={stats['min']:.4f}  max={stats['max']:.4f}")
    print(f"  mean={stats['mean']:.4f}  median={stats['median']:.4f}")
    print(f"  std={stats['std']:.4f}  CV={stats['cv']:.4f}")
    print(f"  n={stats['n']} off-diagonal entries")

    if stats["cv"] < 0.15:
        print("  WARNING: CV < 0.15 — matrix is very flat, SA has limited signal")
    elif stats["cv"] < 0.25:
        print("  CAUTION: CV < 0.25 — modest spread")
    else:
        print("  OK: CV >= 0.25 — meaningful structure")

    # ── CSV dump ────────────────────────────────────────────────────────
    names = [all_tracks[i].get("name", "?") for i in range(len(track_ids))]
    csv_path = _dump_distance_csv(D, names, track_ids, PL_ID)
    if csv_path:
        print(f"\nCSV dumped to: {csv_path}")
    else:
        print("\nCSV dump FAILED")

    # ── Histogram ───────────────────────────────────────────────────────
    png_path = _render_distance_histogram(D, PL_ID)
    if png_path:
        print(f"Histogram saved to: {png_path}")

        # Describe shape
        off_diag = D[D != 0]
        import numpy as np
        hist, edges = np.histogram(off_diag, bins=12)
        peaks = list(np.where(hist >= 0.8 * np.max(hist))[0])
        if len(peaks) <= 1:
            shape = "single peak"
        elif len(peaks) == 2 and abs(peaks[0] - peaks[1]) == 1:
            shape = "single broad peak"
        else:
            shape = f"multi-modal ({len(peaks)} peaks)"
        print(f"Histogram shape: {shape}")
        print(f"Bins (edges): {[f'{e:.2f}' for e in edges[:6]]} ... {[f'{e:.2f}' for e in edges[-3:]]}")
        print(f"Counts:       {list(hist)}")
    else:
        print("Histogram render FAILED")

    return 0


if __name__ == "__main__":
    sys.exit(main())