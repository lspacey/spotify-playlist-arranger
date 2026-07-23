# System Patterns

## Architecture Overview
The Playlist Arranger follows a **modular monolith** pattern with a **NiceGUI web UI** frontend. It is structured as a Python package (`playlist_arranger`) with clear domain separation across subpackages.

```
┌─────────────────────────────────────────────────────┐
│                    NiceGUI Web UI                    │
│  (main.py, ui/state.py, ui/pages/, ui/components/)  │
├─────────────────────────────────────────────────────┤
│                    Orchestration                     │
│          (main.py — page routing, actions)           │
├──────────┬──────────┬──────────┬────────────────────┤
│ Sources  │ Analysis │   LLM    │     Sorting         │
│ spotify  │ session  │ prompts  │  distance/solver    │
│ local    │ worker   │ client   │  anchors            │
├──────────┴──────────┴──────────┴────────────────────┤
│                    Audio Layer                        │
│      capture.py | features.py | mert.py              │
├─────────────────────────────────────────────────────┤
│               Persistence Layer                       │
│    database/db.py (SQLite) | cache/store.py (JSON)   │
├─────────────────────────────────────────────────────┤
│                    Config                             │
│         config.py (settings, constants, .env)         │
└─────────────────────────────────────────────────────┘
```

## Key Design Patterns

### 1. Global Reactive State (`ui/state.py`)
Module-level mutable globals act as a simple reactive store for the NiceGUI SPA. Since NiceGUI runs in a single process with async handlers, this pattern works without complex state management libraries.

### 2. Strategy Pattern for LLM Backends (`llm/client.py`)
The LLM backend is abstracted behind `llm_chat()`. Three strategies are supported:
- **Ollama** — Direct `ollama` Python module calls (local)
- **DeepSeek** — OpenAI-compatible client pointed at `api.deepseek.com`
- **Mistral** — OpenAI-compatible client pointed at `api.mistral.ai/v1`

### 3. Worker Pattern for Analysis (`analysis/worker.py`)
`save_track_worker()` encapsulates the full feature extraction + MERT embedding + DB save pipeline. Called from both the Spotify analysis session and local file analysis.

### 4. Session Pattern for Audio Capture (`analysis/session.py`)
`AnalysisSession` is a stateful object that manages sequential playback of tracks, audio buffer management, polling, and the analysis pipeline for each track.

### 5. Repository Pattern for Database (`database/db.py`)
SQLite operations abstracted behind a simple CRUD interface with thread-safe connection management (WAL mode, per-thread connection caching).

### 6. Dependency Injection for Shared State (`analysis/live_buffer.py`)
`LiveAnalyzeContext` class receives shared objects via constructor injection:
- `mode_lock: threading.Lock` — the SAME lock object used by caller's UI/poll threads
- `is_analyze_mode: Callable[[], bool]` — reads caller's mode flag atomically
- `capture_module` — live reference to `audio.capture` for `actual_sr`/`audio_deque`

This avoids:
- Duplicating the lock across module boundaries (silent race conditions)
- Circular imports (analysis module doesn't import from UI module)
- Module-level mutable globals that break when multiple contexts run concurrently

### 7. Prefer Native Framework Mechanisms Over Custom JS
Custom JS-based row styling (DOM manipulation via `ui.run_javascript()`) proved unreliable due to DOM re-render timing and version-dependent selectors. Quasar's native `.selected` model is maintained by the framework, survives re-renders, and integrates correctly with multi-select checkboxes.

### 8. Proxy Wrapper for API Instrumentation (`sources/spotify_source.py`)
`SpotifyCallProxy.__getattr__` intercepts all spotipy method calls to increment a thread-safe counter. Displayed as "🔄 X API calls" next to the Connect button. Verified zero `isinstance(state.sp, spotipy.Spotify)` type checks — all calls are flat top-level methods, no nested attribute chains that bypass the proxy.

### 9. Canvas + Fixed-Layout Stats for High-Frequency Visualization
For 300ms visual updates, `ui.html(canvas)` + `ui.run_javascript()` avoids NiceGUI component rebuild overhead. Fixed native canvas dimensions (120×60) with matching CSS prevent browser stretching. Stats labels use fixed `w-24` width to prevent layout reflow when values change. Silence gate (`max(abs(mono)) < 1e-10`) shows "—" placeholders instead of computing misleading `20*log10(near_zero)`.

### 10. Reentrant Lock Pattern for Batch State
`_batch_lock = threading.RLock()` (reentrant) protects all `_batch_*` module-level globals. RLock (not Lock) is critical because `_batch_advance_to_next()` holds the lock and then calls `_stop_batch_analysis()` which also acquires it, which then calls `_rebuild_queue_ui()` (re-acquires for UI sync). A standard `Lock` would deadlock on the re-acquire in that call chain.

### 11. Snapshot + Position Sequencer for Batch Advance
`_batch_advance_to_next(caller_track_id)` uses `_batch_current_track_id` as a position tracker rather than a queue index. Worker completion is decoupled from advance — the function rejects stale calls by checking `_batch_current_track_id == caller_track_id`. Queue snapshot is taken at advance time (not batch start), allowing live queue growth during batch run. This prevents double-advance on race conditions between worker completion and watchdog/stop signals.

### 12. Read-Before-Write for Spotify API Calls
`_safe_pause_active_playback()` checks current Spotify playback state before sending a pause command — avoids unnecessary API calls when already paused (reduces rate limit pressure on Spotify Web API). Pattern: query → conditional mutate, not blind mutate.

### 13. `ui_pending_queue` Drain Pattern for Background Thread → Main Thread Communication
Background threads (polling, worker, watchdog) push UI update actions into a thread-safe `collections.deque` protected by `_ui_pending_lock`. A main-thread `ui.timer()` (NiceGUI) drains and executes them via `_batch_drain_ui_queue()`. This prevents "UI updates must be called from main thread" errors. A separate `_ui_context_lock` serializes main-thread access from bg-thread callback sites that interact with NiceGUI async context.

### 14. Interference Detection via Expected Track ID
`_batch_expected_track_id` records what Spotify was told to play. The polling thread compares actual playback track ID against this expectation. If they diverge (interference: user manually changed track, or Spotify played wrong thing), a banner warning is shown and logged. `_batch_watchdog_fired_by_track_id` deduplicates watchdog fires per track.

### 15. Production-Clean Verification
`tests/_verify_production_clean.py` uses a `hasattr` simulation import to verify that development-only attributes (like `_fake_save_track_worker` injection points) are not present in production code paths. This prevents test injection points from accidentally shipping.

### 16. Test Data Isolation
Before each test, `_setup()` resets all batch globals to their idle state and patches `_state.save_analysis_queue` with a no-op lambda. Queue file is fully cleaned between tests. This prevents test data from leaking into the production `cache/analysis_queue.json` file — a real bug that was caught and fixed (`12b3d79`).

### 17. Simulated Annealing for ATSP (`sorting/solver.py`)
The core sorting algorithm uses SA to solve the Asymmetric TSP with anchor constraints. Two move types:
- **2-opt reversal** within a slot (60% probability)
- **Track relocation** between slots (40% probability)

### 18. Module Decomposition via Dependency Injection (2026-07-20)

Three modules were extracted from `playlist_source.py` as pure moves (no behavior changes). Each uses a `configure()` function receiving callbacks from `playlist_source` at module init time to avoid circular imports:

| Extracted Module | Lines | Responsibility |
|---|---|---|
| `analysis/batch_analyzer.py` | 208 | Batch state globals, `_batch_advance_to_next()`, `_stop_batch_analysis()`, watchdog, UI drain queue |
| `ui/audio_viz.py` | 141 | Canvas JS setup, `_update_viz()`, FFT band computation |
| `ui/playlist_highlight.py` | 207 | `_sync_row_highlight()`, `_notify_playing_track()`, auto-expand/collapse, playlist table refs |

Pattern:
```python
# In extracted module:
def configure(callback_fn=None, ...):
    global _callback_fn
    _callback_fn = callback_fn

# In playlist_source.py at module init:
from playlist_arranger.analysis.batch_analyzer import configure
configure(stop_analyzing_fn=_stop_analyzing, ...)
```
This avoids circular imports while keeping the extraction transparent to existing callers.

### 19. Track Playability Filtering (2026-07-20)
`_load_cached_playlist_tracks()` applies `_is_track_playable()` to raw Spotify API results, filtering out local files with no market data before caching. Also detects and auto-deletes stale empty cache files (0 tracks from transient errors) and corrupt cache files (JSON parse failures), forcing clean re-fetch from Spotify API.

### 20. Cache with Invalidation (`sorting/distance.py`)
`_SORTING_CACHE` caches distance matrices keyed by playlist ID and track ID tuple. Invalidation happens when track IDs change.

### 21. Thread-Safe FIFO Queue with Dedupe (`analysis/desc_generator.py`)
A thread-safe background worker queue for description generation uses:
- `queue.Queue` (blocking `.get(timeout=1.0)`) for the FIFO data structure
- `set[str]` for deduplication (O(1) contains check)
- Single `threading.Lock` guarding ALL `_desc_queue_set` mutations + `len()` reads
- `desc_queue_add()`: check-and-add under lock — atomic dedupe, returns `bool`
- `desc_queue_add_many()`: batch add under one lock acquisition
- `desc_queue_clear()`: locks once, drains entire queue with `get_nowait()` in a while loop, clears set, resets processing globals

Worker thread:
- Daemon thread started idempotently (`_desc_worker_start_lock` + boolean guard)
- Blocks on `_desc_queue.get(timeout=1.0)` when queue empty (no busy-loop)
- Removes item from dedupe set AFTER dequeueing (it's now "in-progress", not "queued")
- Updates `desc_generator_current_track_id/name` globals for UI display
- Worker stop: `_desc_worker_stop` threading.Event checked at top of loop and during retry sleeps — clean shutdown, no forceful thread kill

### 22. Live Dialog Update via Module Globals (2026-07-22)
For pushing newly-generated descriptions from a background worker callback into an open dialog:
- `desc_dialog.py` exports `_current_open_track_id` (str|None), `_current_textarea`, `_current_generated_label` module globals
- On dialog open: `show_desc_dialog()` stores track_id + element refs
- On dialog close: `_close_dialog_state()` resets all to None (called via `dialog.on("update:model-value", ...)` because `ui.dialog()` does NOT accept `on_close`)
- Worker callback `_push_desc_to_open_dialog(track_id)`:
  1. Guard: `_current_open_track_id != track_id` → early return (different track or closed)
  2. Guard: `_current_textarea is None` → early return (stale ref after close)
  3. Read fresh `desc_text` from DB via `db.get_track(track_id)`
  4. Set `textarea.value = desc_text` + `generated_label.set_text(f"Generated: {formatted_date}")`
  5. Runs inside `_ui_context_lock` + `_ph._page_client` (same pattern as `_on_analysis_complete`)

Race condition handling:
- Dialog closed between gen start/finish → `_current_open_track_id` is None → skipped
- User swaps dialog from X to Y while X generates → `_current_open_track_id` is Y, not X → skipped
- Same track re-opened → `show_desc_dialog()` refreshes refs to new dialog's elements

### 23. Per-Backend Client Cache with Dict (`llm/client.py`) (2026-07-24)
Previous single-slot `_llm_client` global was replaced with per-backend dict cache:
```python
_llm_clients: dict = {}       # keyed by backend name (ollama/deepseek/mistral)
_llm_models_used: dict = {}   # model name per backend
```
Cache hit checks both client object AND model name match for the specific requested backend. Legacy `_llm_client`/`_llm_backend_used`/`_llm_model_used` module-level names are updated on cache hit for backward compat with `llm_chat()` which reads them directly.

**Why**: Switching backends via the dropdown creates new clients per backend. A single-slot cache would silently return the wrong backend's client on subsequent calls. The dict pattern also handles model-only switches (same backend, different model) by checking `cached_model == model` on cache hit.

### 24. Session-Level In-Memory State Persistence Pattern (2026-07-24)
For remembering UI panel state across collapse/expand cycles within one process lifetime (NOT persisted to disk):
```python
# Module-level globals with sentinel "never touched" values
_gen_last_structure_id: str = DEFAULT
_gen_last_desc_text: str = DEFAULT
_gen_last_desc_touched: bool = False  # True once user has interacted
_gen_last_n: int | None = None      # None = compute from formula
_gen_last_backend: str | None = None  # None = use default
```
Lightweight `on_change` handlers update these globals on every keystroke/change (pure Python variable assignment, zero disk I/O). Render function reads from globals, falling back to defaults only when still at initial/unset state.

**Custom-structure precedence rule**: If structure is "custom" AND the user has never touched the textarea this session (`_gen_last_desc_touched == False`), load from `settings.json` (persisted disk value). Once user types, in-session memory wins. `_gen_last_desc_touched` resets to False when switching TO custom (that's a load, not user typing).

### 25. `ui.notify()` Before Panel.clear() in Async Handlers (2026-07-24)
After an async handler resumes from `asyncio.to_thread`, the UI slot context from the original panel may be stale. Call `ui.notify()` **BEFORE** `_gen_panel.clear()` to avoid RuntimeError. Wrap both in try/except with `logger.exception` as defense-in-depth.
```python
# CORRECT order:
ui.notify("Success", type="positive")
_gen_panel_visible = False
_gen_panel.clear()
```

### 26. Layout Stability: Buttons-First in Rows (2026-07-22)
In NiceGUI `ui.row()`, children are laid out left-to-right in creation order. Place **fixed-width** elements (buttons) BEFORE **variable-width** elements (labels) so label content changes don't shift button positions. Applied in `_render_desc_status()`: buttons ("Stop and clean the queue", "Update all in background") created first, then labels wrapped in a `ui.column()`.

### 27. NiceGUI Dialog Lifecycle Pattern
`ui.dialog()` does NOT accept `on_close` keyword argument. To detect dialog close:
```python
with ui.dialog(value=True) as dialog:
    dialog.on("update:model-value", lambda e: handler() if not e.args else None)
```
`e.args` is falsy when the model-value changes to indicate dialog closed. This fires `_close_dialog_state()` on close only.

## Component Relationships

### Data Flow: Spotify Track Analysis
```
User selects playlist → Spotify API fetches tracks → DB check for missing
→ AnalysisSession plays each track → WASAPI capture → Audio buffer
→ save_track_worker(): features + MERT → SQLite DB + embeddings/*.npy
```

### Data Flow: Local File Analysis
```
User selects folder → scan_folder() finds MP3/FLAC → DB check for missing
→ librosa.load() from disk → save_track_worker(): features + MERT → SQLite DB
```

### Data Flow: Sorting Pipeline
```
Descriptions → Anchor Editor (manual/AI) → Anchor Plan (JSON file)
→ SA Solver loads DB tracks + embeddings → Builds distance matrix
→ Runs 100 SA runs → Returns ordered track list
→ Save to Spotify (new/existing playlist) or Export M3U
```

### Page Navigation
```
Welcome → Spotify Source / Local Source → (Analysis) → (Descriptions)
→ Anchors → Sorting → (Save/Export)
```

Navigation state is managed via global `_current_page` string and `set_page()` + `render_right_panel()` in `main.py`.

## Critical Implementation Paths

### Distance Matrix Construction
`_build_distance_matrix()` in `distance.py` — O(n²) pairwise computation using:
- MERT embedding cosine distance (mood)
- MFCC cosine distance (transition smoothness)
- BPM difference (normalized)
- Camelot wheel distance (key compatibility)
- RMS loudness difference (energy)
- Artist/album penalty

### MERT Embedding Generation
`_mert_embedding()` in `mert.py`:
1. Trim silence from audio
2. Resample to 24kHz (MERT native rate)
3. Truncate to 30 seconds max
4. Pass through Wav2Vec2FeatureExtractor
5. Forward through MERT model
6. Stack last 4 hidden states, mean pool → 768-dim vector
7. Save as `.npy` to `embeddings/` directory

### WASAPI Loopback Capture
`capture.py` uses PyAudioWPatch to create a loopback audio stream. The callback accumulates samples into:
- `audio_deque` — rolling 30-second buffer
- `full_buf` — accumulates entire track (max 10 min)
- `start_buf` — first 10 seconds for start-of-track feature extraction

### LLM Description Generation
`descriptions.py` sends per-track feature summaries to the LLM with system prompt `DESCRIPTION_SYSTEM_PROMPT`. Tracks are batched (by default 10 per request) to reduce API calls.

## File Organization Conventions
- Underscore-prefixed module-level functions are internal to the module
- `HAS_*` module-level booleans check optional dependency availability
- Progress callbacks (`progress_cb(msg)`) are passed through the call chain for UI feedback
- Atomic file writes via `atomic_write_json()` (write to `.tmp`, then rename)