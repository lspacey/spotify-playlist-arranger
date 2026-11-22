"""Append the 2 rebuild_queue_ui tests to test_batch_analysis.py."""
BA = r"e:\Projects\Spotify_playlists\repository\tests\test_batch_analysis.py"

with open(BA, "r", encoding="utf-8") as f:
    content = f.read()

new_tests = '''

def test_rebuild_queue_ui_skips_when_client_disconnected():
    """_rebuild_queue_ui skips when client.has_socket_connection is False."""
    from unittest.mock import patch, MagicMock
    from playlist_arranger.ui.pages import playlist_source as _ps

    class FakeClient:
        has_socket_connection = False

    fake_container = MagicMock()
    fake_container.client = FakeClient()
    fake_container.clear = MagicMock()

    old_container = _ps._queue_container
    from playlist_arranger.ui import analysis_queue as _aq
    old_aq_container = _aq._queue_container
    try:
        _ps._queue_container = fake_container
        _aq._queue_container = fake_container
        _ps._rebuild_queue_ui()
        # .clear() must NOT have been called
        fake_container.clear.assert_not_called()
    finally:
        _ps._queue_container = old_container
        _aq._queue_container = old_aq_container


def test_rebuild_queue_ui_proceeds_when_client_connected():
    """_rebuild_queue_ui proceeds when client.has_socket_connection is True."""
    from unittest.mock import patch, MagicMock
    from playlist_arranger.ui.pages import playlist_source as _ps

    class FakeClient:
        has_socket_connection = True

    fake_container = MagicMock()
    fake_container.client = FakeClient()
    fake_container.clear = MagicMock()

    old_container = _ps._queue_container
    old_batch_btn = _ps._ba._batch_btn
    from playlist_arranger.ui import analysis_queue as _aq
    old_aq_container = _aq._queue_container
    try:
        _ps._queue_container = fake_container
        _aq._queue_container = fake_container
        # Avoid crashing in _render_queue_table/_render_queue_controls
        # by ensuring the container context manager works and the
        # batch button refresh doesn't crash on a None button.
        _ps._ba._batch_btn = None
        _ps._rebuild_queue_ui()
        # .clear() must have been called (guard passed)
        fake_container.clear.assert_called_once()
    finally:
        _ps._queue_container = old_container
        _ps._ba._batch_btn = old_batch_btn
        _aq._queue_container = old_aq_container
'''

content = content.rstrip() + new_tests
with open(BA, "w", encoding="utf-8") as f:
    f.write(content)

print("Done - appended 2 tests to test_batch_analysis.py")

# Verify
import subprocess
result = subprocess.run(
    [r"e:\Projects\Spotify_playlists\repository\venv\Scripts\python.exe", "-m", "pytest", BA, "--collect-only", "-q"],
    capture_output=True, text=True
)
last = [l for l in result.stdout.split('\n') if 'tests collected' in l]
if last:
    print(f"  {last[0].strip()}")