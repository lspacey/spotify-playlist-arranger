# Progress

## What Works
- ✅ Spotify OAuth authentication and playlist fetching
- ✅ WASAPI loopback audio capture with device persistence (by name)
- ✅ Audio feature extraction (BPM, key, loudness, spectral features, MFCC, chroma, frequency balance)
- ✅ MERT neural embedding generation (768-dim) with GPU acceleration
- ✅ SQLite database for persistent track storage
- ✅ LLM-based track description generation (Ollama, DeepSeek, Mistral) — now DB-backed via background worker queue
- ✅ AI anchor selection with 10 playlist structure types
- ✅ Manual anchor editor (add, delete, reorder, placeholders)
- ✅ Simulated annealing ATSP solver with anchor constraints
- ✅ Camelot wheel harmonic key mixing distance
- ✅ Artist/album separation penalties
- ✅ Playlist backup before reordering
- ✅ Save sorted playlist to Spotify (new or existing)
- ✅ Local file scanning (MP3, FLAC) with M3U import/export
- ✅ NiceGUI web interface with sidebar navigation
- ✅ Dark/light mode toggle
- ✅ Rotating file logging (DEBUG level to file, INFO to console)
- ✅ Settings persistence with atomic writes
- ✅ Spotify API retry logic (429/5xx with exponential backoff)
- ✅ Duration tolerance checking for track re-analysis
- ✅ Now Playing card: decoupled Listen (passive monitoring) + Analyze (async buffering/worker)
- ✅ Audio buffer with safety cap (MAX_ANALYZE_BUFFER_S = 900s) preventing unbounded memory growth
- ✅ Async worker with while-loop queue (maxsize=1, no recursion/stack growth risk)
- ✅ Analyzer bug fixes: mid-track init (BUG 1), stop-cascade cleanup (BUG 2), wrong track metadata on flush (BUG 3)
- ✅ Comprehensive debug logging: button clicks, track transitions, worker lifecycle timing, highlight gates
- ✅ Regression test suite: `tests/_test_buffer_lifecycle.py` with 5 tests
- ✅ Spotify API call counter: `SpotifyCallProxy` in `spotify_source.py`, displayed as "🔄 X API calls" next to Connect button
- ✅ Compact audio spectrum visualizer: 120×60 canvas (white bg, gray LED-style bars), 10 log-spaced FFT bands, SR/RMS/Pk stats with fixed-width layout, silence gate preventing -240dB placeholder values, 300ms update cadence
- ✅ Pause-aware audio collection: `_is_playing` threading.Event gate in `LiveAnalyzeContext`, set by polling thread — prevents silence pollution during pause without reintroducing RMS filter
- ✅ Two-column layout rebalanced to 30%/70% (left: Spotify connect/device, right: audio device + visualizer + Now Playing) for wider right column
- ✅ Batch analysis execution with interference detection, watchdog, RLocks, auto-remove, live queue highlighting
- ✅ Description generation queue infrastructure + desc status UI row (under Now Playing card)
- ✅ Per-track description dialog with live-update (DB-backed, updates in real-time when worker finishes)
- ✅ Description generation buttons in playlist tables + queue table (selected tracks + all tracks)
- ✅ Old monolithic `descriptions.py` removed; `load_descriptions`/`save_descriptions` removed from cache/store

## What's Left to Build
- [x] **Queue for Analysis feature** — expandable section above "Your Playlists", populated via per-playlist "Add Selected Tracks to Queue for Analysis" button (replaces "Analyze X missing"). Persists to `cache/analysis_queue.json`. Fully isolated from now-playing highlight system. (2026-07-16)
- [x] **Batch analysis execution logic** — Real sequential playback via `_batch_advance_to_next()` with snapshot+position sequencer, interference detection (`_batch_expected_track_id`), watchdog timer, live queue rendering, thread-safety (RLock), auto-remove analyzed tracks from queue. (2026-07-17/18)
- [x] **Description generation queue + background worker** — Thread-safe FIFO queue with daemon worker, dedupe, UI buttons, status row, live dialog updates. Old descriptions.py removed. (2026-07-21/22)
- [x] Expand test suite: 6 Analyze-mode regression tests + ~50 batch-specific regression tests + 36 desc generator tests (125 total)
- [x] Production-clean verification: `tests/_verify_production_clean.py` ensures `hasattr` checks return False in production
- [x] **Multi-backend LLM dropdown in Generate Anchors panel** — user can switch between Ollama, DeepSeek, Mistral from dropdown; backend override passed through to `_init_llm_client()` (2026-07-24)
- [x] **Per-backend client cache** — dict by backend name prevents stale cache when switching backends (2026-07-24)
- [x] **Session-level panel persistence** — 5 `_gen_last_*` globals remember Generate Anchors panel selections across collapse/expand (in-memory, not disk) (2026-07-24)
- [ ] Wire `desc_generator` worker to actually call LLM (placeholder currently)
- [ ] Wire `run_descriptions()` to `desc_queue_add_many()` + populate `current_descs` from DB
- [ ] Cross-platform audio capture (macOS/Linux support)
- [ ] Batch LLM description generation optimization (concurrent API calls)
- [ ] Export sorted playlist as M3U with relative paths
- [ ] Cached distance matrix persistence across app restarts
- [ ] Progress bar for distance matrix construction in UI
- [ ] Undo/redo for anchor editor
- [ ] Playlist preview (play sorted order before saving)
- [ ] More sorting algorithm options (genetic algorithm, greedy heuristic comparison)
- [ ] Packaging (single .exe via PyInstaller, or MSIX)

## Current Status
The application is feature-complete for its core workflow with **batch analysis fully implemented**, the **description generation queue infrastructure built**, the old monolithic `descriptions.py` replaced by a DB-backed background worker pattern, and the **Anchors page stabilized** (multi-backend LLM, session-level panel persistence). Users can:
1. Connect to Spotify or browse local files
2. **Batch analyze** — queue tracks from multiple playlists, then run "Start Batch Analysis" for sequential automatic capture (or analyze one-at-a-time via the Now Playing card)
3. **Queue descriptions** — add tracks to the description generation queue (per-track, selected-tracks, or all-tracks), see live status under Now Playing card, get live dialog updates when a description finishes
4. Create anchors manually or with AI (with **switchable LLM backend** and **session-persistent Generate Anchors panel**)
5. Run smart sorting
6. Save results back to Spotify or export M3U

Batch analysis includes: interference detection (warns if Spotify plays wrong track), watchdog timer (alerts if track is stuck), live queue position highlighting, thread-safety via reentrant RLock, auto-removal of analyzed tracks from queue, and production-clean verification.

**Description generation infrastructure (2026-07-22)**:
- `analysis/desc_generator.py`: Thread-safe FIFO queue (`queue.Queue` + `set` + `Lock`), daemon worker thread, LLM fallback chain (ollama → primary → mistral → deepseek), VA quadrant computation, `_feat_summary` inlined from old module
- `ui/desc_dialog.py`: Per-track dialog with live-update state (textarea + generated-at label pushed from worker callback), "Generate new description" button wired to `desc_queue_add()`
- `ui/pages/playlist_source.py`: Two new desc-gen buttons per playlist/queue table, desc status row under Now Playing card (buttons-first layout, no shifting), `_push_desc_to_open_dialog()` helper
- Old `llm/descriptions.py` deleted; `cache/store.py` `load_descriptions`/`save_descriptions` removed; `main.py` `run_descriptions()` stubbed with TODO comment

**Anchors page stabilization (2026-07-24)**:
- `ui/pages/anchors.py`: Multi-backend model dropdown in Generate Anchors panel (calls `_init_llm_client(backend=selected_backend, model_override=selected_model)`). 5 `_gen_last_*` session globals for panel persistence. `ui.notify()` before `_gen_panel.clear()` (RuntimeError fix). `_gen_last_n` reset to None on playlist switch.
- `llm/client.py`: Per-backend dict cache (`_llm_clients` keyed by backend name, `_llm_models_used` keyed by backend name). Legacy `_llm_client`/`_llm_backend_used`/`_llm_model_used` maintained for backward compat with `llm_chat()`.
- `llm/prompts.py`: Added `"custom"` structure type + `anchor_pct` to `PLAYLIST_STRUCTURES` (2026-07-22).

**Test suite**: **303 tests, all passing** in 6.75s (2026-07-29 audit) — 85 anchors page + 36 desc generator + 61 batch analysis + 5 buffer lifecycle + 17 desc status + 6 analyze regressions + 16 desc status + 4 logging setup + 2 main reimport + 3 module singleton + 1 sorted uris fallback + 14 spotify source + 1 stale cache schema + 1 start sorting no anchors + 4 nav buttons + 4 connect flow + 3 solver no anchors + 9 distance + 4 logging + 2 desc status boundary + 3 desc status caption = 303. Hash baseline: `tests/_pre_suite_hash.json` (674 embeddings, 15 cache files).

**Test-suite stabilization audit (2026-07-29)**:
- **Hang root-caused**: `test_stale_cache_without_uri_field_triggers_refetch` — the test mocked `playlist()` but not `playlist_items()`, and `playlist_cache.py`'s DI-cached `_get_playlist_tracks_fn` reference wasn't reachable via `mock.patch` on `spotify_source.get_playlist_tracks`. The MagicMock default iterator caused an infinite pagination loop at `spotify_source.py:356` (`time.sleep(0.3)`). Fixed by directly replacing `_pc._get_playlist_tracks_fn` and `_sps._is_track_playable` with lambdas, patching `_pc.CACHE_DIR_DEFAULT` (not `cfg.CACHE_DIR_DEFAULT`), wrapping in try/finally restore, and adding `@pytest.mark.timeout(10)` as a fail-safe.
- **Test-order-dependency root-caused**: `test_rebuild_queue_ui_proceeds_when_client_connected` failed when run after `test_batch_advance_no_ui_calls_from_bg_thread` because that test replaced `ps.ui` with a `_MockUI` instance (which lacks context manager protocol) and never restored it. Fixed by saving/restoring `_src_mod.ui` in a try/finally block.
- **Grep sweep**: Confirmed all other module-level swap patterns (`_save_track_worker_fn`, `_TAVILY_DEBUG_PATH`, `_right_panel`, `_queue_container`, `_get_playlist_tracks_fn`, `CACHE_DIR_DEFAULT`) have proper save/restore teardown. No additional leaks found.
- **Recommendation**: Consider extracting a reusable `mock_ui` pytest fixture (save/restore pattern) if more tests need UI interception in the future — currently only one test uses this pattern.

The application has been tested with RTX 5080 GPU acceleration. All primary features are functional.

## Known Issues
- MERT model is incompatible with `transformers >= 4.44.0` (pinned to 4.38.0 as workaround)
- PyAudioWPatch installation may require Visual C++ Build Tools on some systems
- Large playlists (200+ tracks) have noticeable distance matrix construction time
- Spotify API rate limiting may slow down large playlist operations
- WASAPI loopback capture is Windows-only — no macOS/Linux support
- `torchvision` warning: explicitly excluded from requirements but may be auto-installed as transitive dependency
- `LiveAnalyzeContext._analyze_buf` unprotected property — callers MUST hold `mode_lock` before reading; `get_locked_buf()` assertion catches violations in dev
- Batch analysis queue operates on live `state.analysis_queue` — tracks added mid-batch are discovered when `_batch_advance_to_next()` re-checks the queue (not a frozen snapshot)
- Interference detection tolerance is hardcoded; could be made configurable via settings
- **`anchor_editor.py`**: `save_plan` referenced before assignment (pre-existing bug, not from descriptions.py removal) — `ui.button(on_click=save_plan)` appears before `def save_plan():` in `build_anchor_editor()`. Test `test_anchor_editor_safe_with_empty_descs` treats `UnboundLocalError` as acceptable. TODO: fix separately.
- **`desc_generator` worker**: Currently does not ACTUALLY call LLM — placeholder loop. Infrastructure is ready (queue, dedupe, VA computation, fallback chain, DB write), just needs `_try_generate_description()` call un-commented/re-enabled as next step.

## Evolution of Project Decisions
1. **JSON → SQLite migration**: Originally used `tracks_db.json`, migrated to SQLite for better query performance and concurrent access (`database/migrate.py` exists for migration)
2. **Playlist analyzer → Modular package**: Original `playlist_analyzer.py` was refactored into the `playlist_arranger` package with clear domain boundaries
3. **Console UI → Web UI**: Originally a Rich terminal UI, now NiceGUI web interface for better usability
4. **Single LLM → Multi-backend**: Originally Ollama-only, now supports DeepSeek and Mistral via OpenAI-compatible API
5. **CPU-only → GPU support**: Added CUDA acceleration documentation and auto-detection for MERT model
6. **Spotify-only → Multi-source**: Added local file support (MP3, FLAC scanning, M3U handling)
7. **Monolithic poll → 3-stage decoupled**: Listen (polling), Analyze (buffering), Worker (async MERT) split into independent threads with queue
8. **Callback signature**: `_on_track_changed_cb(old_track_info_dict, new_track_info_dict)` — full dicts to avoid stale global reads (BUG 3 lesson)
9. **Module-level globals → Context class**: Extract analysis logic into `LiveAnalyzeContext` (2026-07-13) — all buffer/worker/poll state owned as instance attributes, shared `mode_lock` injected by caller; enables future concurrent contexts (Spotify live + local file batch)
10. **Custom JS row styling → Native Quasar selection**: Dropped JS-based row highlighting (unreliable due to DOM re-render timing, version-dependent selectors) in favor of Quasar `.selected` model (2026-07-13); both now-playing track and user-checked rows share the same `.selected` visual treatment
11. **Lock hardening**: `get_locked_buf()` with `assert self.mode_lock.locked()` (2026-07-13) — fails fast during dev/testing if caller forgets to hold the lock, prevents silent race-condition bugs
12. **Prefer native mechanisms over custom JS**: JS-based row styling has been unreliable in this codebase — Quasar native `.selected` is maintained by the framework and survives DOM re-renders
13. **Pause-aware audio gating**: Replaced RMS silence filter (which caused coverage undercounting during quiet passages) with `_is_playing` threading.Event gate — polls Spotify playback state rather than analyzing audio energy. Defaults to True so buffer collection works out of the box, cleared on pause, set on resume.
14. **Canvas-based visualization over component rebuilds**: For high-frequency UI updates (300ms), `ui.html(canvas)` + `ui.run_javascript()` avoids NiceGUI component rebuild overhead. Fixed canvas dimensions + CSS matching prevent browser scaling artifacts. Fixed-width stat labels prevent layout reflow when values change.
15. **Reentrant RLock for batch analysis**: `_batch_lock` (RLock, not Lock) protects batch state globals. Reentrant needed because `_batch_advance_to_next()` holds the lock, calls `_stop_batch_analysis()` which also acquires it, which calls `_rebuild_queue_ui()` (re-acquires for UI sync). Standard `Lock` would deadlock on re-acquire.
16. **Snapshot+position sequencer pattern**: Batch advance uses `_batch_current_track_id` as a position tracker rather than queue index. Worker completion is decoupled from advance — the advance function is called with the completed track_id; it checks `_batch_current_track_id == caller_track_id` to reject stale calls. Queue snapshot taken at advance time, not at batch start — allows live queue growth during batch run.
17. **Read-before-write for API calls**: `_safe_pause_active_playback()` checks current Spotify playback state before sending a pause command — avoids unnecessary API calls when already paused (reduces rate limit pressure).
18. **`ui_pending_queue` drain pattern**: Background threads push UI update actions into a thread-safe deque; a main-thread `ui.timer` drains and executes them. Prevents "UI updates must be called from main thread" errors. Includes `_ui_context_lock` (separate from `_batch_lock`) for serializing main-thread access from bg-thread callback sites.
19. **JSON-cache descriptions → DB-backed descriptions (2026-07-21/22)**: Old pipeline read/wrote `cache/descriptions_<pl_id>.json` files via `load_descriptions`/`save_descriptions`. New pipeline: background worker queue writes `desc_text` + `desc_generated_at` directly to SQLite via `db.save_track()`. Per-track dialog reads from `db.get_track()` at open time and live-updates when worker finishes. Old module `llm/descriptions.py` deleted. `main.py` `run_descriptions()` stubbed.
20. **Live dialog update via module globals (2026-07-22)**: `desc_dialog.py` exports `_current_open_track_id`, `_current_textarea`, `_current_generated_label` globals. `_push_desc_to_open_dialog()` in `playlist_source.py` (called from `_on_desc_generated` callback inside `_ui_context_lock` + `_ph._page_client`) reads fresh DB data and pushes to open textarea/label. Guards against stale/closed dialogs, different-track-open, and re-open same track.
21. **Layout stability: buttons-first in rows (2026-07-22)**: In `ui.row()`, children lay out left-to-right in creation order. Place fixed-width elements (buttons) BEFORE variable-width elements (labels) so labels changing length don't shift button positions. Applied in desc status row (`_render_desc_status`).