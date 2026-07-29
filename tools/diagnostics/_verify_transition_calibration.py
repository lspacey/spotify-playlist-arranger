"""One-shot: verify transition effective contribution post-calibration.

NOT part of the test suite.
"""
import json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent.resolve()))
import numpy as np
from playlist_arranger.sorting.stats_analysis import analyze_playlist_stats
from playlist_arranger.database import db as _db
from playlist_arranger.sorting.distance import _load_embedding

pl_id = "5ny37mvOmLQ7kwjV19B3Dc"
cache_file = pathlib.Path(
    "cache/5ny37mvOmLQ7kwjV19B3Dc-AAAAztHygx9kuqte7KbYx7vQf1dkwhSb.tracks.json")
tracks = json.loads(cache_file.read_text(encoding="utf-8"))

db_dict = _db.load_all()
track_ids = [t["id"] for t in tracks]
all_tracks = [db_dict[tid] for tid in track_ids if tid in db_dict]
embeddings = [_load_embedding(tid, db_dict) for tid in track_ids]

print(f"Running stats analysis on {len(all_tracks)} tracks (no embeddings)...")
sr = analyze_playlist_stats(pl_id, "diag", all_tracks, embeddings)

# Show per-component calibration & effective contribution
weights = {"mood": 0.48, "bpm": 0.12, "transition": 0.20, "key": 0.12,
           "energy": 0.08, "texture": 0.10, "freq_balance": 0.08}
comp_order = ["mood", "bpm", "transition", "key", "energy", "texture", "freq_balance"]

print(f"\n{'Component':>14s}  {'Calib':>8s}  {'ObsMax':>8s}  {'Weight':>7s}  "
      f"{'EffContrib':>10s}  {'%':>6s}  {'CV':>6s}")
print("-" * 75)

total = 0.0
items = []
for comp_name in comp_order:
    c = sr.components.get(comp_name)
    if c is None:
        continue
    w = weights[comp_name]
    obs_max = c.observed_max
    eff = obs_max * w
    items.append((comp_name, c.calibration_scale, obs_max, w, eff, c.observed_cv))
    total += eff

for comp_name, calib, obs_max, w, eff, cv in items:
    pct = eff / total * 100 if total > 0 else 0
    print(f"{comp_name:>14s}  {calib:8.4f}  {obs_max:8.4f}  {w:7.3f}  "
          f"{eff:10.4f}  {pct:5.1f}%  {cv:5.2f}")

print(f"\nTotal effective sum: {total:.4f}")
print(f"\nTransition calibration_scale: {sr.components['transition'].calibration_scale:.4f}")
print(f"Transition observed_max: {sr.components['transition'].observed_max:.4f}")
print(f"Transition effective contribution: {items[2][4]:.4f} ({items[2][4]/total*100:.1f}%)")
print(f"  (was 0.0246 / 4.1% before calibration)")