"""Standalone Spotify playback probe.

Usage (from the repo root, venv active):

    python -m tools.spotify_playback_probe <TRACK_ID>

Purpose: test programmatic playback without launching the NiceGUI app, with raw
player-command tracing forced on.  Reuses the project's existing OAuth cache and
scopes via ``init_spotify()``.  Never prints tokens.
"""

import sys
import time


def _probe(track_id):
    from playlist_arranger.sources import spotify_source as ss
    from playlist_arranger.ui import state as st

    # Force raw player-command tracing on regardless of env.
    ss.SPOTIFY_TRACE = True

    print("Connecting to Spotify (reusing cached OAuth) ...")
    sp, user_id = ss.init_spotify()
    print(f"Account id       : {user_id}")
    print(f"Account country  : {st.spotify_market}")
    print(f"Account product  : {st.spotify_product}")
    if st.spotify_product and st.spotify_product != "premium":
        print("!!! WARNING: product is not 'premium' — programmatic playback will fail "
              "(403 PREMIUM_REQUIRED).")

    devices = sp.devices().get("devices", [])
    print(f"\nDevices ({len(devices)}):")
    for d in devices:
        print(f"  id={d.get('id')} name={d.get('name')!r} type={d.get('type')} "
              f"is_active={d.get('is_active')} is_restricted={d.get('is_restricted')} "
              f"volume_percent={d.get('volume_percent')}")

    if not devices:
        print("\nNo devices found — start a Spotify client first.")
        return 1

    device_id = None
    for d in devices:
        if d.get("is_active") and not d.get("is_restricted"):
            device_id = d["id"]
            break
    if device_id is None:
        for d in devices:
            if not d.get("is_restricted"):
                device_id = d["id"]
                break
    if device_id is None:
        print("\nAll devices are restricted — none accept Web API commands.")
        return 1
    print(f"\nUsing device id={device_id}")

    print("\nInitial playback snapshot:")
    print(repr(ss.playback_snapshot(sp)))

    track_uri = f"spotify:track:{track_id}"
    print(f"\nAttempting playback of {track_uri} ...")
    started = False
    try:
        path, snapshot = ss.play_track_on_device(sp, track_uri, device_id=device_id)
        print(f"play_track_on_device succeeded: path={path!r}")
        print(f"final snapshot={snapshot!r}")
        started = True
    except Exception as exc:
        print(f"play_track_on_device FAILED: {exc}")

    print("\nMonitoring playback for 15 s (1 Hz):")
    for i in range(15):
        snap = ss.playback_snapshot(sp)
        print(f"  t+{i:>2}s: {snap!r}")
        time.sleep(1.0)

    if started:
        try:
            ss._player_request(sp, "PUT", "me/player/pause")
            print("\nPaused playback.")
        except Exception as exc:
            print(f"\nCould not pause playback: {exc}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(_probe(sys.argv[1]))
