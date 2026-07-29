"""One-shot: compute d_transition across multiple cached playlists to determine
whether MFCC distances need per-playlist calibration or a fixed corpus-wide divisor.

NOT part of the test suite — not auto-discovered by pytest.
"""
import glob, json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent.resolve()))
import numpy as np
from playlist_arranger.sorting.distance import _cos_dist
from playlist_arranger.database import db as _db

def transition_stats(pl_id, label):
    pattern = f'cache/{pl_id}-*.tracks.json'
    matches = glob.glob(pattern)
    if not matches:
        print(f'{label}: no cache file')
        return
    tracks = json.loads(open(matches[0], 'r', encoding='utf-8').read())
    db_dict = _db.load_all()
    all_tracks = [db_dict[t['id']] for t in tracks if t['id'] in db_dict]
    n = len(all_tracks)
    if n < 2:
        print(f'{label}: only {n} tracks in DB')
        return
    vals = []
    for i in range(n):
        for j in range(n):
            if i == j: continue
            ta = all_tracks[i]; tb = all_tracks[j]
            fa = ta.get('features') or {}; fb = tb.get('features') or {}
            end_a = ta.get('end_seg', fa); start_b = tb.get('start_seg', fb)
            mfcc_a = end_a.get('mfcc20') or end_a.get('mfcc13') or fa.get('mfcc20') or fa.get('mfcc13')
            mfcc_b = start_b.get('mfcc20') or start_b.get('mfcc13') or fb.get('mfcc20') or fb.get('mfcc13')
            if mfcc_a and mfcc_b:
                va = np.array(mfcc_a, dtype=np.float32); vb = np.array(mfcc_b, dtype=np.float32)
                nc = min(len(va), len(vb))
                raw = _cos_dist(va[:nc], vb[:nc])
                vals.append(min(raw / 2.0, 1.0))
    if not vals:
        print(f'{label}: no MFCC data')
        return
    arr = np.array(vals)
    print(f'{label} ({n}t, {len(vals)} pairs): max={np.max(arr):.4f} mean={np.mean(arr):.4f} '
          f'std={np.std(arr):.4f} min={np.min(arr):.4f}')

print('=== Cross-Playlist d_transition (raw /2.0 clamped) ===')
for label, pl_id in [
    ('61t ambient', '5ny37mvOmLQ7kwjV19B3Dc'),
]:
    transition_stats(pl_id, label)

# Also check raw cos_dist range (before /2.0) on the 61t playlist
print()
print('=== Raw MFCC cos_dist (before /2.0) for 61t ambient ===')
pattern = 'cache/5ny37mvOmLQ7kwjV19B3Dc-*.tracks.json'
matches = glob.glob(pattern)
if matches:
    tracks = json.loads(open(matches[0], 'r', encoding='utf-8').read())
    db_dict = _db.load_all()
    all_tracks = [db_dict[t['id']] for t in tracks if t['id'] in db_dict]
    raw_vals = []
    n = len(all_tracks)
    for i in range(n):
        for j in range(n):
            if i == j: continue
            ta = all_tracks[i]; tb = all_tracks[j]
            fa = ta.get('features') or {}; fb = tb.get('features') or {}
            end_a = ta.get('end_seg', fa); start_b = tb.get('start_seg', fb)
            mfcc_a = end_a.get('mfcc20') or end_a.get('mfcc13') or fa.get('mfcc20') or fa.get('mfcc13')
            mfcc_b = start_b.get('mfcc20') or start_b.get('mfcc13') or fb.get('mfcc20') or fb.get('mfcc13')
            if mfcc_a and mfcc_b:
                va = np.array(mfcc_a, dtype=np.float32); vb = np.array(mfcc_b, dtype=np.float32)
                nc = min(len(va), len(vb))
                raw_vals.append(_cos_dist(va[:nc], vb[:nc]))
    arr = np.array(raw_vals)
    print(f'  Raw cos_dist: min={np.min(arr):.4f} max={np.max(arr):.4f} mean={np.mean(arr):.4f}')
    print(f'  With /2.0:     min={np.min(arr/2):.4f} max={np.max(arr/2):.4f} mean={np.mean(arr/2):.4f}')
    print(f'  With /1.0:     min={np.min(arr):.4f} max={np.max(arr):.4f} mean={np.mean(arr):.4f}')