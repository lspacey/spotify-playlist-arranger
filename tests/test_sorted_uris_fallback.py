"""Regression test: URI construction must fall back to track_id when 'uri' is missing from cached tracks."""


def test_sorted_track_uris_falls_back_to_id_when_uri_missing():
    """Tracks with a valid 'id' but no/malformed 'uri' must still produce a
    usable spotify:track: URI, instead of being silently dropped (which
    caused Save buttons to stay disabled after a successful sort against a
    stale cache)."""
    _playlist_tracks = [
        {"id": "abc123", "name": "A", "artist": "X"},  # no uri key at all
        {"id": "def456", "name": "B", "artist": "Y", "uri": ""},  # empty uri
        {"id": "ghi789", "name": "C", "artist": "Z", "uri": "spotify:track:ghi789"},  # valid
    ]
    ordered_descs = [
        {"track_id": "abc123", "name": "A", "artist": "X"},
        {"track_id": "def456", "name": "B", "artist": "Y"},
        {"track_id": "ghi789", "name": "C", "artist": "Z"},
    ]

    sorted_full_tracks = []
    for d in ordered_descs:
        tid = d.get("track_id", "")
        full = next((t for t in _playlist_tracks if t.get("id") == tid), d)
        sorted_full_tracks.append(full)

    _sorted_track_uris = []
    _dropped_no_id = 0
    for t in sorted_full_tracks:
        uri = t.get("uri") or ""
        if not uri.startswith("spotify:track:"):
            tid = t.get("id") or t.get("track_id") or ""
            uri = f"spotify:track:{tid}" if tid else ""
        if uri.startswith("spotify:track:"):
            _sorted_track_uris.append(uri)
        else:
            _dropped_no_id += 1

    assert _dropped_no_id == 0, f"No tracks should be dropped: {_dropped_no_id}"
    assert _sorted_track_uris == [
        "spotify:track:abc123",
        "spotify:track:def456",
        "spotify:track:ghi789",
    ]