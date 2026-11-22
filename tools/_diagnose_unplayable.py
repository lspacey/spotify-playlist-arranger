"""Diagnostic: inspect raw Spotify API fields for playability analysis.

Usage: python tools/_diagnose_unplayable.py [playlist_id]

Fetches the first page of a playlist WITH and WITHOUT market="from_token",
printing raw is_playable/available_markets/restrictions for each track.
"""
import sys
sys.path.insert(0, "e:/Projects/Spotify_playlists/repository")

import os
import json
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from playlist_arranger.config import SPOTIFY_SCOPE, REDIRECT_URI

playlist_id = sys.argv[1] if len(sys.argv) > 1 else "6dwYnWPwm7KgFWO6e0wtdd"


def fetch_and_inspect(market_param=None):
    sp = spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            scope=SPOTIFY_SCOPE,
            redirect_uri=REDIRECT_URI,
            open_browser=False,
        )
    )
    kwargs = {"limit": 25, "offset": 0}
    label = "WITHOUT market"
    if market_param:
        kwargs["market"] = market_param
        label = f"WITH market={market_param!r}"

    result = sp.playlist_items(playlist_id, **kwargs)
    items = result.get("items") or []

    print(f"\n{'='*70}")
    print(f"Playlist: {playlist_id} — {label}")
    print(f"Total items in page: {len(items)}")
    print(f"{'='*70}")

    skip_reasons = {
        "null_item": 0,
        "null_track": 0,
        "is_local": 0,
        "not_track_type": 0,
        "no_id": 0,
        "is_playable_False": 0,
        "restrictions": 0,
        "available_markets_empty_no_is_playable": 0,
        "OK": 0,
    }

    for idx, item in enumerate(items[:15]):
        if not item:
            skip_reasons["null_item"] += 1
            print(f"  [{idx}] NULL item")
            continue

        t = item.get("track")
        name = (t or {}).get("name", "?")[:50] if t else "NULL"
        tid = (t or {}).get("id", "") if t else ""

        print(f"  [{idx}] {name} (id={tid[:12] if tid else 'NONE'})")
        print(f"       is_local={item.get('is_local')}")
        if t:
            print(f"       type={t.get('type')}")
            print(f"       is_playable={t.get('is_playable')!r}")
            print(f"       available_markets={t.get('available_markets')!r}")
            print(f"       restrictions={t.get('restrictions')!r}")
        else:
            print(f"       track=NULL")

        # Apply same filter logic as _is_track_playable to see what would happen
        if not t:
            skip_reasons["null_track"] += 1
            print(f"       → WOULD SKIP: null track")
            continue
        if item.get("is_local"):
            skip_reasons["is_local"] += 1
            print(f"       → WOULD SKIP: is_local")
            continue
        if t.get("type") != "track":
            skip_reasons["not_track_type"] += 1
            print(f"       → WOULD SKIP: type={t.get('type')}")
            continue
        if not tid:
            skip_reasons["no_id"] += 1
            print(f"       → WOULD SKIP: no id")
            continue

        # _is_track_playable logic
        if t.get("is_playable") is False:
            skip_reasons["is_playable_False"] += 1
            print(f"       → WOULD SKIP: is_playable=False")
            continue
        if t.get("restrictions"):
            skip_reasons["restrictions"] += 1
            print(f"       → WOULD SKIP: restrictions={t.get('restrictions')}")
            continue
        if "is_playable" not in t:
            am = t.get("available_markets")
            if am is not None and len(am) == 0:
                skip_reasons["available_markets_empty_no_is_playable"] += 1
                print(f"       → WOULD SKIP: available_markets=[] (no is_playable)")
                continue
        skip_reasons["OK"] += 1
        print(f"       → WOULD KEEP")

    print(f"\n  Summary:")
    for reason, count in skip_reasons.items():
        if count > 0:
            print(f"    {reason}: {count}")
    total_skipped = sum(v for k, v in skip_reasons.items() if k != "OK")
    print(f"    Total would-skip: {total_skipped}")
    print(f"    Total would-keep: {skip_reasons['OK']}")

    return skip_reasons


if __name__ == "__main__":
    # Test WITH market="from_token" first (current production behavior)
    with_market = fetch_and_inspect("from_token")

    # Then test WITHOUT market param (to isolate whether market=from_token causes bad data)
    without_market = fetch_and_inspect(None)

    print(f"\n{'='*70}")
    print("DIFFERENCE ANALYSIS")
    print(f"{'='*70}")
    print(f"With market='from_token':   {with_market['OK']} kept / {sum(v for k,v in with_market.items() if k!='OK')} skipped")
    print(f"Without market param:       {without_market['OK']} kept / {sum(v for k,v in without_market.items() if k!='OK')} skipped")
    print(f"Delta (without→with):       {without_market['OK'] - with_market['OK']} fewer tracks kept")