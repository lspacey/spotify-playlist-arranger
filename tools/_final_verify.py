"""Final verification: desc_generator count, arithmetic, anchors_page status."""
import os, subprocess, sys

repo = r"e:\Projects\Spotify_playlists\repository"

# 1. Count desc_generator tests
dg_path = os.path.join(repo, "tests", "test_desc_generator.py")
with open(dg_path, "r", encoding="utf-8") as f:
    count = sum(1 for l in f if l.strip().startswith("def test_"))
print(f"test_desc_generator.py: {count} test functions")

# 2. Arithmetic
result = 39 + 57 + 6 + 17 - 3 + 6 + count + 10
print(f"Arithmetic: 39+57+6+17-3+6+{count}+10 = {result}")

# 3. Pytest collection
r = subprocess.run(
    [os.path.join(repo, "venv", "Scripts", "python.exe"), "-m", "pytest", os.path.join(repo, "tests"), "--collect-only", "-q"],
    capture_output=True, text=True, cwd=repo
)
for line in r.stdout.split("\n"):
    if "tests collected" in line:
        print(f"pytest says: {line.strip()}")

# 4. Anchors page status
anchors_legacy = os.path.join(repo, "tests", "_test_anchors_page.py")
anchors_new = os.path.join(repo, "tests", "test_anchors_page.py")
if os.path.exists(anchors_legacy):
    print(f"\n_test_anchors_page.py EXISTS — NOT converted, 85 tests HIDDEN from pytest")
    print(f"Files remaining to convert: 1 (_test_anchors_page.py)")
elif os.path.exists(anchors_new):
    print(f"\ntest_anchors_page.py EXISTS — already converted")
else:
    print(f"\nNeither _test_anchors_page.py nor test_anchors_page.py exists — unknown status")