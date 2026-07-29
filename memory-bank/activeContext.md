# Active Context

## Current Work Focus
Test-suite stabilization audit (2026-07-29): Diagnosed and fixed a terminal hang and a test-order-dependency bug discovered during a full `pytest tests/` run. Full suite now runs **303 tests, all passing, in 6.75s** with zero hangs and zero failures.

## Recent Changes

### 2026-07-29 — Test-suite stabilization audit (2 bugs root-caused and fixed)

**Bug 1 — Terminal hang at 99%**: `test_stale_cache_without_uri_field_triggers_refetch` caused pytest to hang indefinitely.

- **Root cause (two-part)**:
  1. The test mocked `fake_sp.playlist()` but `get_playlist_tracks()` calls `sp.playlist_items()` in a pagination loop. Since `playlist_items` was not mocked, the MagicMock's default iterator produced an infinite loop with `time.sleep(0.3)` per iteration — never seeing `"next": None` to terminate.
  2. `playlist_cache.py` stores a DI-cached function reference (`_get_playlist_tracks_fn`) wired at module init time by `playlist_source.py`. `mock.patch("...spotify_source.get_playlist_tracks")` could not reach this cached reference — the stale real function was still called.
- **Fix**: Directly replaced `_pc._get_playlist_tracks_fn` and `_sps._is_track_playable` with lambdas returning canned data. Patched `_pc.CACHE_DIR_DEFAULT` (not `cfg.CACHE_DIR_DEFAULT`) because `playlist_cache.py` imports its own module-level reference. Added `@pytest.mark.timeout(10)` as a hard fail-safe against future mock-completeness bugs.
- **Files changed**: `tests/test_stale_cache_schema.py`

**Bug 2 — Test-order-dependency**: `test_rebuild_queue_ui_proceeds_when_client_connected` passed in isolation but FAILED when run after `test_batch_advance_no_ui_calls_from_bg_thread` in the full suite.

- **Root cause**: `test_batch_advance_no_ui_calls_from_bg_thread` replaced `ps.ui` with a `_MockUI` instance (which lacks `__enter__`/`__exit__` context manager protocol) to intercept UI calls from a background thread. It never restored the original `ps.ui` reference. All subsequent tests that triggered `_rebuild_queue_ui()` → `_render_queue_controls()` → `with ui.row()` would crash with `TypeError: 'test_batch_analysis._MockUI' object does not support the context manager protocol`.
- **Fix**: Saved `saved_ui = _src_mod.ui` before replacement and wrapped the test body in `try: ... finally: _src_mod.ui = saved_ui`.
- **Files changed**: `tests/test_batch_analysis.py`

**Grep sweep**: Confirmed all other module-level attribute swaps (`_save_track_worker_fn`, `_TAVILY_DEBUG_PATH`, `_right_panel`, `_queue_container`, `_get_playlist_tracks_fn`, `CACHE_DIR_DEFAULT`) have proper save/restore teardown. No additional leaks found.

**Recommendation**: Consider extracting a reusable `mock_ui` pytest fixture (save/restore pattern) if more tests need UI interception in the future.

### 2026-07-28 — Playlist track fetching bug fix (double-filtering)
- **Root cause**: `_load_cached_playlist_tracks()` in `playlist_source.py` called `get_playlist_tracks()` (which already filters unplayable tracks and returns flattened dicts), then did a SECOND pass with `_is_track_playable()` on those flattened dicts. `_is_track_playable()` expects raw Spotify API items with a nested `track` key — given flattened dicts, `track.get("type")` returned `None` (not `"track"`), causing every track to be filtered out. Result: always 0 tracks, always empty caches.
- **Fix**: Removed redundant `_is_track_playable()` second-pass filtering and its import from `playlist_source.py`. `get_playlist_tracks()` already handles all unplayable filtering internally. Log message clarified from "raw tracks" to "playable tracks".
- **Test**: `tests/test_stale_cache_schema.py` passes (1/1).

### 2026-07-24 — Anchors page stabilization pass (4 bugs fixed)
- **Bug 1 — Multi-backend LLM model dropdown**: Replaced read-only model label with user-selectable dropdown in Generate Anchors panel. `_available_backend_models()` lists all backends with valid credentials (Ollama always, DeepSeek/Mistral if API keys present). `_on_run_generate` closure-captures `selected_backend` + `selected_model` and passes `backend=selected_backend, model_override=selected_model` to `_init_llm_client()`. 3 tests added.
- **Bug 1 — Per-backend client cache (de-risked stale-cache)**: Changed `llm/client.py` `_init_llm_client()` from single-slot `_llm_client` global to per-backend dict (`_llm_clients` keyed by backend name, `_llm_models_used` keyed by backend name). Cache hit checks both client and model match. Legacy `_llm_client`/`_llm_backend_used`/`_llm_model_used` updated on cache hit for backward compat with `llm_chat()`. 1 regression test added.
- **Bug 2 — ui.notify() ordering**: `ui.notify()` called BEFORE `_gen_panel.clear()` in `_on_run_generate`, wrapped in try/except with `logger.exception` fallback. Prevents RuntimeError from stale UI slot context after async handler resumes from `asyncio.to_thread`. 1 test added verifying source code ordering using `source.rfind("_gen_panel.clear()")`.
- **Session-level panel persistence**: 5 `_gen_last_*` module globals (`_gen_last_structure_id`, `_gen_last_desc_text`, `_gen_last_desc_touched`, `_gen_last_n`, `_gen_last_backend`) remember user selections across collapse/expand cycles. Three lightweight `on_change` handlers update globals on every keystroke/change (no disk I/O). `_render_generate_panel()` reads from these globals, falling back to hardcoded defaults only on first-open-ever. Custom-structure precedence edge case resolved: untouched custom loads from `settings.json`, user-typed text survives within session. N resets to None on playlist switch (`_on_playlist_selected`). 6 tests added.
- **Test suite**: 85 tests in `_test_anchors_page.py`, total suite 209/209 passing (all 6 test modules).

### 2026-07-22 — Background description queue + old module removal + live dialog updates
- **`desc_generator.py`** — New module: thread-safe FIFO queue (`queue.Queue` + `set` + `Lock`), daemon worker thread, LLM fallback chain (ollama → primary → mistral → deepseek), `desc_queue_add/add_many/clear/size`, `start_desc_generator()`/`stop_desc_generator()`, module-level `desc_generator_current_track_id/name` globals for UI. VA quadrant computation (`compute_valence_arousal`, `va_quadrant`, `va_intensity_label`). `_feat_summary` inlined from deleted `descriptions.py`. Description generation writes to SQLite DB (`desc_text` + `desc_generated_at` columns).
- **`descriptions.py` DELETED** — Old monolithic LLM generation module (228 lines). `_emb_stats` + `generate_track_descriptions` gone. `_feat_summary` inlined into `desc_generator.py`. `load_descriptions`/`save_descriptions` removed from `cache/store.py`.
- **`main.py`**: `run_descriptions()` stubbed with warning notification (button still on Anchors page — TODO to wire to `desc_queue_add_many`).
- **`desc_dialog.py`**: Live-update state (`_current_open_track_id`, `_current_textarea`, `_current_generated_label` globals), `_close_dialog_state()` reset on dialog close. "Generate new description" button calls `desc_queue_add()`.
- **`playlist_source.py`**: Two new "Generate new descriptions" buttons per playlist table (selected tracks + all tracks) and per queue table. Desc status row layout reordered (buttons first, labels last — fixes layout shifting). `_push_desc_to_open_dialog()` pushes newly-generated description + timestamp into open dialog live.
- **`cache/store.py`**: `load_descriptions()` and `save_descriptions()` removed (JSON-based pipeline replaced by DB-backed worker).
- **Test suite**: `_test_desc_generator.py` expanded from 27 → 36 tests (9 new: queue tests from initial build, 4 dialog live-update tests, 3 regression tests for deleted module). Full suite: 125/125 passing.

### 2026-07-20 (Minor fixes — HEAD: `0921edf`)
- **Unplayable track filtering**: `_load_cached_playlist_tracks()` now calls `_is_track_playable()` on raw Spotify API results, filtering out local files with no market data. With detailed logging of raw→filtered counts and warnings for empty results.
- **Empty cache detection**: Cache files with 0 tracks are auto-detected and deleted (stale write from transient error), forcing re-fetch from Spotify API.
- **Corrupted cache handling**: `json.loads` failures now delete the corrupt cache file and re-fetch instead of silently falling through.
- **Stale cache cleanup**: Previous snapshot cache files are deleted before writing new snapshot cache.
- **Playlist name in logs**: Added playlist name extraction alongside snapshot_id for clearer debugging output.
- **Additional batch regression tests**: 103 lines of new tests in `_test_batch_analysis.py`.

### 2026-07-20 (Bug fixes, stability, internal changes — `c86d35b`)
- **Module extraction refactoring** (pure moves, no behavior changes):
  - `playlist_arranger/analysis/batch_analyzer.py` (208 lines) — batch state globals, `_batch_advance_to_next()`, `_stop_batch_analysis()`, watchdog, UI drain queue, all extracted from `playlist_source.py`
  - `playlist_arranger/ui/audio_viz.py` (141 lines) — canvas JS setup, `_update_viz()`, FFT band computation extracted from `playlist_source.py`
  - `playlist_arranger/ui/playlist_highlight.py` (207 lines) — `_sync_row_highlight()`, `_notify_playing_track()`, auto-expand/collapse, playlist table refs, all extracted from `playlist_source.py`
- **Dependency injection pattern** used in all three new modules: `configure()` function receives callbacks from `playlist_source` at module init time, avoiding circular imports.
- **`playlist_source.py`**: significantly reduced (709 lines changed, deletions > additions), now delegates to extracted modules
- **New tools directory**: `tools/` with `_add_dedup.py`, `_diagnose_unplayable.py`, `_move_ph_step1/2/3.py` — utility scripts for maintenance operations
- **Test hash snapshot**: `tests/_pre_suite_hash.json` captures current DB state (674 embeddings, 13 cache files) for regression baseline comparison
- **`tests/_capture_hashes.py`**: script to regenerate hash snapshots
- **`tests/_gen_t` deleted**: old test generator script removed
- **Test expansion**: `_test_batch_analysis.py` grew by 1691 lines; `_test_buffer_lifecycle.py` updated (30 lines); `_run_tests.py` updated (26 lines)
- **Memory bank updated**: activeContext.md, progress.md, systemPatterns.md

### Older changes (2026-07-12 through 2026-07-18)
See git history for full details. Key milestones:
- Now Playing architecture (3-stage decoupled Listen/Analyze/Worker)
- Queue for Analysis feature with persist/restore
- Batch analysis execution with interference detection, watchdog, RLocks
- Buffer lifecycle fixes (mid-track init, stop cascade, wrong track metadata)
- API counter + audio visualizer
- Multi-select + native Quasar highlight
- LiveAnalyzeContext extraction

## Next Steps
1. Wire `desc_generator` worker to actually call LLM (currently placeholder `time.sleep()`) — the queue infrastructure is ready, just needs actual generation call
2. Wire `run_descriptions()` to `desc_queue_add_many()` + populate `current_descs` from DB
3. Monitor DeepSeek model migration (legacy `deepseek-chat`/`deepseek-reasoner` retiring July 24, 2026)
4. Consider updating codebase: Ollama `think=False` boolean → string level, DeepSeek `thinking` as object in `extra_body`
5. Cross-platform audio capture (macOS/Linux support)

## Active Decisions and Considerations
- **Descriptions now DB-backed** — `desc_text` and `desc_generated_at` columns in SQLite (via `db.get_track`/`db.save_track`), replacing old `cache/descriptions_<pl_id>.json` files. `load_descriptions`/`save_descriptions` removed from `cache/store.py`.
- **Live dialog update pattern** — `desc_dialog.py` exports `_current_open_track_id`, `_current_textarea`, `_current_generated_label` module globals. `_push_desc_to_open_dialog()` in `playlist_source.py` reads fresh DB data and pushes to open textarea/label inside `_ui_context_lock` + `_ph._page_client` context.
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
- **NiceGUI dialog lifecycle**: `ui.dialog()` does NOT accept `on_close` keyword — use `dialog.on("update:model-value", lambda e: handler() if not e.args else None)` for close detection. This fires `_close_dialog_state()` on close only (e.args is falsy when closing).
- **Layout stability**: In `ui.row()`, children are laid out left-to-right in creation order. Place fixed-width elements (buttons) BEFORE variable-width elements (labels) so the latter's content changes don't shift the former's position.