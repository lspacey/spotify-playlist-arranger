# Active Context

## Current Work Focus
Batch analysis execution fully implemented (2026-07-17/18). The "Start Batch Analysis" stub has been replaced with real sequential playback logic including interference detection, watchdog timer, live queue rendering, and comprehensive thread-safety (RLock). Test suite expanded to cover ~50 batch-specific regression tests. Package split (`playlist_source.py` → package) was attempted and reverted — keeping single-file module for now. All work on `local-files-support` branch.

## Recent Changes
- **2026-07-18 (Batch analysis — package split + reversion)**:
  - Attempted `playlist_source.py` → `playlist_source/` package conversion (`91879b2`), reverted in HEAD (`9f1fc9b`) — single-file module retained
  - 17 commits total on branch covering full batch analysis implementation

- **2026-07-17/18 (Real batch analysis execution)**:
  - **Thread-safety**: `_batch_lock` (reentrant RLock) protecting all `_batch_*` globals — reentrant needed because `_batch_advance_to_next` → `_stop_batch_analysis` → `_rebuild_queue_ui` call chain
  - **Snapshot+position sequencer**: `_batch_advance_to_next()` uses `_batch_current_track_id` as position tracker; snapshot of queue position taken before playback, decoupled from worker completion — stale track_id check prevents double-advance
  - **Interference detection**: `_batch_expected_track_id` tracks what Spotify was told to play; if playback reports a different track, watchdog fires warning (banner + log) with configurable tolerance
  - **Watchdog timer**: Separate polling thread monitors `_batch_current_track_duration_ms + tolerance`; fires once per track via `_batch_watchdog_fired_by_track_id` dedup
  - **Live queue rendering**: `_needs_queue_highlight` flag + `_ui_pending_queue` drain mechanism for batch position updates from background threads
  - **`_safe_pause_active_playback()`**: Read-before-write pause pattern — checks current playback state before sending pause to avoid unnecessary API calls
  - **Auto-remove analyzed tracks**: `_on_analysis_complete` callback removes track from queue after successful analysis save
  - **7-priority track status**: Unified status checking logic (`Status.BAD_GENRE` → `UNKNOWN` → `NEEDS_REANALYSIS` → `DIFFERENT_VERSION` → `LAST_RUN` → `UNPLAYABLE_LOCAL` → `OK`) with "Add Not OK Tracks" button
  - **Status refresh timer**: Periodic UI refresh of track status indicators
  - **4 bugs fixed** from live testing: `_update_viz()` NameError, pause pattern races, batch state cleanup, test data isolation
  - **2 regression tests** for batch interference false-positive invariant
  - **Production-clean verification**: `tests/_verify_production_clean.py` ensures `hasattr` checks return False in production

- **2026-07-16 (Queue for Analysis feature)**:
  - New expandable "Queue for Analysis" section above "Your Playlists" (collapsed by default, stays collapsed on add, preserves open state if user manually expands)
  - Repurposed per-playlist "Analyze X missing" button → "Add Selected Tracks to Queue for Analysis" (enabled only when ≥1 row checked, 0.5s timer poll)
  - Queue label: "Queue for Analysis — N tracks" updated after every mutation
  - Three control buttons: Start Batch Analysis (stub — logs + notify only; real execution to be done in follow-up), Remove Selected Tracks (enabled when ≥1 checked in queue), Remove All Tracks (enabled when queue non-empty)
  - Queue persists to `cache/analysis_queue.json` via `atomic_write_json`; loaded on startup in `main.py`
  - Queue table fully isolated from now-playing highlight system (NOT registered in `_playlist_tables`/`_now_playing_row_keys`)
  - `_run_spotify_analysis()` tagged as TODO (unreferenced — original "Analyze X missing" caller removed)
  - **Test suite fix**: 6 tests in `tests/_run_tests.py` updated for `LiveAnalyzeContext` API — were silently broken since 2026-07-13 refactor (referenced removed module-level globals like `_ps._analyze_buffer`, now all via `_ctx._analyze_buf`). Full suite: 6/6 passing.

- **2026-07-13 (Buffer lifecycle fixes + features)**:
  - **Bug fix 1**: `sync_analyze_buffer(None)` in "not playing" polling branch — buffer flushes immediately on playback stop
  - **Bug fix 2**: Removed `SILENCE_RMS_THRESHOLD` from `collect_samples()`; replaced with `_is_playing` threading.Event gate to prevent pause-silence pollution
  - **Button label**: "Analyzing..." → "Analyzing... Click to stop"
  - **Feature 1 — Spotify API call counter**: `SpotifyCallProxy` in `spotify_source.py`, displayed as "🔄 X API calls" next to Connect button
  - **Feature 2 — Audio spectrum visualizer**: 120×60 canvas (white bg, `#9E9E9E` gray bars, 10 FFT bands), SR/RMS/Pk stats via NiceGUI labels, silence gate
  - **Layout fix**: Two-column split changed from `flex-1`/`flex-1` to `w-[30%]`/`w-[70%]` — prevents visualizer wrapping
  - **Dead code cleanup**: Removed unused `SILENCE_RMS_THRESHOLD` import, `_current_playlist_uri` global

- **2026-07-13 (Part A — Analysis extraction)**:
  - Extracted all buffer/worker/poll state and methods from `playlist_source.py` into `playlist_arranger/analysis/live_buffer.py` as `LiveAnalyzeContext` class
  - Shared `mode_lock` injected by caller (dependency injection pattern), not duplicated
  - `get_locked_buf()` with `assert self.mode_lock.locked()` for lock hardening
  - `AnalyzeBuffer` dataclass exported (with `submitted` field, prevents double-flush on loops/seeks)
  - Source-agnostic: no Spotify imports, works for future local-file batch analysis
  - `5/5` buffer lifecycle tests passing (seek-back, submitted flag, early flush, collect_samples no-op, forward playback)

- **2026-07-13 (Part B — Multi-select + native highlight)**:
  - Removed `_inject_no_selection_css()` — checkboxes now visible
  - Changed `selection="single"` → `selection="multiple"` in track tables
  - Now-playing highlight uses `table.selected = [match_row]` (native Quasar `.selected`, not custom JS)
  - `_now_playing_row_keys[pl_id]` dict tracks which row is now-playing → `_get_selected_rows()` excludes it
  - Play queue feature: ready to wire in (checkboxes work, `_get_selected_rows()` returns only user-checked rows)
  - Double-click on row → `_play_track()` on current Spotify device
  - Caption updated: "Double-click a row to ▶ Play"

- **2026-07-12 (3 bugs fixed)**:
  - **BUG 1**: Mid-track Analyze init — `_start_analyze_buffer()` not called when clicking Analyze mid-track, leaving `_analyze_track_duration_ms=0` → all buffers silently discarded as coverage=0
  - **BUG 2**: Stop cascade — `_analyze_buffer`/`_analyze_track_id`/`_analyze_samples_count` not reset on Stop, leaking stale state into next Listen session
  - **BUG 3**: Wrong track metadata on flush — `_card_listen_thread()` overwrote `_current_track` with new track info BEFORE calling `_on_track_changed_analyze()`, which then read the global and used NEW track's name/duration for OLD track's buffer (e.g. "Fantasy, coverage=172%"). Fixed by saving `previous_track_info` before overwriting and passing full dict to callback.
  - **Comprehensive logging added**: Button clicks (ON/OFF state), track transitions (Artist - Name, duration), worker lifecycle (librosa/MERT timing), highlight path debug (gate-by-gate conditions)
  - **6 regression tests** in `tests/_run_tests.py` (3 BUG 1 + 2 BUG 2 + 1 BUG 3), all passing
- **2026-07-12**: Major refactor of Now Playing card — three-stage decoupling of Listen/Analyze into independent threads with async worker queue; added silence detection, unlimited buffer with safety cap, track change callback, 90% coverage threshold
- **2026-07-10**: Memory bank initialized (7 files) + API doc summaries created
- **2026-07-10**: Context7 MCP server configured and verified working
- Latest git commit (HEAD, `local-files-support`): `9f1fc9b Revert "Convert playlist_source.py to playlist_source/ package"`

### Now Playing Architecture (2026-07-12)
| Component | Thread | Responsibility |
|---|---|---|
| **Listen** (Stage 1) | `_card_listen_thread()` | Spotify API polling, track info, highlight JS ONLY — zero audio processing |
| **Analyze Buffer** (Stage 2) | `_analyze_poll_thread_fn()` | Continuous WASAPI audio capture, silence detection (RMS < `SILENCE_RMS_THRESHOLD`), unbounded buffer growth (capped at `MAX_ANALYZE_BUFFER_S` = 900s), 90% coverage threshold |
| **Async Worker** (Stage 3) | `_analyze_worker_loop()` | Single long-lived `while True` thread (no recursion), maxsize=1 queue, runs `save_track_worker` → librosa + MERT embedding + DB save |

### Button State Table
| State | Listen Button | Analyze Button |
|---|---|---|
| Idle | "Start Listening" (green) | "Analyze" (green, disabled) |
| Listening | "Stop Listening" (red) | "Analyze" (green, enabled) |
| Analyzing | "Stop Listening" (red) | "Analyzing..." (orange) |
| Stopping | "Stopping..." (orange, disabled) | disabled |

### Config Constants Added
- `SILENCE_RMS_THRESHOLD = 0.001` — empirical: typical music RMS ~0.01-0.3, pure silence ~0.0001
- `MIN_COVERAGE_PCT = 0.90` — track must be ≥90% captured to submit
- `MAX_ANALYZE_BUFFER_S = 900` — safety cap: 15 min / ~400 MB at 22050 Hz mono float32

## Next Steps
1. Monitor DeepSeek model migration (legacy `deepseek-chat`/`deepseek-reasoner` retiring July 24, 2026 — ~2 weeks from now)
2. Consider updating codebase: Ollama `think=False` boolean → string level, DeepSeek `thinking` as object in `extra_body`
3. Test full Analyze cycle end-to-end with logs

## Active Decisions and Considerations
- **MERT model version pinned**: `transformers==4.38.0` is pinned (not latest) due to MERT-v1-95M compatibility issue with `conv_pos_batch_norm` removal in transformers ≥ 4.44.0
- **CUDA index for RTX 5080**: Uses `cu130` (CUDA 13.0 / PyTorch 2.12.1) for Blackwell GPU support
- **No torchvision**: Explicitly noted as not required and should not be installed
- **WASAPI-only**: The audio capture is Windows-exclusive; no cross-platform audio backend
- **Three LLM backends**: Ollama (local), DeepSeek (cloud), Mistral (cloud) — all abstracted behind same `llm_chat()` interface

## Important Patterns and Preferences
- All dependency versions are strictly pinned (`==`) for reproducible builds
- **Refactor rule**: When extracting internal state into new classes/contexts (e.g. `LiveAnalyzeContext`), update dependent tests in the SAME commit — tests silently drifted for 3 days after the 2026-07-13 refactor before being caught (2026-07-16). Do NOT defer test updates to a follow-up.
- **LiveAnalyzeContext old→new API mapping** (2026-07-13 refactor, tests fixed 2026-07-16):
  | Old API (`_ps.<attr>`) | New API (`_ctx.<attr>` via `_ps._live_ctx`) |
  |---|---|
  | `_ps._analyze_buffer` | `_ctx._analyze_buf` (AnalyzeBuffer dataclass instance) |
  | `_ps._analyze_track_id` | `_ctx._analyze_buf.track_id` |
  | `_ps._analyze_samples_count` | `_ctx._analyze_buf.samples_count` |
  | `_ps._analyze_sample_rate` | `_ctx._analyze_buf.sample_rate` |
  | `_ps._analyze_track_duration_ms` | `_ctx._analyze_buf.track_info["duration_ms"]` |
  | `_ps._start_analyze_buffer(id, sr)` | `_ctx.sync_analyze_buffer(track_info)` |
  | `_ps._on_track_changed_analyze(old, new)` | `_ctx.on_track_changed(old, new)` |
  | `_ps._analyze_poll_stop` | `_ctx._analyze_poll_stop` (direct access — same attr name) |
  | `_ps._analyze_worker_task` | `_ctx._analyze_worker_task` (under `_ctx._analyze_worker_lock`) |
  | `_ps._analyze_worker_busy` | `_ctx._analyze_worker_busy` (under `_ctx._analyze_worker_lock`) |
  | `_ps._analyze_worker_lock` | `_ctx._analyze_worker_lock` |
- Module-level `HAS_*` booleans for optional dependency handling (graceful degradation)
- Progress callbacks (`progress_cb`) pattern throughout the codebase for UI feedback
- Atomic file writes (`atomic_write_json`) for all persisted JSON — write to `.tmp`, then rename
- Per-thread SQLite connection caching with WAL mode for concurrent access
- Global reactive state in `ui/state.py` (no complex state management needed for NiceGUI SPA)
- Navigation via simple page name string and conditional rendering (no router)

## Learnings and Project Insights
- The distance matrix is the primary performance bottleneck for large playlists — O(n²) pairwise computation
- The SA solver caches distance matrices per playlist ID + track tuple key, avoiding recomputation on re-runs
- Spotify's playlist reorder API has a limit of 100 URIs per request; the code handles chunking with PUT + POST pattern
- MERT embeddings are computed per-track and saved as `.npy` files independently from the SQLite DB (separation of concerns)
- The local file analysis path is significantly faster than Spotify (reads from disk vs real-time playback)
- Placeholders in anchor plans create "open slots" where the SA solver fills free tracks — enables flexible arrangement templates