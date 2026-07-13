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

### 8. Simulated Annealing for ATSP (`sorting/solver.py`)
The core sorting algorithm uses SA to solve the Asymmetric TSP with anchor constraints. Two move types:
- **2-opt reversal** within a slot (60% probability)
- **Track relocation** between slots (40% probability)

### 7. Cache with Invalidation (`sorting/distance.py`)
`_SORTING_CACHE` caches distance matrices keyed by playlist ID and track ID tuple. Invalidation happens when track IDs change.

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