"""Count test functions in all python test files."""
import os

repo = r"e:\Projects\Spotify_playlists\repository"
tests_dir = os.path.join(repo, "tests")

counts = {}
for fname in sorted(os.listdir(tests_dir)):
    if fname.startswith("test_") and fname.endswith(".py"):
        path = os.path.join(tests_dir, fname)
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        count = sum(1 for l in lines if l.strip().startswith("def test_"))
        counts[fname] = count
        print(f"  {fname}: {count}")

total = sum(counts.values())
print(f"\n  Total: {total}")

# Verify against pytest collection
import subprocess
result = subprocess.run(
    [os.path.join(repo, "venv", "Scripts", "python.exe"), "-m", "pytest", tests_dir, "--collect-only", "-q"],
    capture_output=True, text=True, cwd=repo
)
last = [l for l in result.stdout.split('\n') if 'tests collected' in l]
if last:
    print(f"  pytest --collect-only reports: {last[0].strip()}")

# Cross-check arithmetic (correct for 57 batch tests)
batch = counts.get('test_batch_analysis.py', 55)
dg = counts.get('test_desc_generator.py', 78)
dist = counts.get('test_distance.py', 10)
arith = 39 + batch + 6 + 17 - 3 + 6 + dg + dist
print(f"\n  Arithmetic check:")
print(f"    39 (native) + {batch} (batch) + 6 (buffer) + 17 (desc_status)")
print(f"    - 3 (deleted key_bug) + 6 (analyze_regressions)")
print(f"    + {dg} (desc_generator) + {dist} (distance)")
print(f"    = 39+{batch}+6+17-3+6+{dg}+{dist} = {arith}")
print(f"    {'✓ MATCHES' if arith == total else f'✗ MISMATCH (diff={total - arith})'}")