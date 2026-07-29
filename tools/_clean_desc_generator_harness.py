"""Delete the orphaned custom runner harness from test_desc_generator.py."""
import sys

TARGET = "tests/test_desc_generator.py"

with open(TARGET, "r", encoding="utf-8") as f:
    lines = f.readlines()

# Find the last "# ── Run all tests ──" line
for i in range(len(lines) - 1, -1, -1):
    if "# ── Run all tests ──" in lines[i]:
        print(f"Found harness marker at line {i+1}, deleting from here to EOF ({len(lines)} lines)")
        lines = lines[:i-1]  # Keep everything before the blank line before the marker
        break

# Remove trailing blank lines
while lines and lines[-1].strip() == "":
    lines.pop()

with open(TARGET, "w", encoding="utf-8") as f:
    f.writelines(lines)

print(f"New file size: {len(lines)} lines")