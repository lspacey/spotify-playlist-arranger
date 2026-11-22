"""Move misfiled tests from test_desc_generator.py to correct files."""
import re

# ── Targets ──────────────────────────────────────────────────────────────────
BASE = r"e:\Projects\Spotify_playlists\repository"

# 1. Read test_desc_generator.py
with open(f"{BASE}\\tests\\test_desc_generator.py", "r", encoding="utf-8") as f:
    dg_content = f.read()

# 2. Extract rebuild_queue_ui tests (lines ~406-462)
rebuild_tests = re.search(
    r'(def test_rebuild_queue_ui_skips_when_client_disconnected.*?)(?=\n\n\ndef test_load_save_descriptions_removed)',
    dg_content, re.DOTALL
).group(1)

# 3. Extract distance tests — all test_d_* + test_freq_balance + test_duration + test_track_distance + test_settings_missing + test_todo_comment
distance_tests = []
pattern = r'(def test_(?:d_texture|d_freq_balance|d_duration|duration_mismatch|freq_balance_all_zero|settings_missing_new_weights|track_distance_includes|todo_comment).*?)(?=\n\n\ndef test_|\n\n\n# ──|$)'
for m in re.finditer(pattern, dg_content, re.DOTALL):
    distance_tests.append(m.group(1).strip())

print(f"Found {len(distance_tests)} distance-related test blocks")

# 4. Append to test_batch_analysis.py
with open(f"{BASE}\\tests\\test_batch_analysis.py", "r", encoding="utf-8") as f:
    ba_content = f.read()

# Remove trailing whitespace, add tests before EOF
ba_content = ba_content.rstrip() + "\n\n\n" + rebuild_tests.strip() + "\n"
with open(f"{BASE}\\tests\\test_batch_analysis.py", "w", encoding="utf-8") as f:
    f.write(ba_content)

# 5. Create test_distance.py
distance_header = '''\
"""Unit tests for playlist_arranger.sorting.distance module.

Consolidated from test_desc_generator.py during the custom-runner→pytest
migration (2026-07-29).
"""

import sys
sys.path.insert(0, r"e:\\Projects\\Spotify_playlists\\repository")

from playlist_arranger.sorting.distance import _track_distance, WEIGHTS
from playlist_arranger.config import Settings, load_settings
import playlist_arranger.config as _cfg


'''

distance_body = "\n\n\n".join(distance_tests) + "\n"
with open(f"{BASE}\\tests\\test_distance.py", "w", encoding="utf-8") as f:
    f.write(distance_header + distance_body)

# 6. Remove the moved tests from test_desc_generator.py
# Remove rebuild_queue_ui tests
dg_content = re.sub(
    r'\n\ndef test_rebuild_queue_ui_skips_when_client_disconnected.*?(?=\n\n\ndef test_load_save_descriptions_removed)',
    '', dg_content, flags=re.DOTALL
)
dg_content = re.sub(
    r'\n\ndef test_rebuild_queue_ui_proceeds_when_client_connected.*?(?=\n\n\ndef test_load_save_descriptions_removed)',
    '', dg_content, flags=re.DOTALL
)
# Remove distance tests
for pattern_name in [
    r'def test_d_texture_same_track_returns_zero',
    r'def test_d_texture_different_tracks_nonzero',
    r'def test_d_freq_balance_same_vector_zero',
    r'def test_d_freq_balance_orthogonal',
    r'def test_settings_missing_new_weights_falls_back',
    r'def test_freq_balance_all_zero_treated_as_neutral',
    r'def test_duration_mismatch_penalty_skipped_when_zero',
    r'def test_duration_mismatch_penalty_applied',
    r'def test_track_distance_includes_texture_and_freq_balance',
    r'def test_todo_comment_near_normalization_constants',
]:
    dg_content = re.sub(
        rf'\n\n\ndef {pattern_name}.*?(?=\n\n\ndef test_|\n\n\n# |\n\n\n\Z)',
        '', dg_content, flags=re.DOTALL
    )

with open(f"{BASE}\\tests\\test_desc_generator.py", "w", encoding="utf-8") as f:
    f.write(dg_content)

print("Done. Verifying...")