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
- [x] **Queue for Analysis feature** — expandable section above "Your Playlists", populated via per-playlist "Add Selected Tracks to Queue for Analysis" button (replaces "Analyze X missing"). Includes Start Batch Analysis (stub — real execution deferred), Remove Selected, Remove All controls. Persists to `cache/analysis_queue.json`. Fully isolated from now-playing highlight system. (2026-07-16)
- [ ] **Batch analysis execution logic** — "Start Batch Analysis" is currently a stub (logs + notifies only). Real implementation needs to play each queued track sequentially, wait for playback, feed existing analyze buffer pipeline per track, advance to next. **Next major piece of work.**
- [x] Expand test suite: 6 Analyze-mode regression tests updated to `LiveAnalyzeContext` API (were broken since 2026-07-13 refactor — referenced removed module-level globals like `_ps._analyze_buffer`, now access via `_ctx._analyze_buf`)
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
The application is feature-complete for its core workflow. Users can:
1. Connect to Spotify or browse local files
2. Analyze tracks (spotify playback capture or local file analysis)
3. Generate AI descriptions
4. Create anchors manually or with AI
5. Run smart sorting
6. Save results back to Spotify or export M3U

The application has been tested with RTX 5080 GPU acceleration. All primary features are functional. Three critical analyze-mode bugs were fixed (2026-07-12) with 6 regression tests passing.

## Known Issues
- MERT model is incompatible with `transformers >= 4.44.0` (pinned to 4.38.0 as workaround)
- PyAudioWPatch installation may require Visual C++ Build Tools on some systems
- Large playlists (200+ tracks) have noticeable distance matrix construction time
- Spotify API rate limiting may slow down large playlist operations
- WASAPI loopback capture is Windows-only — no macOS/Linux support
- `torchvision` warning: explicitly excluded from requirements but may be auto-installed as transitive dependency
- `LiveAnalyzeContext._analyze_buf` unprotected property — callers MUST hold `mode_lock` before reading; `get_locked_buf()` assertion catches violations in dev

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
