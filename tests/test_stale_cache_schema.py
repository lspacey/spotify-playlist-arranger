"""Regression test: a cache file without 'uri' fields must trigger a re-fetch."""

import json
import tempfile
import pathlib
from unittest.mock import MagicMock, patch


def test_stale_cache_without_uri_field_triggers_refetch():
    """A cache file whose tracks lack the 'uri' field (written by an older app
    version) must be treated as stale and deleted, forcing a fresh fetch —
    not silently returned as-is."""
    from playlist_arranger import config as cfg
    from playlist_arranger.ui import state as _state

    td = tempfile.mkdtemp(prefix="stale_cache_test_")
    tmp_path = pathlib.Path(td)

    pl_id = "plX"
    snap_id = "snapY"
    stale_cache = tmp_path / f"{pl_id}-{snap_id}.tracks.json"
    stale_cache.write_text(json.dumps([
        {"id": "t1", "name": "A", "artist": "X"},  # missing uri — old schema
    ]))

    fake_sp = MagicMock()
    fake_sp.playlist.return_value = {"snapshot_id": snap_id, "name": "Test PL"}
    _state.sp = fake_sp

    fresh_tracks = [{"id": "t1", "name": "A", "artist": "X", "uri": "spotify:track:t1"}]

    import playlist_arranger.ui.pages.playlist_source as ps

    with patch.object(cfg, "CACHE_DIR_DEFAULT", tmp_path), \
         patch.object(ps, "CACHE_DIR_DEFAULT", tmp_path), \
         patch("playlist_arranger.sources.spotify_source.get_playlist_tracks",
               return_value=fresh_tracks), \
         patch("playlist_arranger.sources.spotify_source._is_track_playable",
               return_value=True), \
         patch.object(ps, "_glob") as mock_glob:
        mock_glob.glob.return_value = []
        result = ps._load_cached_playlist_tracks(pl_id)

    # The stale file should have been deleted (or at least not returned as-is)
    if stale_cache.exists():
        reloaded = json.loads(stale_cache.read_text())
        original = [{"id": "t1", "name": "A", "artist": "X"}]
        # If the file still exists, its content must have changed (re-written with uri)
        assert json.dumps(reloaded) != json.dumps(original), (
            "Stale schema cache must be deleted/replaced, not reused as-is"
        )
    assert all("uri" in t and t["uri"].startswith("spotify:track:") for t in result), (
        f"Fresh tracks must have valid URIs: {result}"
    )

    # Cleanup
    import shutil
    shutil.rmtree(td, ignore_errors=True)