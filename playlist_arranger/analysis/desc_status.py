"""Description age status — pure functions (no UI, no I/O)."""

from datetime import datetime, timezone
from dataclasses import dataclass


@dataclass
class DescAgeInfo:
    has_desc: bool
    age_days: int | None       # None if no description
    color: str                 # "green" | "yellow" | "red" | "gray" (gray = no desc)
    caption: str                # e.g. "2mo 14d ago" or "No description"


def get_desc_age_info(desc_text: str | None, desc_generated_at: str | None) -> DescAgeInfo:
    """
    - has_desc=False if desc_text is None/empty
    - color thresholds: age < 90 days -> green, 90-180 days -> yellow, 
      > 180 days -> red (orange-red)
    - caption format: "{months}mo {days}d ago" (e.g. "3mo 5d ago"), 
      or "No description" if has_desc is False
    - Handle desc_generated_at being None/malformed gracefully -> 
      treat as has_desc=False (data inconsistency safety net)
    - Boundary: exactly 90 days → green (threshold is exclusive: age < 90)
                exactly 180 days → yellow (threshold is exclusive: 90 <= age < 180)
    """
    # Safety net: desc_text must be a non-empty string
    if not isinstance(desc_text, str) or not desc_text.strip():
        return DescAgeInfo(has_desc=False, age_days=None, color="gray", caption="No description")

    # Safety net: desc_generated_at must be a valid ISO 8601 timestamp
    if not isinstance(desc_generated_at, str) or not desc_generated_at.strip():
        return DescAgeInfo(has_desc=False, age_days=None, color="gray", caption="No description")

    try:
        # Parse ISO 8601 timestamp (e.g. "2026-04-12T10:30:00")
        generated_dt = datetime.fromisoformat(desc_generated_at)
    except (ValueError, TypeError):
        return DescAgeInfo(has_desc=False, age_days=None, color="gray", caption="No description")

    # Ensure the parsed datetime is timezone-aware for proper comparison.
    # If no tzinfo, treat as UTC (ISO 8601 without offset = local, but we
    # store and compare consistently in UTC).
    if generated_dt.tzinfo is None:
        generated_dt = generated_dt.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    delta = now - generated_dt
    age_days = delta.days

    # Color thresholds (exclusive upper bound for each tier):
    #   age < 90  → green
    #   90 <= age < 180 → yellow
    #   age >= 180 → red
    if age_days < 90:
        color = "green"
    elif age_days < 180:
        color = "yellow"
    else:
        color = "red"

    # Caption: months/days breakdown (~30 days/month)
    months = age_days // 30
    days = age_days % 30
    caption = f"{months}mo {days}d ago"

    return DescAgeInfo(has_desc=True, age_days=age_days, color=color, caption=caption)