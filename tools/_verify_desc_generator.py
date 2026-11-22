"""Verify test_desc_generator.py is clean after harness removal."""
import ast
import subprocess
import sys

repo = r"e:\Projects\Spotify_playlists\repository"
target = f"{repo}\\tests\\test_desc_generator.py"

# 1. AST parse check
try:
    with open(target, "r", encoding="utf-8") as f:
        source = f.read()
    ast.parse(source)
    print("AST parse: OK")
except SyntaxError as e:
    print(f"AST parse: FAILED — {e}")
    sys.exit(1)

# 2. Line count
lines = source.split("\n")
print(f"Line count: {len(lines)}")

# 3. Isolation test
print("\n=== Isolation test ===")
result = subprocess.run(
    [f"{repo}\\venv\\Scripts\\python.exe", "-m", "pytest", target, "-v", "--no-header", "-q"],
    capture_output=True, text=True, cwd=repo
)
for line in result.stdout.split("\n")[-5:]:
    print(line)
if "passed" in result.stdout:
    print(result.stdout.split("\n")[-2])

# 4. Full suite collection
print("\n=== Full suite collection ===")
result = subprocess.run(
    [f"{repo}\\venv\\Scripts\\python.exe", "-m", "pytest", f"{repo}\\tests", "--collect-only", "-q"],
    capture_output=True, text=True, cwd=repo
)
for line in result.stdout.split("\n")[-5:]:
    print(line)