"""Anchors page — playlist dropdown, anchor plan editor, track list."""

import asyncio
import json
import logging
import pathlib
import re

from nicegui import ui

from playlist_arranger import config
from playlist_arranger.ui import state as _state
from playlist_arranger.sources.spotify_source import get_own_playlists
from playlist_arranger.sorting.anchors import _load_anchors_file, _save_anchors_file
from playlist_arranger.ui.components.track_rows import build_track_rows
from playlist_arranger.llm.prompts import ANCHOR_SYSTEM_PROMPT, PLAYLIST_STRUCTURES
from playlist_arranger.database import db as _db

logger = logging.getLogger(__name__)

# ── Module-level button refs & selection state ──────────────────────────────
_controls_row = None
_move_up_btn = None
_move_down_btn = None
_remove_btn = None
_add_placeholder_btn = None
_add_selected_track_btn = None
_generate_anchors_btn = None
_set_anchors_input = None
_set_anchors_btn = None
_clear_btn = None
_save_btn = None
_anchors_select = None
_anchors_list_container = None
_track_list_container = None

# ── SINGLE SOURCE OF TRUTH ────────────────
_anchor_plan: list = []  # anchor plan items (mutated in-place)
_selected_anchor_idx: int | None = None  # selected anchor row (for move/remove)

# Table refs (for programmatic .selected row highlighting)
_anchors_table = None
_track_table = None

# Cached playlist data
_playlist_tracks: list = []
_playlist_name: str = ""

# ── Generate Anchors panel state ───────────────────────────────────────────
_gen_panel: object = None  # ui.column ref, None when collapsed
_gen_panel_visible: bool = False
_gen_structure_dd: object = None
_gen_desc_textarea: object = None
_gen_model_dd: object = None
_gen_n_input: object = None
_gen_run_btn: object = None
_gen_cancel_btn: object = None
_gen_spinner: object = None  # loading spinner, shown during LLM call

# ── Generate Anchors session-level (in-memory) persistence ────────────────
# These remember the user's last selections across collapse/expand cycles
# within the same Python process.  NOT persisted to settings.json/disk.
_gen_last_structure_id: str = PLAYLIST_STRUCTURES[0]["id"] if PLAYLIST_STRUCTURES else "custom"
_gen_last_desc_text: str = PLAYLIST_STRUCTURES[0].get("desc", "") if PLAYLIST_STRUCTURES else ""
_gen_last_desc_touched: bool = False  # True once user has typed in the textarea this session
_gen_last_n: int | None = None  # None = panel never opened; formula-computed on first open
_gen_last_backend: str | None = None  # None = panel never opened; default on first open


def _anchored_track_ids() -> set:
    """Return the set of track IDs currently in the anchor plan."""
    return {e["track_id"] for e in _anchor_plan if e["type"] == "anchor"}


def _selected_track_idx() -> int | None:
    """Derive the 0-based selected track index from table.selected (single source of truth)."""
    if _track_table is None:
        return None
    sel = list(_track_table.selected) if hasattr(_track_table, "selected") else []
    if len(sel) != 1:
        return None
    idx = sel[0].get("idx", 0) if isinstance(sel[0], dict) else 0
    i = int(idx) - 1
    if 0 <= i < len(_playlist_tracks):
        return i
    return None


def _parse_row_idx(e) -> int | None:
    """Extract 0-based row index from NiceGUI 3.x row click event."""
    row = e.args[1] if isinstance(e.args, list) and len(e.args) >= 2 else {}
    idx = row.get("idx", 0)
    if isinstance(idx, (int, float)) and idx > 0:
        return int(idx) - 1
    return None


def _refresh_control_buttons():
    """Re-evaluate enable/disable state for all control buttons."""
    global _move_up_btn, _move_down_btn, _remove_btn
    global _add_selected_track_btn, _clear_btn, _generate_anchors_btn, _save_btn

    n = len(_anchor_plan)
    sel = _selected_anchor_idx
    single_selected = sel is not None and 0 <= sel < n

    if _move_up_btn is not None:
        _move_up_btn.set_enabled(single_selected and sel > 0)
    if _move_down_btn is not None:
        _move_down_btn.set_enabled(single_selected and sel < n - 1)
    if _remove_btn is not None:
        _remove_btn.set_enabled(single_selected)
    if _add_selected_track_btn is not None:
        track_enabled = False
        sti = _selected_track_idx()
        if sti is not None:
            tid = _playlist_tracks[sti].get("id", "")
            track_enabled = tid not in _anchored_track_ids()
        _add_selected_track_btn.set_enabled(track_enabled)
    if _clear_btn is not None:
        _clear_btn.set_enabled(n > 0)
    if _generate_anchors_btn is not None:
        _generate_anchors_btn.set_enabled(len(_playlist_tracks) > 0)


def _rebuild_anchors_list():
    global _anchors_list_container
    if _anchors_list_container is None:
        return
    _anchors_list_container.clear()
    with _anchors_list_container:
        _render_anchors_list()


def _rebuild_track_list():
    global _track_list_container
    if _track_list_container is None:
        return
    _track_list_container.clear()
    with _track_list_container:
        _render_track_list()


# ── Event handlers ──────────────────────────────────────────────────────────

def _on_anchor_row_click(e):
    global _selected_anchor_idx, _anchors_table
    idx = _parse_row_idx(e)
    row_data = e.args[1] if isinstance(e.args, list) and len(e.args) >= 2 else None
    if idx is not None and 0 <= idx < len(_anchor_plan):
        _selected_anchor_idx = idx
        if _anchors_table is not None and row_data is not None:
            _anchors_table.selected = [row_data]
    else:
        _selected_anchor_idx = None
        if _anchors_table is not None:
            _anchors_table.selected = []
    _refresh_control_buttons()


def _on_track_row_click(e):
    idx = _parse_row_idx(e)
    if idx is not None and 0 <= idx < len(_playlist_tracks):
        tid = _playlist_tracks[idx].get("id", "")
        if tid in _anchored_track_ids():
            if _track_table is not None:
                _track_table.selected = []
    _refresh_control_buttons()


def _on_track_selection_change(e):
    _refresh_control_buttons()


def _on_track_double_click(e):
    idx = _parse_row_idx(e)
    if idx is not None and 0 <= idx < len(_playlist_tracks):
        row_data = e.args[1] if isinstance(e.args, list) and len(e.args) >= 2 else None
        tid = _playlist_tracks[idx].get("id", "")
        if tid in _anchored_track_ids():
            if _track_table is not None:
                _track_table.selected = []
        else:
            if _track_table is not None and row_data is not None:
                _track_table.selected = [row_data]
            _add_selected_track()
            if _track_table is not None:
                _track_table.selected = []
    _refresh_control_buttons()


# ── Anchor plan mutation methods ────────────────────────────────────────────

def _move_up():
    global _anchor_plan, _selected_anchor_idx
    if _selected_anchor_idx is None or _selected_anchor_idx <= 0:
        return
    idx = _selected_anchor_idx
    _anchor_plan[idx - 1], _anchor_plan[idx] = _anchor_plan[idx], _anchor_plan[idx - 1]
    _selected_anchor_idx = idx - 1
    _rebuild_anchors_list()
    _rebuild_track_list()
    _refresh_control_buttons()


def _move_down():
    global _anchor_plan, _selected_anchor_idx
    if _selected_anchor_idx is None or _selected_anchor_idx >= len(_anchor_plan) - 1:
        return
    idx = _selected_anchor_idx
    _anchor_plan[idx], _anchor_plan[idx + 1] = _anchor_plan[idx + 1], _anchor_plan[idx]
    _selected_anchor_idx = idx + 1
    _rebuild_anchors_list()
    _rebuild_track_list()
    _refresh_control_buttons()


def _remove_anchor():
    global _anchor_plan, _selected_anchor_idx
    if _selected_anchor_idx is None or _selected_anchor_idx < 0 or _selected_anchor_idx >= len(_anchor_plan):
        return
    del _anchor_plan[_selected_anchor_idx]
    _selected_anchor_idx = None
    _rebuild_anchors_list()
    _rebuild_track_list()
    _refresh_control_buttons()


def _add_placeholder():
    global _anchor_plan
    _anchor_plan.append({"type": "placeholder"})
    _rebuild_anchors_list()
    _rebuild_track_list()
    _refresh_control_buttons()


def _add_selected_track():
    global _anchor_plan
    sti = _selected_track_idx()
    if sti is None:
        return
    tid = _playlist_tracks[sti].get("id", "")
    if tid and tid not in _anchored_track_ids():
        _anchor_plan.append({"type": "anchor", "track_id": tid})
    _rebuild_anchors_list()
    _rebuild_track_list()
    _refresh_control_buttons()


def _clear_anchors():
    global _anchor_plan, _selected_anchor_idx
    _anchor_plan.clear()
    _selected_anchor_idx = None
    if _track_table is not None:
        _track_table.selected = []
    _rebuild_anchors_list()
    _rebuild_track_list()
    _refresh_control_buttons()


def _collapse_adjacent_placeholders(plan: list) -> list:
    """Return a new plan list with adjacent placeholder entries merged into one.

    Example: [anchor, ph, ph, anchor] → [anchor, ph, anchor]
    Leading/trailing placeholder runs also collapse: [ph, ph, anchor] → [ph, anchor]
    """
    if not plan:
        return []
    result = []
    for item in plan:
        if item.get("type") == "placeholder":
            # Only append a placeholder if the previous item ISN'T already
            # a placeholder (skip duplicates adjacent to another placeholder).
            if not result or result[-1].get("type") != "placeholder":
                result.append({"type": "placeholder"})
        else:
            result.append(item)
    return result


def _save_anchors():
    global _anchor_plan, _selected_anchor_idx
    pl_id = getattr(_state, "selected_playlist_id", None)
    if not pl_id:
        ui.notify("No playlist selected", type="warning")
        return

    # Collapse adjacent placeholders in the plan being saved.
    original_len = len(_anchor_plan)
    collapsed = _collapse_adjacent_placeholders(_anchor_plan)

    _save_anchors_file(pl_id, _playlist_name, collapsed)
    actual_count = len(collapsed)

    # Update in-memory plan to match what was written to disk, so the UI
    # never shows a stale state with redundant placeholders that aren't
    # actually in the file.  If the selected anchor index pointed to an
    # item that was collapsed away, reset selection to avoid a stale index.
    if len(collapsed) < original_len:
        _anchor_plan[:] = collapsed
        if _selected_anchor_idx is not None and _selected_anchor_idx >= len(_anchor_plan):
            _selected_anchor_idx = None
        _rebuild_anchors_list()
        _rebuild_track_list()
        _refresh_control_buttons()

    ui.notify(f"Anchors saved ({actual_count} items)", type="positive")
    logger.info("Saved %d anchor items for playlist %s (collapsed from %d)",
                actual_count, pl_id[:8], original_len)


# ── Generate Anchors LLM flow ──────────────────────────────────────────────

def _available_backend_models() -> list[dict]:
    """Return list of {value, label} for each backend that has usable credentials.

    Checks .env for API keys (or Ollama being installed) and uses the model
    name from settings.json (falling back to .env defaults).
    """
    from playlist_arranger.config import (
        OLLAMA_MODEL, DEEPSEEK_MODEL, MISTRAL_MODEL,
    )
    from playlist_arranger.llm.client import (
        DEEPSEEK_API_KEY, MISTRAL_API_KEY, OLLAMA_BASE_URL,
    )
    s = config.load_settings()
    options = []

    # Ollama: always available (local server, no API key needed)
    ollama_model = s.ollama_model or OLLAMA_MODEL
    options.append({
        "value": "ollama",
        "label": f"ollama: {ollama_model}",
    })

    # DeepSeek: available if API key is set
    if DEEPSEEK_API_KEY:
        deepseek_model = s.deepseek_model or DEEPSEEK_MODEL
        options.append({
            "value": "deepseek",
            "label": f"deepseek: {deepseek_model}",
        })

    # Mistral: available if API key is set
    if MISTRAL_API_KEY:
        mistral_model = s.mistral_model or MISTRAL_MODEL
        options.append({
            "value": "mistral",
            "label": f"mistral: {mistral_model}",
        })

    return options


def _resolve_model_name(backend: str | None = None) -> str:
    """Return the current active model name for a backend, or all backends.

    If backend is provided, return just that backend's model name.
    If backend is unrecognised or None, use the configured default backend.
    """
    s = config.load_settings()
    model_map = {
        "ollama": s.ollama_model,
        "deepseek": s.deepseek_model,
        "mistral": s.mistral_model,
    }
    if backend is None:
        backend = s.llm_backend or config.LLM_BACKEND
    if backend in model_map:
        return model_map[backend] or "(no model set)"
    return f"(unknown backend: {backend})"


# ── Track description block / file helpers ─────────────────────────────────────

CHUNK_THRESHOLD = 300  # trigger chunked batching above this many tracks
CHUNK_SIZE = 300       # tracks per batch when chunking


def _build_single_track_line(i: int, t: dict) -> str:
    """Build a single track's description line (same format as before)."""
    tid = t.get("id", "")
    name = t.get("name", "?")[:60]
    artist = t.get("artist", "?")[:40]

    entry = _db.get_track(tid) if tid else None
    desc = ""
    if entry and entry.get("desc_text"):
        desc = entry["desc_text"][:150]

    from playlist_arranger.analysis.desc_generator import _feat_summary

    feat = _feat_summary(entry) if entry else ""
    desc_display = desc if desc else "(no description available)"

    return f'  {i:4d}. "{name}" — {artist}\n        {desc_display}\n        [{feat}]'


def _build_track_descriptions_list() -> list[str]:
    """Return a list of per-track description strings (one per track, 1-indexed).

    Same content as ``_build_track_descriptions_block`` but returned as a
    list of individual strings instead of one concatenated block.
    """
    return [_build_single_track_line(i, t) for i, t in enumerate(_playlist_tracks, 1)]


def _build_track_descriptions_block() -> str:
    """Build a position-numbered list of tracks with descriptions for the LLM prompt.

    Tracks without descriptions are INCLUDED in the list (keeping position
    numbers contiguous 1..N) with "(no description available)" as placeholder.
    Reads descriptions from the database via _db.get_track().
    """
    return "\n".join(_build_track_descriptions_list())


def _track_descriptions_file_path(plid: str) -> pathlib.Path:
    """Return the path for the anchor context file for a given playlist ID."""
    return config.ANCHORS_DIR_DEFAULT / f"anchor_context_{plid}.txt"


def write_track_descriptions_file(descs: list[str], plid: str) -> pathlib.Path:
    """Write track descriptions to a plain text file atomically.

    Each element in *descs* is one track's formatted description line
    (the same content ``_build_track_descriptions_block`` produces).
    Writes to ``ANCHORS_DIR / anchor_context_{plid}.txt`` using the
    atomic write pattern (``.tmp`` then rename).

    Returns the path of the written file.
    """
    file_path = _track_descriptions_file_path(plid)
    content = "\n".join(descs).encode("utf-8")

    tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
    tmp_path.write_bytes(content)
    tmp_path.replace(file_path)

    logger.debug("Wrote %d track descriptions to %s", len(descs), file_path)
    return file_path


def _read_track_descriptions_file(plid: str) -> str | None:
    """Read the anchor context file for a playlist, or None if not found."""
    file_path = _track_descriptions_file_path(plid)
    try:
        return file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except Exception:
        logger.warning("Failed to read track descriptions file %s", file_path, exc_info=True)
        return None


def _parse_anchor_positions(raw_text: str, expected_n: int, track_count: int) -> list[int] | None:
    """Parse LLM response for position numbers. Returns 1-based positions or None on failure.

    STRICT validation: exactly N numbers, all in [1, track_count], no duplicates.
    Any deviation aborts with None (user must retry).

    The regex is slightly lenient on formatting to tolerate common LLM quirks
    (bullet-prefixed numbers like ``- 7`` or ``* 23``) while still requiring
    valid integer positions.  Count/range/duplicate validation remains STRICT.
    """
    # Find ANCHORS: section
    section = re.search(r"ANCHORS\s*:(.*)", raw_text, flags=re.DOTALL | re.IGNORECASE)
    block = section.group(1).strip() if section else raw_text

    numbers = []
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue  # blank lines are fine, skip
        # Match a bare number optionally preceded by bullets/hyphens/whitespace.
        # Examples: "7", "  7  ", "7.", "- 7", "* 7", "#7"
        m = re.match(r"^[\s\-*#]*(\d+)\s*\.?\s*$", line)
        if m:
            numbers.append(int(m.group(1)))
        if len(numbers) >= expected_n + 5:  # generous cap to prevent runaway parsing
            break

    if len(numbers) != expected_n:
        return None  # STRICT: exactly N

    # Validate range
    if any(n < 1 or n > track_count for n in numbers):
        return None

    # Validate no duplicates
    if len(set(numbers)) != len(numbers):
        return None

    return numbers


def _on_generate_anchors_clicked():
    """Toggle visibility of the Generate Anchors panel."""
    global _gen_panel_visible, _gen_panel
    _gen_panel_visible = not _gen_panel_visible

    if _gen_panel_visible:
        # Render the panel
        _gen_panel.clear()
        with _gen_panel:
            _render_generate_panel()
    else:
        _gen_panel.clear()


def _on_structure_changed(e):
    """Handle structure type dropdown change."""
    global _gen_desc_textarea, _gen_n_input
    global _gen_last_structure_id, _gen_last_desc_text, _gen_last_desc_touched, _gen_last_n
    if _gen_desc_textarea is None or _gen_n_input is None:
        return

    chosen_id = e.value
    _gen_last_structure_id = chosen_id

    # Find the structure
    struct = next((s for s in PLAYLIST_STRUCTURES if s["id"] == chosen_id), None)
    if struct is None:
        return

    if chosen_id == "custom":
        # Load persisted custom prompt
        s = config.load_settings()
        _gen_desc_textarea.value = s.custom_anchor_prompt or ""
        _gen_last_desc_text = _gen_desc_textarea.value
        _gen_last_desc_touched = False  # reset — this is a load, not user typing
    else:
        _gen_desc_textarea.value = struct.get("desc", "")
        _gen_last_desc_text = _gen_desc_textarea.value
        _gen_last_desc_touched = False

    # Recompute N (formula overrides user override when structure changes)
    anchor_pct = struct.get("anchor_pct", 20)
    n = max(3, int(len(_playlist_tracks) * anchor_pct / 100))
    _gen_n_input.value = n
    _gen_last_n = n


async def _on_run_generate():
    """Execute the full generate-anchors flow: prompt → LLM → parse → apply.

    Custom anchor prompts are persisted ONLY when the Run button is clicked
    with structure type "custom" — NOT on every keystroke.  This eliminates
    unnecessary disk I/O (config.save_settings() rewrites the entire
    settings.json file each call, so per-keystroke writes would be wasteful).

    IMPORTANT — Async / to_thread pattern:
    This function is ``async def`` and wraps the blocking LLM HTTP call in
    ``await asyncio.to_thread(...)``.  NiceGUI runs on a single asyncio event
    loop; a synchronous blocking network call inside an event handler would
    FREEZE THE ENTIRE SERVER for ALL connected clients for the duration of
    the request.  The same pattern is used in playlist_source.py for Spotify
    OAuth (``await asyncio.to_thread(init_spotify, None)``) and track loading
    (``await asyncio.to_thread(load_cached_playlist_tracks, pid)``).
    """
    global _anchor_plan, _gen_panel_visible, _gen_panel
    global _gen_structure_dd, _gen_desc_textarea, _gen_n_input, _gen_model_dd

    if not _playlist_tracks:
        ui.notify("No tracks loaded.", type="warning")
        return

    # Read current panel values
    structure_id = _gen_structure_dd.value if _gen_structure_dd else PLAYLIST_STRUCTURES[0]["id"]
    desc_text = _gen_desc_textarea.value if _gen_desc_textarea else ""
    n_anchors = int(_gen_n_input.value) if _gen_n_input else 5

    # Determine which backend/model to use for this call.
    # The model dropdown value is the backend key (e.g. "ollama", "deepseek").
    # The actual model name is resolved from settings (same logic as the label).
    selected_backend = _gen_model_dd.value if _gen_model_dd else None
    if not selected_backend:
        s = config.load_settings()
        selected_backend = s.llm_backend or config.LLM_BACKEND
    selected_model = _resolve_model_name(selected_backend)

    # Persist custom prompt only on Run click (Option 3: no per-keystroke
    # disk writes — save_settings rewrites the entire settings.json).
    if structure_id == "custom":
        s = config.load_settings()
        s.custom_anchor_prompt = desc_text or ""
        config.save_settings(s)

    # Build track descriptions (as list + file)
    pl_id = getattr(_state, "selected_playlist_id", None)
    all_descs = _build_track_descriptions_list()
    total_tracks = len(all_descs)

    # Write descriptions to file (single source of truth)
    if pl_id:
        write_track_descriptions_file(all_descs, pl_id)

    # Build user message WITHOUT the full descriptions block
    struct = next((s for s in PLAYLIST_STRUCTURES if s["id"] == structure_id), PLAYLIST_STRUCTURES[0])

    def _build_user_msg(desc_content: str) -> str:
        """Build a user_msg with the given descriptions content appended."""
        return (
            f'Playlist name: "{_playlist_name}"\n'
            f"Total tracks: {len(_playlist_tracks)}\n\n"
            f'Requested structure: {struct["name"]}\n'
            f"{desc_text}\n\n"
            f"Select exactly {n_anchors} anchor tracks and arrange them "
            f"to create this structure. Below is the full track list "
            f"with positions, descriptions and technical features:\n\n"
            f"{desc_content}\n\n"
            f"Now select {n_anchors} anchor positions and return them as position numbers."
        )

    # Decide: chunked batching or single call
    use_chunked = total_tracks > CHUNK_THRESHOLD

    if use_chunked:
        # ── Chunked batching ──────────────────────────────────────────────
        logger.info(
            "Anchor generation: %d tracks exceeds threshold (%d) — "
            "using chunked batching (chunk size %d)",
            total_tracks, CHUNK_THRESHOLD, CHUNK_SIZE,
        )
        # Build per-chunk info
        chunks: list[list[str]] = []
        chunk_ranges: list[tuple[int, int]] = []  # (start_pct, end_pct) for positional context
        chunk_n_anchors_list: list[int] = []
        for start in range(0, total_tracks, CHUNK_SIZE):
            end = min(start + CHUNK_SIZE, total_tracks)
            chunk_descs = all_descs[start:end]
            chunks.append(chunk_descs)
            # Positional context: what portion of the playlist this chunk covers
            start_pct = int(start * 100 / total_tracks)
            end_pct = int(end * 100 / total_tracks)
            chunk_ranges.append((start_pct, end_pct))
            # Proportional share of anchors for this chunk
            chunk_n = max(0, round(n_anchors * len(chunk_descs) / total_tracks))
            chunk_n_anchors_list.append(chunk_n)

        # Ensure total anchors across chunks doesn't exceed requested
        # Adjust largest chunks first if there's a rounding discrepancy
        total_allocated = sum(chunk_n_anchors_list)
        while total_allocated > n_anchors:
            max_idx = chunk_n_anchors_list.index(max(chunk_n_anchors_list))
            if chunk_n_anchors_list[max_idx] > 0:
                chunk_n_anchors_list[max_idx] -= 1
                total_allocated -= 1
            else:
                break
        while total_allocated < n_anchors:
            min_idx = chunk_n_anchors_list.index(min(chunk_n_anchors_list))
            chunk_n_anchors_list[min_idx] += 1
            total_allocated += 1

        logger.debug(
            "Chunk plan: %d chunks, anchors per chunk: %s, ranges: %s",
            len(chunks), chunk_n_anchors_list, chunk_ranges,
        )

        # ── Show loading state ─────────────────────────────────────────────
        if _gen_run_btn is not None:
            _gen_run_btn.set_enabled(False)
        if _gen_cancel_btn is not None:
            _gen_cancel_btn.set_enabled(False)
        if _gen_spinner is not None:
            _gen_spinner.set_visibility(True)

        from playlist_arranger.llm.client import llm_chat, _init_llm_client

        all_positions: list[int] = []
        raw_resp = ""

        def _build_chunk_user_msg(chunk_descs: list[str], chunk_idx: int, chunk_n: int) -> str:
            """Build a chunk-aware user message with positional context.

            Tells the LLM which portion of the playlist this chunk covers
            (e.g. "tracks ~0% to ~33% — the opening/rising segment")
            and what energy/mood target is expected for this portion of
            the requested structure arc.
            """
            start_pct, end_pct = chunk_ranges[chunk_idx]
            n_chunks = len(chunks)

            # Describe the arc position qualitatively
            if n_chunks == 1:
                position_hint = "This covers the entire playlist."
            elif chunk_idx == 0:
                position_hint = (
                    f"This chunk covers tracks ~{start_pct}%–{end_pct}% — "
                    f"the BEGINNING/OPENING portion of the playlist. "
                    f"Select tracks appropriate for the START of a "
                    f"\"{struct['name']}\" arc ({struct.get('desc', desc_text)})."
                )
            elif chunk_idx == n_chunks - 1:
                position_hint = (
                    f"This chunk covers tracks ~{start_pct}%–{end_pct}% — "
                    f"the ENDING/CLOSING portion of the playlist. "
                    f"Select tracks appropriate for the END of a "
                    f"\"{struct['name']}\" arc ({struct.get('desc', desc_text)})."
                )
            else:
                position_hint = (
                    f"This chunk covers tracks ~{start_pct}%–{end_pct}% — "
                    f"the MIDDLE/TRANSITION portion of the playlist (chunk "
                    f"{chunk_idx + 1} of {n_chunks}). "
                    f"Select tracks that fit this intermediate position "
                    f"within a \"{struct['name']}\" arc "
                    f"({struct.get('desc', desc_text)})."
                )

            chunk_content = "\n".join(chunk_descs)
            return (
                f'Playlist name: "{_playlist_name}"\n'
                f"Total tracks in full playlist: {len(_playlist_tracks)}\n"
                f"Tracks in this chunk: {len(chunk_descs)}\n\n"
                f'Requested full-playlist structure: {struct["name"]}\n'
                f"{desc_text}\n\n"
                f"ARC POSITION: {position_hint}\n\n"
                f"From this chunk, select exactly {chunk_n} anchor tracks "
                f"that best serve this portion of the arc. "
                f"Below are the tracks with positions, descriptions and "
                f"technical features:\n\n"
                f"{chunk_content}\n\n"
                f"Return exactly {chunk_n} anchor positions as position numbers."
            )

        _progress_notify = None  # handle for the active "Processing chunk" notification

        def _dismiss_progress():
            """Dismiss the current 'Processing chunk...' notification if any."""
            nonlocal _progress_notify
            if _progress_notify is not None:
                try:
                    _progress_notify.dismiss()
                except Exception:
                    pass
                _progress_notify = None

        try:
            for ci, (chunk_descs, chunk_n) in enumerate(zip(chunks, chunk_n_anchors_list)):
                if chunk_n <= 0:
                    logger.debug("Chunk %d: %d anchors → skipping", ci + 1, chunk_n)
                    continue
                logger.info(
                    "Chunk %d/%d: %d tracks (~%d%%–%d%%), %d anchors requested",
                    ci + 1, len(chunks), len(chunk_descs),
                    chunk_ranges[ci][0], chunk_ranges[ci][1], chunk_n,
                )

                # ── Per-chunk progress notification ──────────────────────────
                _dismiss_progress()  # dismiss previous "Processing..." handle
                try:
                    _progress_notify = ui.notify(
                        f"Processing chunk {ci + 1} of {len(chunks)} "
                        f"({len(chunk_descs)} tracks, ~{chunk_ranges[ci][0]}%–"
                        f"{chunk_ranges[ci][1]}% of playlist)…",
                        timeout=0,  # stays until manually dismissed
                        type="ongoing",
                    )
                except Exception:
                    _progress_notify = None

                def _blocking_chunk_call(chunk_idx: int, chunk_descs: list[str], chunk_n: int) -> str:
                    _init_llm_client(backend=selected_backend, model_override=selected_model)
                    chunk_user_msg = _build_chunk_user_msg(chunk_descs, chunk_idx, chunk_n)
                    return llm_chat(ANCHOR_SYSTEM_PROMPT, chunk_user_msg, temperature=0.4, max_tokens=6000)

                # Save chunk prompt to debug file BEFORE the LLM call
                chunk_user_msg = _build_chunk_user_msg(chunk_descs, ci, chunk_n)
                chunk_prompt_path = config.CACHE_DIR_DEFAULT / f"anchors_chunk_{ci + 1}_prompt.txt"
                full_chunk_prompt = (
                    f"=== CHUNK {ci + 1} of {len(chunks)} ===\n"
                    f"=== SYSTEM PROMPT ===\n{ANCHOR_SYSTEM_PROMPT}\n\n"
                    f"=== USER MESSAGE ===\n{chunk_user_msg}\n"
                )
                try:
                    chunk_prompt_path.write_text(full_chunk_prompt, encoding="utf-8")
                    logger.debug("Saved chunk %d/%d prompt to %s", ci + 1, len(chunks), chunk_prompt_path)
                except Exception:
                    logger.warning("Failed to save chunk %d/%d prompt", ci + 1, len(chunks), exc_info=True)

                raw_resp = await asyncio.to_thread(_blocking_chunk_call, ci, chunk_descs, chunk_n)
                _dismiss_progress()  # dismiss "Processing..." after LLM returns
                if not raw_resp:
                    logger.warning("Chunk %d/%d: LLM returned empty response", ci + 1, len(chunks))
                    try:
                        ui.notify(
                            f"Chunk {ci + 1}/{len(chunks)}: no response — stopping anchor generation",
                            timeout=5000,
                            type="negative",
                        )
                    except Exception:
                        pass
                    break

                chunk_positions = _parse_anchor_positions(raw_resp, chunk_n, total_tracks)
                if chunk_positions is None:
                    logger.warning(
                        "Chunk %d/%d: failed to parse %d positions — stopping anchor generation",
                        ci + 1, len(chunks), chunk_n,
                    )
                    try:
                        ui.notify(
                            f"Chunk {ci + 1}/{len(chunks)}: could not parse response — "
                            f"stopping anchor generation",
                            timeout=5000,
                            type="negative",
                        )
                    except Exception:
                        pass
                    break
                all_positions.extend(chunk_positions)
                logger.info(
                    "Chunk %d/%d: parsed %d positions, total so far: %d",
                    ci + 1, len(chunks), len(chunk_positions), len(all_positions),
                )
                try:
                    ui.notify(
                        f"Chunk {ci + 1}/{len(chunks)}: {len(chunk_positions)} "
                        f"anchors selected ✓",
                        timeout=3000,
                        type="positive",
                    )
                except Exception:
                    pass
        except Exception as exc:
            _dismiss_progress()  # safety net: dismiss any lingering progress notification
            try:
                ui.notify(f"LLM error: {exc}", type="negative")
            except Exception:
                logger.exception("Failed to show LLM error notification")
            finally:
                if _gen_run_btn is not None:
                    _gen_run_btn.set_enabled(True)
                if _gen_cancel_btn is not None:
                    _gen_cancel_btn.set_enabled(True)
                if _gen_spinner is not None:
                    _gen_spinner.set_visibility(False)
            logger.exception("Anchor generation chunked LLM call failed")
            return
        finally:
            _dismiss_progress()  # belt-and-suspenders: dismiss any lingering "Processing..." notification

        if not all_positions:
            if _gen_run_btn is not None:
                _gen_run_btn.set_enabled(True)
            if _gen_cancel_btn is not None:
                _gen_cancel_btn.set_enabled(True)
            if _gen_spinner is not None:
                _gen_spinner.set_visibility(False)
            ui.notify("All LLM chunks returned empty or unparseable responses", type="negative")
            return

        # Deduplicate positions (different chunks may pick the same track)
        seen = set()
        deduped_positions = []
        for pos in all_positions:
            if pos not in seen:
                seen.add(pos)
                deduped_positions.append(pos)

        # If we have more than requested (unlikely after dedup), truncate
        if len(deduped_positions) > n_anchors:
            deduped_positions = deduped_positions[:n_anchors]

        actual_n = len(deduped_positions)

        if actual_n < n_anchors:
            logger.warning(
                "Chunked batching produced %d anchors vs %d requested",
                actual_n, n_anchors,
            )

        # ── Option B: Lightweight re-order pass ───────────────────────────
        deduped_positions = _try_reorder_anchor_positions(
            deduped_positions, struct, desc_text, total_tracks,
            selected_backend, selected_model,
        )

        # Restore UI after all chunks + re-order
        if _gen_run_btn is not None:
            _gen_run_btn.set_enabled(True)
        if _gen_cancel_btn is not None:
            _gen_cancel_btn.set_enabled(True)
        if _gen_spinner is not None:
            _gen_spinner.set_visibility(False)

        positions = deduped_positions

    else:
        # ── Single call (within threshold) ─────────────────────────────────
        # Read back the file for debug/logging consistency
        desc_content = "\n".join(all_descs)
        user_msg = _build_user_msg(desc_content)

        # Save debug prompt BEFORE LLM call
        debug_path = config.ANCHORS_DIR_DEFAULT / "anchors_prompts_for_debug.txt"
        full_prompt = (
            f"=== SYSTEM PROMPT ===\n{ANCHOR_SYSTEM_PROMPT}\n\n"
            f"=== USER MESSAGE ===\n{user_msg}\n"
        )
        try:
            debug_path.write_text(full_prompt, encoding="utf-8")
        except Exception:
            pass

        # ── Show loading state ─────────────────────────────────────────────
        if _gen_run_btn is not None:
            _gen_run_btn.set_enabled(False)
        if _gen_cancel_btn is not None:
            _gen_cancel_btn.set_enabled(False)
        if _gen_spinner is not None:
            _gen_spinner.set_visibility(True)

        from playlist_arranger.llm.client import llm_chat, _init_llm_client

        raw_resp = ""
        try:
            def _blocking_llm_call():
                _init_llm_client(backend=selected_backend, model_override=selected_model)
                return llm_chat(ANCHOR_SYSTEM_PROMPT, user_msg, temperature=0.4, max_tokens=6000)

            raw_resp = await asyncio.to_thread(_blocking_llm_call)
        except Exception as exc:
            try:
                ui.notify(f"LLM error: {exc}", type="negative")
            except Exception:
                logger.exception("Failed to show LLM error notification")
            finally:
                if _gen_run_btn is not None:
                    _gen_run_btn.set_enabled(True)
                if _gen_cancel_btn is not None:
                    _gen_cancel_btn.set_enabled(True)
                if _gen_spinner is not None:
                    _gen_spinner.set_visibility(False)
            logger.exception("Anchor generation LLM call failed")
            return

        # Restore UI
        if _gen_run_btn is not None:
            _gen_run_btn.set_enabled(True)
        if _gen_cancel_btn is not None:
            _gen_cancel_btn.set_enabled(True)
        if _gen_spinner is not None:
            _gen_spinner.set_visibility(False)

        if not raw_resp:
            ui.notify("LLM returned empty response", type="negative")
            return

        # Parse response
        positions = _parse_anchor_positions(raw_resp, n_anchors, len(_playlist_tracks))
        if positions is None:
            ui.notify(
                f"Failed to parse {n_anchors} valid position numbers from LLM response. "
                f"Check debug output in cache/anchors_prompts_for_debug.txt",
                type="negative",
            )
            return

    # ── Common post-processing (both single and chunked paths) ─────────────
    # 'positions' is already set from either the chunked or single-call path above

    # Convert 1-based positions to 0-based indices, look up track IDs
    anchor_items = []
    for pos in positions:
        idx = pos - 1
        tid = _playlist_tracks[idx].get("id", "")
        anchor_items.append({"type": "anchor", "track_id": tid})

    # Clear existing plan, build new with interior placeholders
    _anchor_plan.clear()
    for i, item in enumerate(anchor_items):
        _anchor_plan.append(item)
        if i < len(anchor_items) - 1:
            _anchor_plan.append({"type": "placeholder"})

    # Rebuild UI
    _rebuild_anchors_list()
    _rebuild_track_list()
    _refresh_control_buttons()

    # ── Notify success BEFORE collapsing the panel ───────────────────────
    # Bug 2 fix: after an async handler resumes from asyncio.to_thread,
    # calling ui.notify() AFTER _gen_panel.clear() can fail because the
    # panel's UI slot context no longer exists.  Always notify first.
    notification_ok = True
    try:
        ui.notify(f"Generated {len(anchor_items)} anchors", type="positive")
    except Exception:
        logger.exception("Failed to show anchor generation success notification")
        notification_ok = False
    else:
        logger.info("Anchor generation: selected %d anchors for playlist %s",
                    len(anchor_items),
                    getattr(_state, "selected_playlist_id", "")[:8])

    # Now safe to collapse the panel (done, whether notify succeeded or not)
    _gen_panel_visible = False
    _gen_panel.clear()

    if not notification_ok:
        # Recover: the data mutation succeeded, so don't leave user in the dark
        try:
            ui.notify(f"Generated {len(anchor_items)} anchors (notification delayed)", type="positive")
        except Exception:
            pass  # truly can't notify — anchors are still applied


def _try_reorder_anchor_positions(
    deduped_positions: list[int],
    struct: dict,
    desc_text: str,
    total_tracks: int,
    backend: str,
    model: str,
) -> list[int]:
    """Attempt to re-order anchor positions into the requested structure arc.

    Sends a lightweight LLM prompt with only anchor names + BPM/key/loudness
    (no full descriptions), then validates the response preserves the same
    set of positions.

    Returns:
        The reordered list if the LLM returns a valid same-set permutation.
        The original *deduped_positions* unchanged if the reorder fails
        (empty response, parse failure, LLM exception, or mismatched set).
    """
    from playlist_arranger.llm.client import llm_chat, _init_llm_client

    actual_n = len(deduped_positions)
    if actual_n < 3 or actual_n < 2:
        return list(deduped_positions)

    # Build name-only anchor list with key features
    anchor_summaries: list[str] = []
    for pos in deduped_positions:
        t = _playlist_tracks[pos - 1]
        name = t.get("name", "?")[:50]
        artist = t.get("artist", "?")[:30]
        tid = t.get("id", "")
        entry = _db.get_track(tid) if tid else None
        features = entry.get("features", {}) if entry else {}
        bpm = features.get("bpm", "?")
        key = features.get("camelot", features.get("chroma_key", "?"))
        rms = features.get("rms_db", "?")
        summary = f'  {pos}. "{name}" — {artist}  [BPM: {bpm}, Key: {key}, Loudness: {rms} dBFS]'
        anchor_summaries.append(summary)

    reorder_msg = (
        f'Playlist: "{_playlist_name}" ({total_tracks} tracks)\n'
        f'Requested structure: {struct["name"]}\n'
        f"{desc_text}\n\n"
        f"Below are {actual_n} anchor tracks already selected "
        f"from the playlist.  Each has a position number (the "
        f"track's original position in the playlist).\n\n"
        f"Your task: re-order these {actual_n} anchors into the "
        f"correct sequence that realises the \"{struct['name']}\" "
        f"structure arc.  Return ONLY the position numbers in "
        f"the desired order — one per line, using the SAME "
        f"position numbers shown below.  Do not add commentary.\n\n"
        f"--- ANCHOR TRACKS ---\n"
        f"{chr(10).join(anchor_summaries)}\n\n"
        f"Re-order the {actual_n} positions above into the "
        f"correct \"{struct['name']}\" arc sequence and return "
        f"them in ANCHORS: format."
    )

    reorder_system = (
        "You are a playlist re-ordering assistant. "
        "You receive a list of anchor tracks (name, artist, BPM, "
        "key, loudness) and a target structure type. "
        "Output ONLY the position numbers in the requested order, "
        "one per line, in ANCHORS: format.  No commentary."
    )

    try:
        _init_llm_client(backend=backend, model_override=model)
        reorder_raw = llm_chat(reorder_system, reorder_msg, temperature=0.2, max_tokens=6000)
        if reorder_raw:
            reordered = _parse_anchor_positions(reorder_raw, actual_n, total_tracks)
            if reordered is not None:
                if set(reordered) == set(deduped_positions):
                    logger.info(
                        "Re-order pass succeeded: %d anchors reordered",
                        actual_n,
                    )
                    return reordered
                else:
                    logger.warning(
                        "Re-order pass produced different position set — "
                        "keeping original chunk order. "
                        "Expected %s, got %s",
                        sorted(deduped_positions), sorted(reordered),
                    )
            else:
                logger.warning(
                    "Re-order pass parse failed — keeping chunk order"
                )
        else:
            logger.warning(
                "Re-order pass returned empty — keeping chunk order"
            )
    except Exception:
        logger.exception(
            "Re-order pass LLM call failed — keeping chunk order"
        )

    return list(deduped_positions)


def _on_cancel_generate():
    """Collapse the Generate Anchors panel — no side effects."""
    global _gen_panel_visible, _gen_panel
    _gen_panel_visible = False
    _gen_panel.clear()


def _on_desc_text_changed(e):
    """Lightweight session-level in-memory persistence — no disk I/O."""
    global _gen_last_desc_text, _gen_last_desc_touched
    _gen_last_desc_text = e.value or ""
    _gen_last_desc_touched = True


def _on_n_changed(e):
    """Lightweight session-level in-memory persistence — no disk I/O."""
    global _gen_last_n
    try:
        _gen_last_n = int(e.value)
    except (TypeError, ValueError):
        pass


def _on_model_changed(e):
    """Lightweight session-level in-memory persistence — no disk I/O."""
    global _gen_last_backend
    _gen_last_backend = e.value


def _render_generate_panel():
    """Render the Generate Anchors controls panel, restoring session-level state.

    The model dropdown lists ALL backends that have usable credentials
    (Ollama if installed, DeepSeek/Mistral if API keys are set in .env).
    Values are restored from _gen_last_* session globals, falling back to
    hardcoded defaults only on first-open-ever this process lifetime.
    """
    global _gen_structure_dd, _gen_desc_textarea, _gen_model_dd, _gen_n_input
    global _gen_run_btn, _gen_cancel_btn, _gen_spinner
    global _gen_last_structure_id, _gen_last_desc_text, _gen_last_desc_touched, _gen_last_n, _gen_last_backend

    # Structure type dropdown — restore last or default
    structure_options = {s["id"]: s["name"] for s in PLAYLIST_STRUCTURES}
    struct_id = _gen_last_structure_id if _gen_last_structure_id in structure_options else PLAYLIST_STRUCTURES[0]["id"]

    _gen_structure_dd = (
        ui.select(
            label="Playlist structure",
            options=structure_options,
            value=struct_id,
            on_change=_on_structure_changed,
        )
        .classes("w-80")
    )

    # Description textarea — restore session-level value, with custom-structure
    # edge case: if this is the first open (never touched) and structure is
    # "custom", load from settings.json.  Otherwise use in-session memory.
    if struct_id == "custom" and not _gen_last_desc_touched:
        s = config.load_settings()
        desc_value = s.custom_anchor_prompt or ""
    else:
        desc_value = _gen_last_desc_text if _gen_last_desc_text else PLAYLIST_STRUCTURES[0].get("desc", "")

    _gen_desc_textarea = (
        ui.textarea(
            label="Structure description",
            value=desc_value,
            on_change=_on_desc_text_changed,
        )
        .classes("w-full")
        .props("rows=3")
    )

    # Model dropdown — restore last or default
    available = _available_backend_models()
    s = config.load_settings()
    default_backend = s.llm_backend or config.LLM_BACKEND
    if default_backend not in {opt["value"] for opt in available}:
        default_backend = available[0]["value"] if available else "ollama"

    # Use session-last backend if it's available; otherwise fall back to default
    if _gen_last_backend and _gen_last_backend in {opt["value"] for opt in available}:
        model_value = _gen_last_backend
    else:
        model_value = default_backend
        _gen_last_backend = model_value  # remember for next toggle

    model_options = {opt["value"]: opt["label"] for opt in available}

    _gen_model_dd = (
        ui.select(
            label="LLM Model",
            options=model_options,
            value=model_value,
            on_change=_on_model_changed,
        )
        .classes("w-80")
    )

    # N input — restore last or compute from formula
    if _gen_last_n is not None:
        n_value = max(1, min(_gen_last_n, len(_playlist_tracks))) if _playlist_tracks else _gen_last_n
    else:
        struct = next((s for s in PLAYLIST_STRUCTURES if s["id"] == struct_id), PLAYLIST_STRUCTURES[0])
        anchor_pct = struct.get("anchor_pct", 20)
        n_value = max(3, int(len(_playlist_tracks) * anchor_pct / 100))
        _gen_last_n = n_value

    _gen_n_input = (
        ui.number(
            label="Number of anchors",
            value=n_value,
            min=1,
            max=len(_playlist_tracks) if _playlist_tracks else 99,
            step=1,
            on_change=_on_n_changed,
        )
        .classes("w-32")
    )

    # Action buttons + spinner
    with ui.row().classes("gap-2 items-center"):
        _gen_run_btn = ui.button("Clear current anchors and Run", on_click=_on_run_generate).props(
            "color=green"
        )
        _gen_cancel_btn = ui.button("Cancel", on_click=_on_cancel_generate).props("color=grey")
        _gen_spinner = ui.spinner(size="sm")
        _gen_spinner.set_visibility(False)


def _on_set_anchors():
    """Validate comma-separated track numbers and add them to the anchor plan.

    Validation is STRICT and all-or-nothing — if ANY token fails validation
    the entire operation is aborted and the plan is NOT modified.
    """
    global _anchor_plan, _set_anchors_input

    if _set_anchors_input is None:
        return

    raw = (_set_anchors_input.value or "").strip()
    if not raw:
        ui.notify("Enter comma-separated track numbers", type="warning")
        return

    # ── Tokenize ───────────────────────────────────────────────────────────
    tokens = [t.strip() for t in raw.split(",")]
    # Reject trailing/double commas (empty tokens)
    if any(t == "" for t in tokens):
        ui.notify("Input contains empty entries (double or trailing comma)", type="warning")
        return

    # ── Validate each token is a positive integer ─────────────────────────
    int_re = re.compile(r"^\d+$")
    numbers: list[int] = []
    for token in tokens:
        if not int_re.match(token):
            ui.notify(f"Invalid entry: '{token}' is not a valid integer", type="negative")
            return
        numbers.append(int(token))

    # ── Range check ────────────────────────────────────────────────────────
    n_tracks = len(_playlist_tracks)
    for n in numbers:
        if n < 1 or n > n_tracks:
            ui.notify(
                f"Track number {n} is out of range (1-{n_tracks})",
                type="negative",
            )
            return

    # ── Duplicate check in input ──────────────────────────────────────────
    if len(set(numbers)) != len(numbers):
        ui.notify("Duplicate track numbers in input", type="negative")
        return

    # ── Already-anchored check ────────────────────────────────────────────
    already = _anchored_track_ids()
    for n in numbers:
        idx = n - 1
        tid = _playlist_tracks[idx].get("id", "")
        if tid in already:
            ui.notify(
                f"Track {n} is already anchored",
                type="negative",
            )
            return

    # ── All validation passed — apply ─────────────────────────────────────
    # Insert a separator placeholder between existing plan and new anchors
    # if the plan is non-empty and the last entry is NOT already a placeholder.
    if _anchor_plan and _anchor_plan[-1].get("type") != "placeholder":
        _anchor_plan.append({"type": "placeholder"})

    for i, n in enumerate(numbers):
        # Precede every anchor except the very first one with a placeholder
        if i > 0:
            _anchor_plan.append({"type": "placeholder"})
        idx = n - 1
        tid = _playlist_tracks[idx].get("id", "")
        _anchor_plan.append({"type": "anchor", "track_id": tid})

    # Collapse adjacent placeholders (existing helper) — handles the case
    # where the old plan already ended with a placeholder.
    _anchor_plan[:] = _collapse_adjacent_placeholders(_anchor_plan)

    # Clear input, refresh UI
    _set_anchors_input.value = ""
    _rebuild_anchors_list()
    _rebuild_track_list()
    _refresh_control_buttons()

    pl_id = getattr(_state, "selected_playlist_id", None)
    if pl_id:
        try:
            _save_anchors_file(pl_id, _playlist_name, _anchor_plan)
        except Exception:
            logger.exception("Failed to auto-save after Set Anchors")
    ui.notify(f"Added {len(numbers)} anchors", type="positive")


# ── UI rendering ───────────────────────────────────────────────────────────

def _render_controls():
    """Render the anchor control buttons row."""
    global _controls_row, _move_up_btn, _move_down_btn, _remove_btn
    global _add_placeholder_btn, _add_selected_track_btn, _generate_anchors_btn
    global _set_anchors_input, _set_anchors_btn
    global _clear_btn, _save_btn

    with ui.row().classes("w-full gap-1 items-center mt-2") as _controls_row:
        _move_up_btn = ui.button("↑", on_click=_move_up).classes("text-sm").props("size=sm")
        _move_down_btn = ui.button("↓", on_click=_move_down).classes("text-sm").props("size=sm")
        _remove_btn = ui.button("Remove", on_click=_remove_anchor).classes("text-sm").props("size=sm color=red")
        _add_placeholder_btn = ui.button("Add placeholder", on_click=_add_placeholder).classes("text-sm").props("size=sm")
        _add_selected_track_btn = ui.button("Add selected track", on_click=_add_selected_track).classes("text-sm").props("size=sm color=blue")
        _generate_anchors_btn = ui.button("Generate Anchors", on_click=_on_generate_anchors_clicked).classes("text-sm").props("size=sm color=teal")
        _clear_btn = ui.button("Clear Anchors", on_click=_clear_anchors).classes("text-sm").props("size=sm color=orange")
        _save_btn = ui.button("Save Anchors", on_click=_save_anchors).classes("text-sm").props("size=sm color=green")

    with ui.row().classes("w-full gap-2 items-center mt-2"):
        _set_anchors_input = (
            ui.input(
                label="Track numbers (comma-separated)",
                placeholder="e.g. 3, 17, 42, 108",
            )
            .classes("grow")
        )
        _set_anchors_btn = ui.button(
            "Set These Anchors", on_click=_on_set_anchors,
        ).props("size=sm color=blue-6")

    _refresh_control_buttons()


def _render_anchors_list():
    title_text = f"Anchors for {_playlist_name}" if _playlist_name else "Anchors"

    columns = [
        {"name": "idx", "label": "#", "field": "idx", "sortable": True},
        {"name": "type", "label": "Type", "field": "type"},
        {"name": "info", "label": "Track", "field": "info"},
    ]
    rows = []
    track_by_id = {t["id"]: t for t in _playlist_tracks}
    for i, entry in enumerate(_anchor_plan, 1):
        if entry["type"] == "anchor":
            tid = entry.get("track_id", "")
            track = track_by_id.get(tid, {})
            name = track.get("name", "?")[:40]
            artist = track.get("artist", "?")[:30]
            rows.append({"idx": i, "type": "⚓ Anchor", "info": f"{name} — {artist}"})
        else:
            rows.append({"idx": i, "type": "· Placeholder", "info": "— placeholder —"})

    ui.label(title_text).classes("text-lg font-bold mb-1")
    if not rows:
        ui.label("No anchors yet. Add some below.").classes("text-sm text-gray-400 italic mb-2")
        return

    global _anchors_table
    _anchors_table = ui.table(
        columns=columns, rows=rows, row_key="idx", pagination={"rowsPerPage": 0},
    ).classes("w-full").props("dense")
    _anchors_table.on("rowClick", _on_anchor_row_click)


def _render_track_list():
    if not _playlist_tracks:
        ui.label("No tracks loaded.").classes("text-sm text-gray-400 italic")
        return

    anchored_ids = _anchored_track_ids()
    rows = build_track_rows(_playlist_tracks, anchored_ids=anchored_ids)

    columns = [
        {"name": "idx", "label": "#", "field": "idx", "sortable": True},
        {"name": "name", "label": "Track", "field": "name"},
        {"name": "artist", "label": "Artist", "field": "artist"},
        {"name": "duration", "label": "Dur", "field": "duration"},
        {"name": "desc", "label": "Desc", "field": "desc", "sortable": False},
        {"name": "status", "label": "Status", "field": "status"},
    ]

    ui.label(f"Playlist: {_playlist_name} ({len(_playlist_tracks)} tracks)").classes("text-sm font-semibold mb-1")

    global _track_table
    _track_table = ui.table(
        columns=columns, rows=rows, row_key="idx",
        selection="single",
        pagination={"rowsPerPage": 0},
        on_select=_on_track_selection_change,
    ).classes("w-full").props("dense")

    _track_table.add_slot("body-cell-desc", r"""
    <q-td :props="props">
      <span class="desc-icon-container">
        <q-icon :name="props.row.desc_icon" :color="props.row.desc_color" size="18px"
                style="cursor: pointer;"
                @click.stop="() => $parent.$emit('desc_click', props.row)" />
        <span class="desc-icon-tip">{{ props.row.desc_caption }}</span>
      </span>
    </q-td>
    """)

    from playlist_arranger.ui.pages.playlist_source import _on_desc_icon_click
    _track_table.on("desc_click", _on_desc_icon_click)
    _track_table.on("rowDblclick", _on_track_double_click)
    _track_table.on("rowClick", _on_track_row_click)


def _on_playlist_selected(pl_id: str):
    global _anchor_plan, _playlist_tracks, _playlist_name
    global _selected_anchor_idx
    global _gen_last_n

    _state.selected_playlist_id = pl_id

    if not pl_id:
        return

    _anchor_plan = _load_anchors_file(pl_id) or [{"type": "placeholder"}]

    from playlist_arranger.ui.pages.playlist_source import _load_cached_playlist_tracks
    try:
        _playlist_tracks = _load_cached_playlist_tracks(pl_id)
    except Exception as exc:
        logger.exception("Failed to load tracks for playlist %s", pl_id[:8])
        ui.notify(f"Failed to load tracks: {exc}", type="negative")
        _playlist_tracks = []

    _playlist_name = ""
    try:
        pl_data = _state.sp.playlist(pl_id, fields="name")
        _playlist_name = pl_data.get("name", pl_id[:8])
    except Exception:
        _playlist_name = pl_id[:8]

    _selected_anchor_idx = None

    # Reset Generate Anchors N on playlist switch — a fixed N from a
    # 10-track playlist doesn't make sense for a 200-track playlist.
    _gen_last_n = None

    _rebuild_anchors_list()
    _rebuild_track_list()
    _refresh_control_buttons()


def build_anchors():
    global _anchor_plan, _playlist_tracks, _playlist_name
    global _selected_anchor_idx
    global _anchors_select, _anchors_list_container, _track_list_container
    global _gen_panel, _gen_panel_visible

    ui.label("Anchors").classes("text-2xl font-bold mb-4")

    if _state.sp is None or not _state.spotify_user_id:
        ui.label("Connect Spotify to view your playlists.").classes("text-red-500")
        return

    try:
        playlists = get_own_playlists(_state.sp, _state.spotify_user_id)
    except Exception as exc:
        ui.label(f"Failed to load playlists: {exc}").classes("text-red-500")
        return

    if not playlists:
        ui.label("No playlists found.").classes("text-gray-500")
        return

    options = {pl["id"]: pl["name"] for pl in playlists}

    saved_id = getattr(_state, "selected_playlist_id", None)
    default_val = saved_id if saved_id in options else (list(options.keys())[0] if options else None)

    def on_change(e):
        _on_playlist_selected(e.value)

    _anchors_select = ui.select(
        label="Select a playlist", options=options, value=default_val, on_change=on_change,
    ).classes("w-80 mb-4")

    _anchors_list_container = ui.column().classes("w-full mb-2")
    with _anchors_list_container:
        _render_anchors_list()

    _render_controls()

    # ── Generate Anchors panel (collapsible, between controls and track list) ──
    _gen_panel = ui.column().classes("w-full mb-2 p-3 border rounded")
    # Hide initially
    _gen_panel_visible = False

    ui.separator().classes("my-4")
    _track_list_container = ui.column().classes("w-full")
    with _track_list_container:
        _render_track_list()

    if default_val:
        _on_playlist_selected(default_val)