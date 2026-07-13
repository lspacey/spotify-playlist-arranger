# Active Context

## Current Work Focus
End of session — buffer lifecycle fixes, API call counter, and audio visualizer complete. Docs updated, ready to commit to `local-files-support`.

## Recent Changes
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
- Latest git commit: `0f4fb5f3ab98d5bc049639436d816b13374ee0e6`

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