# Active Context

## Current Work Focus
Per-playlist normalization & stats analysis workflow (2026-07-30): Implemented a dedicated "Analyze Statistics" pipeline that computes per-component calibration scales for the distance function using `_robust_range()` on the actual playlist's feature distributions. This replaces hardcoded global divisors for non-trivially-bounded components (transition MFCC, flatness, dynamic range, onset strength) with playlist-specific calibration. The UI now gates "Run Sorting" behind a valid stats cache, with interactive weight sliders, CV signal-quality badges, effective contribution charts, and composite histograms.

## Recent Changes

### 2026-07-30 — Per-playlist calibration & stats analysis workflow

**Phase 1 — Distance matrix diagnostics (earlier in session)**
- `distance.py`: `_compute_distance_stats()`, `_dump_distance_csv()`, `_render_distance_histogram()` for CSV/PNG/histogram diag output
- `solver.py`: Auto-generates CSV + histogram PNG after distance matrix build
- `smart_sorting.py`: Inline histogram display in Sorting Logs panel
- `requirements.txt`: Added `matplotlib==3.10.8`
- `tests/test_distance.py`: 4 new tests (14 total on distance)

**Phase 2 — Per-component normalization audit**
- Cross-playlist transition audit confirmed raw MFCC cos_dist max ~0.25 — hardcoded `/2.0` shrinks effective range too aggressively
- `flatness` sub-component found essentially dead (music flatness 0.00-0.02, not [0,1])
- Produced per-component raw range table identifying 3 issues: flatness (50× smaller than siblings), transition (4.1% effective despite 0.200 weight), mood (dominant but no calibration needed)

**Phase 3 — stats_analysis.py (new module)**
- `StatsResult` + `ComponentCalibration` dataclasses
- `analyze_playlist_stats()` computes calibration scales via `_robust_range()` for: `dynamic_range`, `onset_str`, `flatness`, `transition`. Fixed-range components (bpm/200, key/camelot, energy/60, freq_balance/√2) keep theoretical divisors.
- `save_stats_cache()` with pruning (deletes stale snapshot files for same playlist_id)
- `load_stats_cache()` with snapshot_id + mood_mode validation
- Cache JSON schema: `cache/<playlist_id>_<snapshot_id>_stats.json` containing per-component calibration scales, histogram data, `mood_mode` ("embedding" | "chroma_fallback"), `weights_used`, `last_used_for_sort_at` timestamp

**Phase 4 — Solver integration**
- `solver.py`: `_run_smart_sorting()` now requires `stats_cache` parameter (raises RuntimeError if missing). Reads calibration scales (`flat_scale`, `transition_scale`, `dyn_scale`, `onset_scale`) and user-tuned `weights_used` from cache. Re-saves cache with `last_used_for_sort_at` timestamp after successful sort.
- `distance.py`: `_track_distance()` + `_build_distance_matrix()` accept `flat_scale` and `transition_scale` parameters. Applied as divisors in `d_flat` and `d_transition` before `min(..., 1.0)` clamping.

**Phase 5 — UI: smart_sorting.py full rewrite**
- "Analyze Statistics" button to LEFT of "Run Sorting"
- Button gating: Run Sorting disabled until valid stats cache exists with flatness calibration
- On playlist select: auto-loads `load_stats_cache(playlist_id, snapshot_id)`; "Run Analyze Statistics first" hint if missing
- **Analyze panel** (inside Sorting Logs expansion):
  - Weight sliders (ui.number) per component: mood, bpm, transition, key, energy, texture, freq_balance
  - CV badges: green (CV > 0.20), yellow (0.10-0.20), red (< 0.10) — thresholds as module-level constants
  - Effective contribution table (observed_max × weight) recomputed on weight change
  - Composite 2×4 histogram PNG (7 panels + 1 blank)
  - "Save & Enable Sorting" button — writes current slider weights to cache, collapses to compact summary
- Compact summary state: "Analyzed N tracks · weights saved · avg CV 0.24" + "Re-analyze" button

**Phase 6 — Transition calibration verified**
- Real 61-track playlist: `transition_scale=0.1221` (calibrated from raw MFCC cos_dist → `_robust_range()`). Post-calibration: transition effective contribution = 0.2000 (25.1%), up from 0.0246 (4.1%).
- CV scale-invariant — CV now 0.71 (green) because values properly span [0,1] post-calibration.

**Test suite**: 28 tests all passing (6 stats_analysis + 4 stats_workflow + 14 distance + 3 solver_no_anchors + 1 start_sorting_no_anchors). Stats_workflow tests cover: button gating (disabled without cache, enabled with), analyze triggers correct args, save uses current slider values (not stale cached ones). All per-project rules: mocked external deps, `tmp_path` for cache I/O, no real network/DB access.

## Next Steps
1. Run the full 303-test suite to confirm no regressions from the distance/solver/smart_sorting changes
2. Consider wiring `desc_generator` worker to actually call LLM (placeholder currently)
3. Monitor DeepSeek model migration (legacy `deepseek-chat`/`deepseek-reasoner` retiring)
4. Cross-platform audio capture (macOS/Linux support)

## Active Decisions and Considerations
- **Per-playlist calibration is now REQUIRED for sorting** — solver refuses to run without valid stats cache. This gates sorting behind an explicit user-triggered analysis step (deliberately slow O(n²) operation).
- **Snapshot_id-based cache invalidation** matches Spotify's own change-detection mechanism — automatically invalidates when tracks are added/removed/reordered.
- **Mood_mode invalidation**: Cached `mood_mode` field ("embedding" vs "chroma_fallback") allows caller to detect stale caches when embedding availability changes (e.g., MERT embeddings computed after initial analysis). Load function returns the data; caller gate-keeps.
- **Cache pruning**: `save_stats_cache()` deletes all other `*_stats.json` files for the same playlist_id (different snapshots), preventing unbounded cache growth.
- **CV thresholds** are named constants (`CV_GOOD_THRESHOLD=0.20`, `CV_MODERATE_THRESHOLD=0.10`), not magic numbers.
- **Transition/flatness calibration uses same `_robust_range()` pattern** as existing `dyn_range`/`onset_str` — consistent pipeline.
- MERT model version pinned: `transformers==4.38.0` (not latest) due to MERT-v1-95M compatibility
- CUDA index for RTX 5080: Uses `cu130` (CUDA 13.0 / PyTorch 2.12.1)

## Important Patterns and Preferences
- All dependency versions are strictly pinned (`==`) for reproducible builds
- **Refactor rule**: When extracting internal state, update dependent tests in the SAME commit
- **Per-playlist calibration pattern**: `_robust_range()` on raw feature distributions → store in `ComponentCalibration.calibration_scale` → apply as divisor in `_track_distance()` before `min(..., 1.0)` clamp
- **Cache invalidation via snapshot_id**: Spotify's `snapshot_id` changes on ANY playlist modification — perfect for cache key
- **DI-cached function references**: `mock.patch` on original module functions may not reach DI-cached references — replace the cached reference directly (pattern #18/27 in systemPatterns)
- Progress callbacks (`progress_cb`) pattern throughout for UI feedback
- Atomic file writes (`atomic_write_json`) for all persisted JSON
- Per-thread SQLite connection caching with WAL mode
- Global reactive state in `ui/state.py` for NiceGUI SPA
- Navigation via page name string and conditional rendering

## Learnings and Project Insights
- Distance matrix is the primary bottleneck for large playlists — O(n²) pairwise computation
- SA solver caches distance matrices per playlist ID + track tuple + calibration key
- Spotify playlist reorder API has 100 URI limit; code handles chunking
- MERT embeddings saved as `.npy` files independently from SQLite DB
- Placeholders in anchor plans create "open slots" for SA solver
- **NiceGUI dialog lifecycle**: `ui.dialog()` does NOT accept `on_close` — use `dialog.on("update:model-value", ...)`
- **Layout stability**: Place fixed-width elements BEFORE variable-width ones in `ui.row()`
- **CV is scale-invariant**: Calibration changes effective contribution % but NOT CV — CV reflects genuine data variance
- **Music flatness is 0.00-0.02, not [0,1]**: librosa's theoretical range doesn't match real music — needs per-playlist calibration
- **Raw MFCC cos_dist caps at ~0.25**: Music vectors never reach orthogonal — needs per-playlist calibration, not hardcoded `/2.0`