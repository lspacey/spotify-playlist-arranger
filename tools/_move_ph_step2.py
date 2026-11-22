"""Move playlist_highlight sub-step 2 items out of playlist_source.py."""
path = "playlist_arranger/ui/pages/playlist_source.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Remove the two globals
old_globals = """# Playlist expansion references for auto-expand (keyed by playlist_id)
_playlist_expansions = {}

# Set of playlist IDs that were auto-expanded (not manually by user)
_auto_expanded_playlist_ids = set()"""

new_globals = """# _playlist_expansions and _auto_expanded_playlist_ids moved to
# playlist_arranger/ui/playlist_highlight.py.  Access via _ph._playlist_expansions etc."""

if old_globals in content:
    content = content.replace(old_globals, new_globals)
    print("OK: removed globals")
else:
    print("ERROR: globals NOT FOUND")
    # Try loose search
    idx = content.find("_playlist_expansions = {}")
    print(f"  Found _playlist_expansions at: {idx}")
    if idx >= 0:
        print(f"  Context: ...{content[idx-60:idx+60]}...")

# 2. Remove _auto_expand_playlist function
old_auto_expand = """
def _auto_expand_playlist(playlist_id: str, track_id: str):
    \"\"\"Auto-expand a collapsed playlist, loading its tracks and rendering table.\"\"\"
    entry = _playlist_expansions.get(playlist_id)
    if entry is None:
        return
    exp, content_col = entry
    if not exp.value:
        try:
            tracks = _load_cached_playlist_tracks(playlist_id)
            _state.current_playlist_id = playlist_id
            _state.current_playlist_name = ""
            _state.current_playlist_source = "spotify"
            _state.current_tracks[:] = tracks
            content_col.clear()
            with content_col:
                _show_track_compact_table(tracks, playlist_id, _state.current_playlist_name, _render_playlists_set_page_cb)
            exp.value = True
            _auto_expanded_playlist_ids.add(playlist_id)
        except Exception:
            logger.exception("Auto-expand failed")


def _collapse_playlist(playlist_id: str):
    \"\"\"Collapse a previously auto-expanded playlist and remove highlight.\"\"\"
    if playlist_id not in _auto_expanded_playlist_ids:
        return
    entry = _playlist_expansions.get(playlist_id)
    if entry is None:
        return
    exp, _content_col = entry
    exp.value = False
    _auto_expanded_playlist_ids.discard(playlist_id)
    table = _playlist_tables.get(playlist_id)
    if table is not None:
        table.selected = []
    if playlist_id:
        js = (
            \"(function(){\"
            \"var el=document.querySelector('[data-pl-id=\\\\\"\" + playlist_id + \"\\\\\"]');\"
            \"if(el){el.classList.remove('pa-playlist-highlight');el.style.backgroundColor='';}\"
            \"console.log('[collapse] cleared');\"
            \"})()\"
        )
        ui.run_javascript(js)
    logger.debug(\"Collapsed playlist %s\", playlist_id[:8] if playlist_id else \"?\")"""

new_func_comment = """

# _auto_expand_playlist() and _collapse_playlist() moved to
# playlist_arranger/ui/playlist_highlight.py.  Use _ph._auto_expand_playlist() etc."""

if old_auto_expand in content:
    content = content.replace(old_auto_expand, new_func_comment)
    print("OK: removed both functions")
else:
    print("ERROR: functions NOT FOUND")
    # Try to find them
    idx = content.find("def _auto_expand_playlist")
    print(f"  Found def at: {idx}")
    if idx >= 0:
        print(f"  Context: ...{content[idx:idx+80]}...")

# 3. Add configure() call right after the import
# Find the import line and add configure call after it
marker = "from playlist_arranger.ui import playlist_highlight as _ph"
config_call = """
# ---- Wire playlist_highlight's dependency injection (circular-import avoidance) ----
_ph.configure(
    load_cached_playlist_tracks=_load_cached_playlist_tracks,
    show_track_compact_table=_show_track_compact_table,
    render_playlists_set_page_cb=_render_playlists_set_page_cb,
)"""

if marker in content:
    if config_call not in content:
        content = content.replace(marker, marker + config_call)
        print("OK: added _ph.configure() call")
    else:
        print("INFO: _ph.configure() call already present")
else:
    print("ERROR: marker not found for configure() placement")

# 4. Replace bare references with _ph. prefix
replacements = [
    ("_playlist_expansions.get(", "_ph._playlist_expansions.get("),
    ("_playlist_expansions[", "_ph._playlist_expansions["),
    ("_auto_expanded_playlist_ids.add(", "_ph._auto_expanded_playlist_ids.add("),
    ("_auto_expanded_playlist_ids.discard(", "_ph._auto_expanded_playlist_ids.discard("),
    ("_auto_expanded_playlist_ids:", "_ph._auto_expanded_playlist_ids:"),
    ("_collapse_playlist(", "_ph._collapse_playlist("),
    ("_auto_expand_playlist(", "_ph._auto_expand_playlist("),
    ("_auto_expanded_playlist_ids ", "_ph._auto_expanded_playlist_ids "),
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