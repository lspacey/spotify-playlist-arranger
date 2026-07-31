# Active Context — Spotify Playlist Arranger

## Current Work Focus
Smart Sorting page post-save behavior improvements, RuntimeError fixes, and stale-reference cleanup on page navigation.

## Recent Changes (2026-07-30)

### Smart Sorting Page — Post-Save Behavior
- **Bug fix**: Applied `client = ui.context.client` capture + `with client:` guard pattern (systemPattern #1) to `_on_save_new_playlist` and `_on_save_current_playlist._execute()`. Fixes RuntimeError when `ui.notify()` is called after `await asyncio.to_thread()` resumes with no active slot context.
- **Feature**: `_refresh_playlist_dropdown()` re-fetches user playlists from Spotify and updates the select dropdown in-place after creating a new playlist (preserves current selection).
- **Feature**: `_refresh_current_playlist_tracks()` re-fetches playlist tracks from Spotify after overwrite, writes the new cache file with snapshot_id, and triggers `_rebuild_all()` to redraw the track list.

### Smart Sorting Page — Stale-Reference Cleanup
- **Bug fix**: `build_smart_sorting()` now resets ALL 18 module-level UI element globals to `None` at its top, before any new element creation. Cancels leaked `_log_timer` from a previous page instance. Fixes "element has been deleted but is still being used" RuntimeError when navigating away and back to the Sorting page while sorted results were showing.
- **Defense-in-depth**: `_refresh_save_buttons()` and `_refresh_buttons()` now wrap all `.set_enabled()` / `.set_visibility()` / `.set_text()` calls in `try/except RuntimeError` guards, logging at DEBUG level and continuing gracefully if a stale reference survives the explicit reset.

### Test Suite
- Created `tests/test_smart_sorting.py` with 19 tests covering: client-context guards, playlist dropdown refresh, track cache refresh, notify ordering, dropdown selection preservation, stale-reference cleanup, and defensive error handling.
- Full suite: 363 passed, 0 failed.

## Next Steps
- Monitor for any remaining stale-reference issues on other pages (Spotify Source, Anchors, Stats, Batch Analysis)
- Consider applying the same `client = ui.context.client` pattern to `_on_start_sorting` and `_on_insert_last_n` if issues arise
- Track any leftover nicegui "Client has been deleted" warning patterns in production logs
- Verify pagination persistence works correctly in real browser usage with large playlists
- Monitor production logs for low-signal status flagging false positives on tracks with unusually quiet intros

## Active Decisions
- **Pattern #1 (capture client before await)** is now the DEFAULT for all new async handler code in this project
- **Defensive RuntimeError guards** on UI element mutation functions are now a standard second line of defense against stale references
- **Explicit reset of all UI globals at build entry** is required for any page that lazily creates UI elements (not all in the build function)

## Important Patterns
- `smart_sorting.py` now has 18 module-level UI globals (up from ~10 before the cleanup pass). All are reset to None at the top of `build_smart_sorting()`.
- `tests/test_smart_sorting.py` uses source-inspection tests (`_SRC_PATH.read_text()`) for order verification and module-level mock-replacement tests for behavioral verification.
- See `systemPatterns.md` patterns #1 (UI context re-entry), #34 (stale-reference cleanup), and #35 (defensive UI guards).