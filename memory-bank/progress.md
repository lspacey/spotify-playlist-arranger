# Progress

## What Works
- ✅ Spotify OAuth authentication and playlist fetching
- ✅ WASAPI loopback audio capture with device persistence (by name)
- ✅ Audio feature extraction (BPM, key, loudness, spectral features, MFCC, chroma, frequency balance)
- ✅ MERT neural embedding generation (768-dim) with GPU acceleration
- ✅ SQLite database for persistent track storage
- ✅ LLM-based track description generation (Ollama, DeepSeek, Mistral)
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

## What's Left to Build
- [x] **Queue for Analysis feature** — expandable section above "Your Playlists", populated via per-playlist "Add Selected Tracks to Queue for Analysis" button (replaces "Analyze X missing"). Persists to `cache/analysis_queue.json`. Fully isolated from now-playing highlight system. (2026-07-16)
- [x] **Batch analysis execution logic** — Real sequential playback via `_batch_advance_to_next()` with snapshot+position sequencer, interference detection (`_batch_expected_track_id`), watchdog timer, live queue rendering, thread-safety (RLock), auto-remove analyzed tracks from queue. (2026-07-17/18)
- [x] Expand test suite: 6 Analyze-mode regression tests + ~50 batch-specific regression tests in `tests/_test_batch_analysis.py` (batch advance, interference, watchdog, queue lifecycle, thread-safety, race conditions, flush-during-stop patterns)
- [x] Production-clean verification: `tests/_verify_production_clean.py` ensures `hasattr` checks return False in production
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
The application is feature-complete for its core workflow with **batch analysis now fully implemented** and the monolithic `playlist_source.py` partially decomposed into focused modules. Users can:
1. Connect to Spotify or browse local files
2. **Batch analyze** — queue tracks from multiple playlists, then run "Start Batch Analysis" for sequential automatic capture (or analyze one-at-a-time via the Now Playing card)
3. Generate AI descriptions
4. Create anchors manually or with AI
5. Run smart sorting
6. Save results back to Spotify or export M3U

Batch analysis includes: interference detection (warns if Spotify plays wrong track), watchdog timer (alerts if track is stuck), live queue position highlighting, thread-safety via reentrant RLock, auto-removal of analyzed tracks from queue, and production-clean verification.

**Recent refactoring (2026-07-20)**: Three modules extracted from `playlist_source.py`:
- `analysis/batch_analyzer.py` — batch state and sequencer logic
- `ui/audio_viz.py` — canvas visualization and FFT updates
- `ui/playlist_highlight.py` — row highlighting, auto-expand, notify

**New tools directory** (`tools/`): `_add_dedup.py` (deduplication), `_diagnose_unplayable.py` (track playability diagnostics), `_move_ph_step1/2/3.py` (multi-step playlist migration helper).

**Cache robustness (2026-07-20)**: Empty cache files and corrupt cache files are auto-detected and deleted, forcing clean re-fetch. Unplayable tracks (local files with no market data) are filtered during playlist loading.

**Test suite**: ~50 batch-specific regression tests in `tests/_test_batch_analysis.py` + 5 buffer lifecycle tests + 6 analyze-mode regression tests + `tests/_verify_production_clean.py` (hasattr guard) + `tests/_capture_hashes.py` (hash snapshot utility) — all passing. Hash baseline: `tests/_pre_suite_hash.json` (674 embeddings, 13 cache files).

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

> **2026-07-13 — Buffer lifecycle fixes (continue implementation)**
> - **Bug fix 1**: `sync_analyze_buffer(None)` called in "not playing" branch of polling loop — flushes buffer immediately on playback stop (previously waited for track change that never came)
> - **Bug fix 2**: Removed `SILENCE_RMS_THRESHOLD` filter from `collect_samples()` — all audio chunks appended to buffer for accurate coverage%; replaced with `_is_playing` threading.Event gate to prevent pause-silence pollution
> - **Button label**: "Analyzing..." → "Analyzing... Click to stop" for clearer UX
> - Cleaned up unused `SILENCE_RMS_THRESHOLD` import and `_current_playlist_uri` global variable

> **2026-07-13 — Stretch: API counter + audio visualizer (Features 1 & 2)**
> - **Feature 1 — Spotify API call counter**: `SpotifyCallProxy` class in `spotify_source.py` wraps spotipy client via `__getattr__`, thread-safe counter, displayed as "🔄 X API calls" next to Connect button. Verified zero `isinstance(state.sp, spotipy.Spotify)` type checks in codebase — all calls are flat top-level methods.
> - **Feature 2 — Audio spectrum visualizer**: 120×60 canvas (white bg, `#9E9E9E` gray LED-style bars, 10 log-spaced FFT bands), placed to the right of Audio Capture Device dropdown. Stats (SR/RMS/Pk) displayed via NiceGUI labels in a vertical column with fixed `w-24` width to prevent layout shift. 300ms `ui.timer` updates, FFT computed in Python from deque copy under brief `audio_lock`. Silence gate: `max(abs(mono)) < 1e-10` → shows "— dB" placeholders instead of computing `20*log10(near_zero)` → -240dB.
> - **Layout fix**: Two-column split adjusted from `flex-1`/`flex-1` (50/50) to `w-[30%]`/`w-[70%]` to prevent visualizer wrapping. Note: attempts to fix via CSS `min-width:0` overrides on nested Quasar flex containers (`q-field__inner`/`q-field__control`/`q-field__control-container`) proved ineffective in practice (reverted) — working fix was reallocating column widths.

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
