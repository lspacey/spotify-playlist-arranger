"""Remove duplicate distance tests from test_desc_generator.py."""
TARGET = r"e:\Projects\Spotify_playlists\repository\tests\test_desc_generator.py"

with open(TARGET, "r", encoding="utf-8") as f:
    content = f.read()

# Remove the 10 duplicated distance test blocks (now in test_distance.py)
blocks_to_remove = [
    "test_todo_comment_near_normalization_constants",
    "test_d_texture_same_track_returns_zero",
    "test_d_texture_different_tracks_nonzero",
    "test_d_freq_balance_same_vector_zero",
    "test_d_freq_balance_orthogonal",
    "test_settings_missing_new_weights_falls_back",
    "test_freq_balance_all_zero_treated_as_neutral",
    "test_duration_mismatch_penalty_skipped_when_zero",
    "test_duration_mismatch_penalty_applied",
    "test_track_distance_includes_texture_and_freq_balance",
]

import re
for name in blocks_to_remove:
    pattern = rf'\n\ndef {name}.*?(?=\n\ndef test_|\n\n# ── |$)'
    content = re.sub(pattern, '', content, flags=re.DOTALL)

# Also clean up the now-empty "Distance component tests" section comment and "sync_weights" comment
content = content.replace('\n\n# ── Distance component tests ───────────────────────────────────────────────────\n\n\n# ── sync_weights_from_settings tests ────────────────────────────────────────', '\n\n# ── sync_weights_from_settings tests ────────────────────────────────────────')
content = content.replace('\n\n\n\n# ── Solver tests', '\n\n\n# ── Solver tests')

with open(TARGET, "w", encoding="utf-8") as f:
    f.write(content)

print("Done. Running verification...")
import subprocess
result = subprocess.run(
    [r"e:\Projects\Spotify_playlists\repository\venv\Scripts\python.exe", "-m", "pytest", TARGET, "--collect-only", "-q"],
    capture_output=True, text=True
)
for line in result.stdout.split('\n')[-5:]:
    print(line)