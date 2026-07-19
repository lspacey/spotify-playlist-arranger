"""Add dedup + locking to all analysis_queue mutation sites."""
path = "playlist_arranger/ui/pages/playlist_source.py"
with open(path, "r", encoding="utf-8") as f:
    lines = f.readlines()

# Each section below operates on the lines directly by matching markers

# ── 1. _add_selected_to_queue (line ~874): add dedup + lock ──
# Replace the append loop with a locked dedup version
for i, line in enumerate(lines):
    if "def _add_selected_to_queue(pl_id: str, tracks: list):" in line:
        func_start = i
        # Find the end of the existing loop
        for j in range(i, min(i+40, len(lines))):
            if "    _persist_queue()" in lines[j]:
                loop_end = j
                break
        # Build replacement body
        old_body = "".join(lines[func_start+6:loop_end])  # lines after docstring to before _persist_queue()
        new_body = """    selected_idxs = sorted(r["idx"] for r in selected_rows)
    added = 0
    skipped = 0
    for idx in selected_idxs:
        i = idx - 1
        if 0 <= i < len(tracks):
            track = tracks[i]
            with _state.analysis_queue_lock:
                tid = track.get("id", "")
                if not tid:
                    logger.warning("Dedup skipped — track has no id: %s", track.get("name", "?")[:40])
                    _state.analysis_queue.append(track)
                    added += 1
                elif any(t.get("id") == tid for t in _state.analysis_queue):
                    skipped += 1
                else:
                    _state.analysis_queue.append(track)
                    added += 1
            # Lock released
            if skipped and not added:
                pass  # notification handled below, outside the per-track loop
    """
        # Replace from func_start+6 to loop_end
        lines[func_start+6:loop_end] = [new_body]
        
        # Now update the notification line to mention skipped
        for k in range(func_start, min(func_start+30, len(lines))):
            if 'f"Added {added} track(s) to queue"' in lines[k]:
                # Keep original notification but add skipped notification if any were skipped
                lines[k] = lines[k].replace("f\"Added {added} track(s) to queue\"", "f\"Added {added} track(s) to queue\" + (f\" (skipped {skipped} duplicate(s))\" if skipped else \"\")")
                # Add extra notify for skipped tracks
                lines.insert(k+1, "    if skipped:\n")
                lines.insert(k+2, "        ui.notify(f\"{skipped} track(s) already in queue — skipped\", type=\"info\")\n")
                break
        break

# ── 2. _add_not_ok_to_queue (line ~1233): add dedup + lock ──
for i, line in enumerate(lines):
    if "def _add_not_ok_to_queue():" in line:
        # Find the append loop
        for j in range(i, min(i+30, len(lines))):
            if "before = len(_state.analysis_queue)" in lines[j]:
                before_line = j
            if "            _persist_queue()" in lines[j]:
                after_line = j
                break
        # Replace from before_line to after_line
        new_block = """            added = 0
            skipped = 0
            for t in not_ok:
                with _state.analysis_queue_lock:
                    tid = t.get("id", "")
                    if not tid:
                        logger.warning("Dedup skipped — track has no id: %s", t.get("name", "?")[:40])
                        _state.analysis_queue.append(t)
                        added += 1
                    elif any(q.get("id") == tid for q in _state.analysis_queue):
                        skipped += 1
                    else:
                        _state.analysis_queue.append(t)
                        added += 1
                # Lock released
            _persist_queue()
            _update_queue_label()
            _rebuild_queue_ui()
            if added:
                ui.notify(f"Added {added} track(s) to queue" + (f" (skipped {skipped} duplicate(s))" if skipped else ""), type="positive")
            elif skipped:
                ui.notify(f"All {skipped} track(s) already in queue — nothing to add", type="info")
            logger.info("Added %d not-OK track(s) to analysis queue (total=%d, skipped %d)", added, len(_state.analysis_queue), skipped)
"""
        lines[before_line:after_line+1] = [new_block]
        break

# ── 3. _on_remove_selected: add lock ──
for i, line in enumerate(lines):
    if "        def _on_remove_selected():" in line:
        for j in range(i, min(i+20, len(lines))):
            if "del _state.analysis_queue[i]" in lines[j]:
                # Wrap in lock
                lines[j] = "                    with _state.analysis_queue_lock:\n                        del _state.analysis_queue[i]\n"
                break
        break

# ── 4. _on_remove_all: add lock ──
for i, line in enumerate(lines):
    if "        def _on_remove_all():" in line:
        for j in range(i, min(i+10, len(lines))):
            if "_state.analysis_queue.clear()" in lines[j]:
                lines[j] = lines[j].replace(
                    "            _state.analysis_queue.clear()",
                    "            with _state.analysis_queue_lock:\n                _state.analysis_queue.clear()")
                break
        break

with open(path, "w", encoding="utf-8") as f:
    f.writelines(lines)

print("Done. Applied dedup + locking to all 4 mutation sites in playlist_source.py.")