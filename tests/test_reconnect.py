"""
Tests for WebSocket disconnect handling and stale-client guards (Issue #fix).

Validates:
1. _on_analysis_complete is a no-op when has_socket_connection is False
2. _on_analysis_complete proceeds normally when has_socket_connection is True
3. _on_desc_generated skips UI when page_client is stale
4. Source-inspection: all background→UI callbacks have has_socket_connection guard
5. Simulated disconnect mid-analysis: no exception, queue state correctly updated
"""

import threading
import logging

import pytest

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MockClient:
    """Minimal mock of a NiceGUI client with has_socket_connection support."""

    def __init__(self, *, has_socket_connection: bool = True):
        self.has_socket_connection = has_socket_connection
        self._was_entered = False

    def __enter__(self):
        self._was_entered = True
        return self

    def __exit__(self, *args):
        return False


# ---------------------------------------------------------------------------
# Test 1: _on_analysis_complete skips UI when has_socket_connection=False
# ---------------------------------------------------------------------------

def test_analysis_complete_skips_ui_on_disconnected_client(monkeypatch):
    """When page_client.has_socket_connection is False, _on_analysis_complete
    must NOT call ui.notify() and must NOT enter the client context."""
    import playlist_arranger.ui.pages.playlist_source as ps
    from playlist_arranger.ui import state as _state
    from playlist_arranger.ui import playlist_highlight as _ph

    # Preserve original state
    orig_page_client = _ph._page_client
    orig_queue = list(_state.analysis_queue)
    orig_current_id = _state.analysis_current_track_id

    try:
        # Setup: mock page_client with dead connection
        dead_client = MockClient(has_socket_connection=False)
        _ph._page_client = dead_client

        # Setup: add a track to the queue
        track = {"id": "test_dead_001", "name": "Dead Track", "artist": "Dead Artist"}
        _state.analysis_queue.append(track)
        _state.analysis_current_track_id = "test_dead_001"

        # Track whether ui.notify was called
        notify_calls = []
        monkeypatch.setattr(ps.ui, "notify", lambda *a, **kw: notify_calls.append((a, kw)))

        # Track whether rebuild was called
        rebuild_calls = []
        monkeypatch.setattr(ps, "_rebuild_queue_ui", lambda: rebuild_calls.append(1))

        # Call _on_analysis_complete
        ps._on_analysis_complete(track)

        # Verify: track was removed from queue (state mutation still happens)
        remaining = [t for t in _state.analysis_queue if t.get("id") == "test_dead_001"]
        assert len(remaining) == 0, "Track should be removed from queue even when disconnected"

        # Verify: ui.notify was NOT called
        assert len(notify_calls) == 0, (
            f"ui.notify must not be called on dead client, got {len(notify_calls)} calls")

        # Verify: rebuild_queue_ui was NOT called
        assert len(rebuild_calls) == 0, (
            f"_rebuild_queue_ui must not be called on dead client, got {len(rebuild_calls)} calls")

        # Verify: client context was NOT entered
        assert not dead_client._was_entered, "Client context must not be entered on dead client"

    finally:
        _ph._page_client = orig_page_client
        _state.analysis_queue[:] = orig_queue
        _state.analysis_current_track_id = orig_current_id


# ---------------------------------------------------------------------------
# Test 2: _on_analysis_complete proceeds normally when connected
# ---------------------------------------------------------------------------

def test_analysis_complete_proceeds_on_connected_client(monkeypatch):
    """When page_client.has_socket_connection is True, _on_analysis_complete
    must call ui.notify() and rebuild the queue UI."""
    import playlist_arranger.ui.pages.playlist_source as ps
    from playlist_arranger.ui import state as _state
    from playlist_arranger.ui import playlist_highlight as _ph

    orig_page_client = _ph._page_client
    orig_queue = list(_state.analysis_queue)
    orig_current_id = _state.analysis_current_track_id

    try:
        live_client = MockClient(has_socket_connection=True)
        _ph._page_client = live_client

        track = {"id": "test_live_001", "name": "Live Track", "artist": "Live Artist"}
        _state.analysis_queue.append(track)
        _state.analysis_current_track_id = "test_live_001"

        notify_calls = []
        monkeypatch.setattr(ps.ui, "notify", lambda *a, **kw: notify_calls.append((a, kw)))

        rebuild_calls = []
        monkeypatch.setattr(ps, "_rebuild_queue_ui", lambda: rebuild_calls.append(1))

        ps._on_analysis_complete(track)

        # Track removed
        remaining = [t for t in _state.analysis_queue if t.get("id") == "test_live_001"]
        assert len(remaining) == 0

        # ui.notify WAS called
        assert len(notify_calls) >= 1, "ui.notify must be called on live client"

        # rebuild WAS called
        assert len(rebuild_calls) >= 1, "_rebuild_queue_ui must be called on live client"

        # Client context WAS entered
        assert live_client._was_entered, "Client context must be entered on live client"

    finally:
        _ph._page_client = orig_page_client
        _state.analysis_queue[:] = orig_queue
        _state.analysis_current_track_id = orig_current_id


# ---------------------------------------------------------------------------
# Test 3: _on_desc_generated skips UI when page_client is stale
# ---------------------------------------------------------------------------

def test_desc_generated_skips_ui_on_disconnected_client(monkeypatch):
    """When page_client.has_socket_connection is False, _on_desc_generated
    must NOT call ui.notify() and must NOT touch UI elements."""
    import playlist_arranger.ui.pages.playlist_source as ps
    from playlist_arranger.ui import playlist_highlight as _ph

    orig_page_client = _ph._page_client

    try:
        dead_client = MockClient(has_socket_connection=False)
        _ph._page_client = dead_client

        notify_calls = []
        monkeypatch.setattr(ps.ui, "notify", lambda *a, **kw: notify_calls.append((a, kw)))

        rebuild_calls = []
        monkeypatch.setattr(ps, "_rebuild_queue_ui", lambda: rebuild_calls.append(1))

        ps._on_desc_generated("test_track_001")

        assert len(notify_calls) == 0, "ui.notify must not be called on dead client"
        assert len(rebuild_calls) == 0, "_rebuild_queue_ui must not be called on dead client"
        assert not dead_client._was_entered, "Client context must not be entered on dead client"

    finally:
        _ph._page_client = orig_page_client


# ---------------------------------------------------------------------------
# Test 4: Source-inspection — verify all bg→UI callbacks have guard
# ---------------------------------------------------------------------------

def test_all_bg_to_ui_callbacks_have_socket_connection_guard():
    """Every background-thread-to-UI callback that uses _ph._page_client must
    check has_socket_connection before entering the with-block."""
    import ast
    import pathlib

    src_path = pathlib.Path(__file__).parent.parent / "playlist_arranger" / "ui" / "pages" / "playlist_source.py"
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Find all functions that reference _ph._page_client and contain 'with'
    functions_with_page_client = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            func_source = ast.get_source_segment(source, node) or ""
            if "_ph._page_client" in func_source and "_ui_context_lock" in func_source:
                functions_with_page_client.append(node.name)

    # These are the known functions that use _ph._page_client from bg threads:
    expected_callbacks = {"_on_analysis_complete", "_on_desc_generated"}
    found = set(functions_with_page_client) & expected_callbacks

    # Verify each callback has the guard pattern
    for func_name in found:
        func_node = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                func_node = node
                break
        assert func_node is not None, f"Function {func_name} not found"

        func_source = ast.get_source_segment(source, func_node) or ""
        # Check for the guard pattern: has_socket_connection check before with-block
        has_guard = (
            "has_socket_connection" in func_source
            and "_ph._page_client is not None" in func_source
        )
        assert has_guard, (
            f"Function {func_name} must check has_socket_connection before "
            f"entering the _ph._page_client with-block.\n"
            f"Source snippet:\n{func_source[:500]}"
        )

    assert len(found) >= len(expected_callbacks), (
        f"Expected {len(expected_callbacks)} callbacks to have guards, found {len(found)}: {found}"
    )


# ---------------------------------------------------------------------------
# Test 5: Disconnect mid-analysis — no exception, state updated correctly
# ---------------------------------------------------------------------------

def test_disconnect_mid_analysis_no_exception(monkeypatch):
    """Calling _on_analysis_complete with a dead page_client must not raise
    an exception, and the analysis queue must still be correctly updated."""
    import playlist_arranger.ui.pages.playlist_source as ps
    from playlist_arranger.ui import state as _state
    from playlist_arranger.ui import playlist_highlight as _ph

    orig_page_client = _ph._page_client
    orig_queue = list(_state.analysis_queue)
    orig_current_id = _state.analysis_current_track_id

    try:
        # Simulate a client that disconnected mid-session
        dead_client = MockClient(has_socket_connection=False)
        _ph._page_client = dead_client

        # Queue with multiple tracks — simulate batch in progress
        tracks = [
            {"id": "batch_001", "name": "Track 1", "artist": "A1"},
            {"id": "batch_002", "name": "Track 2", "artist": "A2"},
            {"id": "batch_003", "name": "Track 3", "artist": "A3"},
        ]
        _state.analysis_queue[:] = list(tracks)
        _state.analysis_current_track_id = "batch_002"

        # Suppress all UI calls
        monkeypatch.setattr(ps.ui, "notify", lambda *a, **kw: None)
        monkeypatch.setattr(ps, "_rebuild_queue_ui", lambda: None)

        # Call should NOT raise
        try:
            ps._on_analysis_complete(tracks[1])  # complete track 2
        except Exception as e:
            pytest.fail(f"_on_analysis_complete raised {type(e).__name__}: {e}")

        # Verify track 2 was removed from queue
        remaining_ids = [t.get("id") for t in _state.analysis_queue]
        assert "batch_002" not in remaining_ids, (
            f"Track batch_002 should be removed from queue, got {remaining_ids}")
        assert "batch_001" in remaining_ids, "Track batch_001 should still be in queue"
        assert "batch_003" in remaining_ids, "Track batch_003 should still be in queue"

    finally:
        _ph._page_client = orig_page_client
        _state.analysis_queue[:] = orig_queue
        _state.analysis_current_track_id = orig_current_id