"""Step 2 migration script — replaces inline queue functions in playlist_source.py
with delegation wrappers pointing to analysis_queue module globals."""
import sys

TARGET = "playlist_arranger/ui/pages/playlist_source.py"

with open(TARGET, "r", encoding="utf-8") as f:
    lines = f.readlines()

# ── Step 1: Replace function bodies with delegation wrappers ────────────────

# _persist_queue: line 1139-1140 (0-indexed: 1138-1139)
lines[1138] = "def _persist_queue():\n"
lines[1139] = "    _aq.persist_queue()\n"

# _rebuild_queue_ui: lines 1143-1174 (0-indexed: 1142-1173)
# Replace body keeping signature line intact
lines[1142] = "def _rebuild_queue_ui():\n"
lines[1143] = "    global _queue_table_ref, _queue_rows_cache, _queue_container\n"
lines[1144] = "    _aq.rebuild_queue_ui()\n"
lines[1145] = "    _queue_table_ref = _aq._queue_table_ref\n"
lines[1146] = "    _queue_rows_cache = _aq._queue_rows_cache\n"
lines[1147] = "    _refresh_batch_btn_enabled()\n"
# Delete remaining old lines (1148-1173)
for i in range(1148, 1174):
    lines[i] = ""

# _add_selected_to_queue: lines 1178-1212 (0-indexed: 1177-1211)
lines[1177] = "def _add_selected_to_queue(pl_id: str, tracks: list):\n"
lines[1178] = "    _aq.add_selected_to_queue(pl_id, tracks)\n"
for i in range(1179, 1212):
    lines[i] = ""

# _update_queue_label: lines 1214-1221 (0-indexed: 1213-1220)
lines[1213] = "def _update_queue_label():\n"
lines[1214] = "    _aq.update_queue_label()\n"
for i in range(1215, 1221):
    lines[i] = ""

# _render_queue_table: lines 1224-1291 (0-indexed: 1223-1290)
lines[1223] = "def _render_queue_table():\n"
lines[1224] = "    global _queue_table_ref, _queue_rows_cache\n"
lines[1225] = "    _aq.render_queue_table()\n"
lines[1226] = "    _queue_table_ref = _aq._queue_table_ref\n"
lines[1227] = "    _queue_rows_cache = _aq._queue_rows_cache\n"
for i in range(1228, 1291):
    lines[i] = ""

# _render_analysis_queue: lines 1505-1515 (0-indexed: 1504-1514)
lines[1504] = "def _render_analysis_queue():\n"
lines[1505] = "    global _queue_expansion_ref, _queue_label_ref, _queue_container\n"
lines[1506] = "    _aq.render_analysis_queue()\n"
lines[1507] = "    _queue_expansion_ref = _aq._queue_expansion_ref\n"
lines[1508] = "    _queue_label_ref = _aq._queue_label_ref\n"
lines[1509] = "    _queue_container = _aq._queue_container\n"
for i in range(1510, 1515):
    lines[i] = ""

# _get_selected_rows: lines 1569-1576 (0-indexed: 1568-1575)
lines[1568] = "def _get_selected_rows(pl_id: str):\n"
lines[1569] = "    return _aq._get_selected_rows(pl_id)\n"
for i in range(1570, 1576):
    lines[i] = ""

# ── Step 2: Update read sites outside the 8 functions ──────────────────────

def replace_in_range(start_0, end_0, replacements):
    """Apply (old, new) tuples in lines[start_0:end_0]."""
    for i in range(start_0, end_0):
        for old, new in replacements:
            if old in lines[i]:
                lines[i] = lines[i].replace(old, new)

# B1. _stop_analyzing: _queue_now_playing_row_keys.clear() → _aq._queue_now_playing_row_keys.clear()
replace_in_range(320, 338, [
    ("_queue_now_playing_row_keys.clear()", "_aq._queue_now_playing_row_keys.clear()"),
    ("if _queue_table_ref is not None", "if _aq._queue_table_ref is not None"),
    ("_queue_table_ref.selected = []", "_aq._queue_table_ref.selected = []"),
])

# B2. _card_listen_thread: three globals passed to _ph._sync_row_highlight
replace_in_range(460, 480, [
    ("_queue_table_ref,", "_aq._queue_table_ref,"),
    ("_queue_rows_cache,", "_aq._queue_rows_cache,"),
    ("_queue_now_playing_row_keys,", "_aq._queue_now_playing_row_keys,"),
])

# B3. _update_np_ui drain loop: lines 658-675
replace_in_range(650, 680, [
    ("global _queue_table_ref, _queue_rows_cache", "# queue globals now in _aq module"),
    ("if _queue_table_ref is not None and _queue_rows_cache:", "if _aq._queue_table_ref is not None and _aq._queue_rows_cache:"),
    ("if t.get(\"id\") == ctid and i < len(_queue_rows_cache):", "if t.get(\"id\") == ctid and i < len(_aq._queue_rows_cache):"),
    ("target_row = _queue_rows_cache[i]", "target_row = _aq._queue_rows_cache[i]"),
    ("if target_row is None and _queue_rows_cache:", "if target_row is None and _aq._queue_rows_cache:"),
    ("target_row = _queue_rows_cache[0]", "target_row = _aq._queue_rows_cache[0]"),
    ("_queue_table_ref.selected = [target_row]", "_aq._queue_table_ref.selected = [target_row]"),
])

# B4. _update_np_ui highlight: lines 709-717
replace_in_range(705, 720, [
    ("_queue_table_ref,", "_aq._queue_table_ref,"),
    ("_queue_rows_cache,", "_aq._queue_rows_cache,"),
    ("_queue_now_playing_row_keys,", "_aq._queue_now_playing_row_keys,"),
])

# B5. _render_queue_controls: lines 1418-1482 (various _queue_table_ref)
replace_in_range(1412, 1490, [
    ("global _queue_table_ref", "# _queue_table_ref now accessed via _aq module"),
    ("if _queue_table_ref is None:", "if _aq._queue_table_ref is None:"),
    ("selected = list(_queue_table_ref.selected) if hasattr(_queue_table_ref, 'selected') else []",
     "selected = list(_aq._queue_table_ref.selected) if hasattr(_aq._queue_table_ref, 'selected') else []"),
    ("if _queue_table_ref is not None:", "if _aq._queue_table_ref is not None:"),
    ("selected = list(_queue_table_ref.selected) if hasattr(_queue_table_ref, 'selected') else []",
     "selected = list(_aq._queue_table_ref.selected) if hasattr(_aq._queue_table_ref, 'selected') else []"),
])

# ── Step 3: Add _aq.configure() wiring ─────────────────────────────────────
# Find the wiring section
for i, line in enumerate(lines):
    if "# ---- Wire extracted modules (playlist_cache, desc_status, local_section) ----" in line:
        lines[i] = (
            "# ---- Wire analysis_queue dependency injection ----\n"
            "_aq.configure(\n"
            "    state_module=_state,\n"
            "    db_module=_db,\n"
            "    desc_gen_module=_desc_gen,\n"
            "    ph_module=_ph,\n"
            "    get_desc_age_info_fn=get_desc_age_info,\n"
            "    get_track_status_fn=_get_track_status,\n"
            "    play_track_fn=_play_track,\n"
            "    desc_icon_click_fn=_on_desc_icon_click,\n"
            "    render_queue_controls_fn=_render_queue_controls,\n"
            ")\n"
            "\n"
            "# ---- Wire extracted modules (playlist_cache, desc_status, local_section) ----\n"
        )
        break

# ── Step 4: Write back (skip empty lines from deleted functions) ────────────
with open(TARGET, "w", encoding="utf-8") as f:
    for line in lines:
        if line is not None and line != "":
            f.write(line)

print("Migration complete. Verifying...")