"""Move playlist_highlight sub-step 3: _notify_playing_track + _page_client + _last_notified_playlist_id."""
path = "playlist_arranger/ui/pages/playlist_source.py"
with open(path, "r", encoding="utf-8") as f:
    lines = f.readlines()

print(f"Initial line count: {len(lines)}")

# ── 1. Remove globals _page_client and _last_notified_playlist_id ──
# They currently sit around line 208-213
page_client_line = None
last_notified_line = None
for i, line in enumerate(lines):
    if line.strip().startswith("_page_client = None") and i < 15:
        continue  # skip the _page_client in playlist_highlight module section
    if line.strip().startswith("_page_client = None"):
        page_client_line = i
    if line.strip().startswith("_last_notified_playlist_id = None"):
        last_notified_line = i

print(f"_page_client = None at line {page_client_line+1 if page_client_line else 'NOT FOUND'}")
print(f"_last_notified_playlist_id = None at line {last_notified_line+1 if last_notified_line else 'NOT FOUND'}")

# Replace the block (includes the comments above)
if page_client_line and last_notified_line:
    # Find the comment lines above
    comment_start = page_client_line
    for i in range(page_client_line-1, max(0, page_client_line-5), -1):
        if "# Captured client context" in lines[i] or "# Last notified" in lines[i]:
            comment_start = min(comment_start, i)
    
    # Build replacement
    replacement = [
        "# _page_client and _last_notified_playlist_id moved to\n",
        "# playlist_arranger/ui/playlist_highlight.py.  Access via _ph._page_client etc.\n",
    ]
    
    # Find end of the block (after _last_notified_playlist_id line)
    block_end = last_notified_line + 1
    
    print(f"Removing lines {comment_start+1}-{block_end}")
    lines[comment_start:block_end] = replacement
    print("OK: removed _page_client and _last_notified_playlist_id globals")

# ── 2. Remove _notify_playing_track function ──
func_start = None
func_end = None
for i, line in enumerate(lines):
    if line.strip().startswith("def _notify_playing_track("):
        func_start = i
    if func_start is not None and i > func_start:
        if line.strip().startswith("# ─── Card logic:") or line.strip().startswith("def _card_listen_thread"):
            func_end = i
            break

if func_start and func_end:
    print(f"Removing function lines {func_start+1}-{func_end}")
    replacement = [
        "\n",
        "# _notify_playing_track() moved to playlist_arranger/ui/playlist_highlight.py.\n",
        "# Use _ph._notify_playing_track(...).\n",
        "\n",
    ]
    lines[func_start:func_end] = replacement
    print("OK: removed _notify_playing_track function")
else:
    print(f"ERROR: could not find function boundaries: start={func_start}, end={func_end}")

# ── 3. Update _ph.configure() call to include ui_context_lock ──
for i, line in enumerate(lines):
    if '_ph.configure(' in line:
        # Find the end of the configure call
        j = i
        while ')' not in lines[j] or j == i:
            j += 1
        # Update the call to add ui_context_lock
        # Current last param line is render_playlists_set_page_cb
        # Add ui_context_lock=_ui_context_lock before the closing paren
        lines.insert(j, "    ui_context_lock=_ui_context_lock,\n")
        print(f"OK: added ui_context_lock to _ph.configure() at line {i+1}")
        break
else:
    print("ERROR: _ph.configure() not found")

# ── 4. Update _page_client = ui.context.client to _ph._page_client ──
for i, line in enumerate(lines):
    if "_page_client = ui.context.client" in line and "_ph." not in line:
        lines[i] = line.replace("_page_client", "_ph._page_client")
        print(f"OK: updated _page_client assignment at line {i+1}")
        break

# ── 5. Update _on_analysis_complete: global _page_client → _ph._page_client ──
for i, line in enumerate(lines):
    if "global _page_client" in line and "_ph." not in line:
        # Check this isn't inside the notify function (already removed)
        # Just replace the global declaration
        lines[i] = line.replace("global _page_client", "# _page_client now accessed via _ph._page_client")
        print(f"OK: updated global _page_client at line {i+1}")
    if "_page_client is not None" in line and "_ph." not in line:
        lines[i] = lines[i].replace("_page_client", "_ph._page_client")
        print(f"OK: updated _page_client reference at line {i+1}")
    if "with _ui_context_lock, _page_client:" in line and "_ph." not in line:
        lines[i] = lines[i].replace("_page_client", "_ph._page_client")
        print(f"OK: updated _page_client in with-statement at line {i+1}")

# ── 6. Update _card_listen_thread: global _page_client, _notify_playing_track call ──
for i, line in enumerate(lines):
    if "global _page_client" in line and i > 250:  # in _card_listen_thread
        lines[i] = line.replace("global _page_client", "# _page_client now in _ph module")
        print(f"OK: updated global _page_client in _card_listen_thread at line {i+1}")
    if "_notify_playing_track(" in line and "_ph." not in line and "logger.exception" not in line:
        lines[i] = lines[i].replace("_notify_playing_track(", "_ph._notify_playing_track(")
        print(f"OK: updated _notify_playing_track call at line {i+1}")

# ── 7. Replace remaining bare _page_client references (in _update_np_ui etc.) ──
# We already handled the main ones. But there might be a global in _drain_pending_ui_item's _page_client access
# Let's just do a blanket replace for any remaining bare _page_client
for i, line in enumerate(lines):
    if "global _page_client" in line and "_ph." not in line and "#" not in line[:10]:
        lines[i] = line.replace("global _page_client", "# _page_client moved to _ph module")
        print(f"OK: updated remaining global _page_client at line {i+1}")

with open(path, "w", encoding="utf-8") as f:
    f.writelines(lines)

print(f"\nFinal line count: {len(lines)}")
print("Done.")