"""Per-table pagination state — persists user's runtime page/rowsPerPage override.

Each ``ui.table`` instance (playlist track tables keyed by playlist_id,
queue table keyed by ``"__queue__"``) stores its live pagination state here.
On table rebuild, the saved state is restored instead of resetting to the
Settings.json default.
"""

import logging

logger = logging.getLogger(__name__)

# Valid page size values (must match the options in settings_panel.py)
_VALID_PAGE_SIZES = frozenset({5, 10, 15, 20, 25, 50, 0})

# Per-table pagination state: key → {"rowsPerPage": int, "page": int}
# Keys: playlist_id for playlist tables, "__queue__" for queue table.
_pagination_cache: dict[str, dict] = {}


def _read_default_page_size() -> int:
    """Read the default page size from Settings.json (15 if not configured)."""
    try:
        from playlist_arranger.ui.state import get_settings
        s = get_settings()
        val = getattr(s, "default_page_size", 15)
        if isinstance(val, int) and val in _VALID_PAGE_SIZES:
            return val
    except Exception:
        logger.debug("Could not read default_page_size from settings", exc_info=True)
    return 15


def get_default_pagination() -> dict:
    """Return the default pagination dict: {rowsPerPage: N, page: 1}.

    ``rowsPerPage`` is read from Settings.json (default page size).
    If the value is 0 ("Show All"), the returned pagination is {rowsPerPage: 0}.
    """
    rpp = _read_default_page_size()
    return {"rowsPerPage": rpp, "page": 1}


def get_saved_pagination(table_key: str) -> dict | None:
    """Return the saved pagination state for *table_key*, or None if unsaved.

    Table keys:
      - ``"__queue__"`` for the analysis queue table
      - playlist ID for playlist track tables
    """
    return _pagination_cache.get(table_key)


def save_pagination(table_key: str, pagination: dict) -> None:
    """Save pagination state for *table_key* from a live table's .pagination dict.

    Expected dict shape: {rowsPerPage: int, page: int, rowsNumber: int}
    Only ``rowsPerPage`` and ``page`` are saved; ``rowsNumber`` is ignored.
    """
    if not isinstance(pagination, dict):
        return
    rpp = pagination.get("rowsPerPage", 0)
    page = pagination.get("page", 1)
    _pagination_cache[table_key] = {"rowsPerPage": int(rpp), "page": int(page)}


def apply_pagination(table, table_key: str) -> None:
    """Restore saved pagination state to *table*, falling back to Settings.json default.

    Must be called AFTER the table is constructed but BEFORE any user interaction.
    If no user override exists for *table_key*, uses the Settings.json default.
    """
    saved = get_saved_pagination(table_key)
    if saved is not None:
        rpp = saved["rowsPerPage"]
        page = saved["page"]
    else:
        default = get_default_pagination()
        rpp = default["rowsPerPage"]
        page = default["page"]

    try:
        table._props["pagination"] = {"rowsPerPage": rpp, "page": page}
        table.update()
    except Exception:
        logger.debug("Failed to apply pagination to table %s", table_key, exc_info=True)


def clamp_page_to_valid(table_key: str, total_rows: int) -> None:
    """Clamp saved page number to valid range based on current row count.

    Called after queue removal when total rows may have decreased below the
    saved page's starting index.  If the saved page exceeds the new max page,
    it is clamped to the last valid page.
    """
    saved = _pagination_cache.get(table_key)
    if saved is None:
        return
    rpp = saved["rowsPerPage"]
    if rpp <= 0:
        return  # infinite rows — page is meaningless
    max_page = max(1, (total_rows + rpp - 1) // rpp)
    if saved["page"] > max_page:
        saved["page"] = max_page


def on_pagination_change_handler(table_key: str):
    """Return a callback suitable for ``ui.table(on_pagination_change=...)``.

    The returned async callable reads the table's live ``.pagination`` dict
    and saves it to the per-table cache.
    """
    async def handler(e):
        # ValueChangeEventArguments uses .value (the new pagination dict),
        # NOT .args.
        if hasattr(e, "value") and isinstance(e.value, dict):
            save_pagination(table_key, e.value)
    return handler