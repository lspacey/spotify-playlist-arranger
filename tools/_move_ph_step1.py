"""Move playlist_highlight sub-step 1 items out of playlist_source.py."""
import re

path = "playlist_arranger/ui/pages/playlist_source.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Remove the function definition
old_func = '''def _sync_now_playing_row_highlight(plid: str, tid: str):
    """Sync the table.selected to highlight the row for track_id in playlist_id.
    Removes any previous now-playing highlight and adds the current one.
    Extracted from _notify_playing_track() and _update_np_ui() (DRY)."""
    if not plid or not tid:
        return
    table = _playlist_tables.get(plid)
    rows_cache = _playlist_rows_cache.get(plid, [])
    if table is None or not rows_cache:
        return
    tracks = _state.current_tracks
    track_index = next((i for i, t in enumerate(tracks) if t.get("id") == tid), None)
    if track_index is not None and track_index < len(rows_cache):
        match_row = rows_cache[track_index]
        prev_np_key = _now_playing_row_keys.get(plid)
        current = list(table.selected) if hasattr(table, 'selected') else []
        if prev_np_key is not None and prev_np_key != match_row["idx"]:
            current = [r for r in current if r.get("idx") != prev_np_key]
        if not any(r.get("idx") == match_row["idx"] for r in current):
            current.append(match_row)
            table.selected = current
            _now_playing_row_keys[plid] = match_row["idx"]'''

new_comment = '''
# _sync_now_playing_row_highlight() moved to playlist_arranger/ui/playlist_highlight.py.
# Imported as _ph.  All calls below use _ph._sync_now_playing_row_highlight(...).'''

if old_func in content:
    content = content.replace(old_func, new_comment)
    print("OK: removed function definition")
else:
    print("ERROR: function definition NOT FOUND")
    # Try to find it
    idx = content.find("def _sync_now_playing_row_highlight")
    print(f"  Found at index: {idx}")
    if idx >= 0:
        print(f"  Context: ...{content[idx-20:idx+60]}...")

# 2. Replace globals comment and definitions
old_globals = '''# Per-playlist table refs for native selection highlight from background thread
_playlist_tables = {}
_playlist_rows_cache = {}
_now_playing_row_keys = {}'''
new_globals = '''# _playlist_tables, _playlist_rows_cache, _now_playing_row_keys moved to
# playlist_arranger/ui/playlist_highlight.py.  Access via _ph._playlist_tables etc.'''
if old_globals in content:
    content = content.replace(old_globals, new_globals)
    print("OK: removed globals")
else:
    print("ERROR: globals NOT FOUND")

# 3. Replace bare references with _ph. prefix
replacements = [
    ("_playlist_tables.get(", "_ph._playlist_tables.get("),
    ("_playlist_rows_cache.get(", "_ph._playlist_rows_cache.get("),
    ("_now_playing_row_keys.get(", "_ph._now_playing_row_keys.get("),
    ("_now_playing_row_keys[", "_ph._now_playing_row_keys["),
    ("_playlist_tables[", "_ph._playlist_tables["),
    ("_playlist_rows_cache[", "_ph._playlist_rows_cache["),
    # Function calls
    ("_sync_now_playing_row_highlight(", "_ph._sync_now_playing_row_highlight("),
]

for old, new in replacements:
    if old in content:
        content = content.replace(old, new)
        print(f"OK: replaced bare '{old}' -> '{new}'")
    else:
        print(f"INFO: no bare '{old}' found (already prefixed)")

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print("\nDone.")