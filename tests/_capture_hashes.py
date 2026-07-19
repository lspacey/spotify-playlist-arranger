"""Capture pre-suite hashes for production data verification."""
import hashlib
import pathlib
import json

repo = pathlib.Path(__file__).parent.parent
db = repo / "database" / "tracks_db.sqlite"
emb = repo / "embeddings"
cache = repo / "cache"


def hash_file(p):
    if p.exists():
        return hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    return "MISSING"


def list_dir_files(d):
    if not d.exists():
        return set()
    return set(sorted(p.name for p in d.iterdir() if p.is_file()))


ref = {
    "db_hash": hash_file(db),
    "emb_count": len(list_dir_files(emb)),
    "emb_files": sorted(list_dir_files(emb)),
    "cache_count": len(list_dir_files(cache)),
    "cache_files": sorted(list_dir_files(cache)),
}
with open(repo / "tests" / "_pre_suite_hash.json", "w") as f:
    json.dump(ref, f)
print("Captured:", ref["db_hash"], ref["emb_count"], ref["cache_count"])