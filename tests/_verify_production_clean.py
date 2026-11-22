"""Verify production data is unchanged after test suite run."""
import hashlib
import pathlib
import json

repo = pathlib.Path(__file__).parent.parent
db = repo / "database" / "tracks_db.sqlite"
emb = repo / "embeddings"
cache = repo / "cache"

with open(repo / "tests" / "_pre_suite_hash.json") as f:
    ref = json.load(f)

def hash_file(p):
    if p.exists():
        return hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    return "MISSING"

def list_dir_files(d):
    if not d.exists():
        return set()
    return set(sorted(p.name for p in d.iterdir() if p.is_file()))

db_now = hash_file(db)
emb_files_now = sorted(list_dir_files(emb))
cache_files_now = sorted(list_dir_files(cache))

ok = True

if db_now != ref["db_hash"]:
    print(f"FAIL: DB hash changed: {ref['db_hash']} -> {db_now}")
    ok = False
else:
    print(f"OK: DB hash unchanged ({db_now})")

if len(emb_files_now) != ref["emb_count"]:
    print(f"FAIL: Embeddings file count changed: {ref['emb_count']} -> {len(emb_files_now)}")
    ok = False
elif emb_files_now != ref["emb_files"]:
    print("FAIL: Embeddings file list changed")
    added = set(emb_files_now) - set(ref["emb_files"])
    removed = set(ref["emb_files"]) - set(emb_files_now)
    if added:
        print(f"  added: {added}")
    if removed:
        print(f"  removed: {removed}")
    ok = False
else:
    print(f"OK: Embeddings unchanged ({len(emb_files_now)} files)")

if len(cache_files_now) != ref["cache_count"]:
    print(f"NOTE: Cache file count changed: {ref['cache_count']} -> {len(cache_files_now)}")
elif cache_files_now != ref["cache_files"]:
    print("NOTE: Cache file list changed (cache is mutable, expected)")
    diff = set(cache_files_now) ^ set(ref["cache_files"])
    if diff:
        print(f"  diff: {diff}")
else:
    print(f"OK: Cache unchanged ({len(cache_files_now)} files)")

print(f"\n{'ALL CLEAN - Production data UNCHANGED' if ok else 'CONTAMINATION DETECTED'}")
if not ok:
    raise SystemExit(1)