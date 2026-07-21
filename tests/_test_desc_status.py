"""Unit tests for playlist_arranger.analysis.desc_status."""

import sys
sys.path.insert(0, r"e:\Projects\Spotify_playlists\repository")

from datetime import datetime, timezone, timedelta
from playlist_arranger.analysis.desc_status import get_desc_age_info, DescAgeInfo

results = []


def _iso_days_ago(days: int) -> str:
    """Return an ISO 8601 timestamp exactly `days` days ago."""
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.isoformat()


# ── desc_text=None → has_desc=False ──────────────────────────────────────
def test_none_desc_text():
    info = get_desc_age_info(None, _iso_days_ago(5))
    assert not info.has_desc, f"Expected has_desc=False, got {info.has_desc}"
    assert info.age_days is None, f"Expected age_days=None, got {info.age_days}"
    assert info.color == "gray", f"Expected color='gray', got {info.color!r}"
    assert info.caption == "No description", f"Expected caption='No description', got {info.caption!r}"


def test_empty_desc_text():
    info = get_desc_age_info("", _iso_days_ago(5))
    assert not info.has_desc
    assert info.caption == "No description"


def test_whitespace_desc_text():
    info = get_desc_age_info("   ", _iso_days_ago(5))
    assert not info.has_desc
    assert info.caption == "No description"


# ── Valid desc_text + 10 days ago → green ──────────────────────────────
def test_10_days_green():
    info = get_desc_age_info("A cool track", _iso_days_ago(10))
    assert info.has_desc, "Expected has_desc=True"
    assert info.age_days == 10, f"Expected age_days=10, got {info.age_days}"
    assert info.color == "green", f"Expected color='green', got {info.color!r}"
    assert info.caption == "0mo 10d ago", f"Unexpected caption: {info.caption!r}"


# ── 100 days ago → yellow ──────────────────────────────────────────────
def test_100_days_yellow():
    info = get_desc_age_info("A cool track", _iso_days_ago(100))
    assert info.has_desc
    assert info.color == "yellow", f"Expected color='yellow', got {info.color!r}"
    # 100 = 3*30 + 10
    assert info.caption == "3mo 10d ago", f"Unexpected caption: {info.caption!r}"


# ── 200 days ago → red ─────────────────────────────────────────────────
def test_200_days_red():
    info = get_desc_age_info("A cool track", _iso_days_ago(200))
    assert info.has_desc
    assert info.color == "red", f"Expected color='red', got {info.color!r}"
    # 200 = 6*30 + 20
    assert info.caption == "6mo 20d ago", f"Unexpected caption: {info.caption!r}"


# ── Malformed desc_generated_at → has_desc=False (safety net) ────────
def test_malformed_timestamp():
    info = get_desc_age_info("Has desc text", "not-a-timestamp")
    assert not info.has_desc, "Malformed timestamp should treat as no desc"
    assert info.color == "gray"
    assert info.caption == "No description"


def test_none_timestamp():
    info = get_desc_age_info("Has desc text", None)
    assert not info.has_desc
    assert info.caption == "No description"


def test_empty_timestamp():
    info = get_desc_age_info("Has desc text", "")
    assert not info.has_desc


# ── Boundary: exactly 90 days → green (age < 90) ──────────────────────
def test_boundary_90_days_green():
    info = get_desc_age_info("Boundary test", _iso_days_ago(90))
    assert info.has_desc
    assert info.age_days == 90
    assert info.color == "yellow", f"Expected color='yellow' at exactly 90 days (90 <= age < 180), got {info.color!r}"


# ── Boundary: exactly 180 days → yellow (90 <= age < 180) ─────────────
def test_boundary_180_days_yellow():
    info = get_desc_age_info("Boundary test", _iso_days_ago(180))
    assert info.has_desc
    assert info.age_days == 180
    assert info.color == "red", f"Expected color='red' at exactly 180 days (age >= 180), got {info.color!r}"


# ── Boundary: 89 days → green ─────────────────────────────────────────
def test_89_days_green():
    info = get_desc_age_info("Almost yellow", _iso_days_ago(89))
    assert info.has_desc
    assert info.color == "green", f"Expected color='green' at 89 days, got {info.color!r}"


# ── Boundary: 179 days → yellow ───────────────────────────────────────
def test_179_days_yellow():
    info = get_desc_age_info("Almost red", _iso_days_ago(179))
    assert info.has_desc
    assert info.color == "yellow", f"Expected color='yellow' at 179 days, got {info.color!r}"


# ── Months/days caption breakdown ──────────────────────────────────────
def test_caption_95_days():
    info = get_desc_age_info("Test", _iso_days_ago(95))
    assert info.caption == "3mo 5d ago", f"Expected '3mo 5d ago', got {info.caption!r}"


def test_caption_1_day():
    info = get_desc_age_info("Test", _iso_days_ago(1))
    assert info.caption == "0mo 1d ago", f"Expected '0mo 1d ago', got {info.caption!r}"


def test_caption_30_days():
    info = get_desc_age_info("Test", _iso_days_ago(30))
    assert info.caption == "1mo 0d ago", f"Expected '1mo 0d ago', got {info.caption!r}"


def test_caption_365_days():
    info = get_desc_age_info("Test", _iso_days_ago(365))
    assert info.caption == "12mo 5d ago", f"Expected '12mo 5d ago', got {info.caption!r}"


# ── Run all tests ──────────────────────────────────────────────────────
tests = [
    ("test_none_desc_text", test_none_desc_text),
    ("test_empty_desc_text", test_empty_desc_text),
    ("test_whitespace_desc_text", test_whitespace_desc_text),
    ("test_10_days_green", test_10_days_green),
    ("test_100_days_yellow", test_100_days_yellow),
    ("test_200_days_red", test_200_days_red),
    ("test_malformed_timestamp", test_malformed_timestamp),
    ("test_none_timestamp", test_none_timestamp),
    ("test_empty_timestamp", test_empty_timestamp),
    ("test_boundary_90_days_yellow", test_boundary_90_days_green),
    ("test_boundary_180_days_red", test_boundary_180_days_yellow),
    ("test_89_days_green", test_89_days_green),
    ("test_179_days_yellow", test_179_days_yellow),
    ("test_caption_95_days", test_caption_95_days),
    ("test_caption_1_day", test_caption_1_day),
    ("test_caption_30_days", test_caption_30_days),
    ("test_caption_365_days", test_caption_365_days),
]

for name, fn in tests:
    try:
        fn()
        results.append(f"PASS: {name}")
    except Exception as e:
        results.append(f"FAIL: {name} - {e}")

for r in results:
    print(r)

passed = sum(1 for r in results if r.startswith("PASS"))
failed = sum(1 for r in results if r.startswith("FAIL"))
print(f"\n{passed}/{len(results)} passed, {failed} failed")

sys.exit(1 if failed > 0 else 0)