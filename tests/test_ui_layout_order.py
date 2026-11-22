"""
Source-inspection tests verifying action buttons appear ABOVE their tables
in the Spotify page's playlist and queue sections.

This ensures users with long playlists/queues don't have to scroll past
hundreds of rows to reach the controls.
"""

import ast
import pathlib


# ---------------------------------------------------------------------------
# Test 1: show_track_compact_table — button before table
# ---------------------------------------------------------------------------

def test_track_table_button_before_table():
    """In show_track_compact_table(), the "Add Selected Tracks to Queue
    for Analysis" button row must be rendered BEFORE the ui.table() call."""
    src_path = (
        pathlib.Path(__file__).parent.parent
        / "playlist_arranger" / "ui" / "track_table.py"
    )
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    func_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "show_track_compact_table":
            func_node = node
            break
    assert func_node is not None, "show_track_compact_table function not found"

    func_source = ast.get_source_segment(source, func_node) or ""
    lines = func_source.split("\n")

    button_line = None
    table_line = None
    for i, line in enumerate(lines):
        if "Add Selected Tracks to Queue for Analysis" in line.strip():
            button_line = i
        if line.strip().startswith("track_table = ui.table("):
            table_line = i

    if table_line is None:
        # Fallback: find ui.table( preceded by a variable assignment
        for i, line in enumerate(lines):
            if "= ui.table(" in line.strip():
                table_line = i
                break

    assert button_line is not None, (
        "Could not find 'Add Selected Tracks to Queue for Analysis' button "
        "in show_track_compact_table() source"
    )
    assert table_line is not None, (
        "Could not find 'track_table = ui.table(' call "
        "in show_track_compact_table() source"
    )

    assert button_line < table_line, (
        f"'Add Selected Tracks' button row (line {button_line + 1}) must appear "
        f"BEFORE the track table (line {table_line + 1}) in "
        f"show_track_compact_table(). "
        f"Current ordering is reversed — users must scroll past the table "
        f"to find the button."
    )


# ---------------------------------------------------------------------------
# Test 2: render_analysis_queue — controls before table
# ---------------------------------------------------------------------------

def test_analysis_queue_controls_before_table():
    """In render_analysis_queue(), _render_queue_controls_fn() must be
    called BEFORE render_queue_table()."""
    src_path = (
        pathlib.Path(__file__).parent.parent
        / "playlist_arranger" / "ui" / "analysis_queue.py"
    )
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    func_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "render_analysis_queue":
            func_node = node
            break
    assert func_node is not None, "render_analysis_queue function not found"

    func_source = ast.get_source_segment(source, func_node) or ""
    lines = func_source.split("\n")

    controls_line = None
    table_line = None
    for i, line in enumerate(lines):
        if "_render_queue_controls_fn()" in line.strip():
            controls_line = i
        if "render_queue_table()" in line.strip():
            table_line = i

    assert controls_line is not None, (
        "Could not find _render_queue_controls_fn() call "
        "in render_analysis_queue() source"
    )
    assert table_line is not None, (
        "Could not find render_queue_table() call "
        "in render_analysis_queue() source"
    )

    assert controls_line < table_line, (
        f"_render_queue_controls_fn() (line {controls_line + 1}) must be called "
        f"BEFORE render_queue_table() (line {table_line + 1}) in "
        f"render_analysis_queue(). Current ordering is reversed."
    )


# ---------------------------------------------------------------------------
# Test 3: rebuild_queue_ui — controls before table
# ---------------------------------------------------------------------------

def test_rebuild_queue_ui_controls_before_table():
    """In rebuild_queue_ui(), _render_queue_controls_fn() must be called
    BEFORE render_queue_table()."""
    src_path = (
        pathlib.Path(__file__).parent.parent
        / "playlist_arranger" / "ui" / "analysis_queue.py"
    )
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    func_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "rebuild_queue_ui":
            func_node = node
            break
    assert func_node is not None, "rebuild_queue_ui function not found"

    func_source = ast.get_source_segment(source, func_node) or ""
    lines = func_source.split("\n")

    controls_line = None
    table_line = None
    for i, line in enumerate(lines):
        if "_render_queue_controls_fn()" in line.strip():
            controls_line = i
        if "render_queue_table()" in line.strip():
            table_line = i

    assert controls_line is not None, (
        "Could not find _render_queue_controls_fn() call "
        "in rebuild_queue_ui() source"
    )
    assert table_line is not None, (
        "Could not find render_queue_table() call "
        "in rebuild_queue_ui() source"
    )

    assert controls_line < table_line, (
        f"_render_queue_controls_fn() (line {controls_line + 1}) must be called "
        f"BEFORE render_queue_table() (line {table_line + 1}) in "
        f"rebuild_queue_ui(). Current ordering is reversed."
    )