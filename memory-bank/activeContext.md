# Active Context — Spotify Playlist Arranger

## Current Work Focus
AI anchor generation robustness for large playlists and SA solver performance.

## Recent Changes (2026-08-05)

### AI Anchor Generation — Chunked Batching + Arc Awareness
- **Feature**: `_on_run_generate()` in `anchors.py` now splits large playlists (>150 tracks) into 100-track chunks with proportional anchor allocation per chunk.
- **Arc-position context (Option A)**: Each chunk receives a position hint ("BEGINNING/OPENING", "MIDDLE/TRANSITION", "ENDING/CLOSING") with percentage range and structure description.
- **Lightweight re-order pass (Option B)**: After chunk merge, sends anchor names + BPM/Key/Loudness only (no full descriptions) to reorder into final structure arc. Validates same-set invariant, falls back gracefully.
- **Per-chunk progress**: Dismiss-based notification tracking (`_progress_notify` + `_dismiss_progress()`) with `timeout=0` on ongoing, `timeout=3000` on success/warning.
- **Backend propagation fix**: `_try_reorder_anchor_positions` now receives `backend`/`model` parameters — no longer silently switches to settings.json default mid-generation.
- **Test suite**: 119 tests covering chunked batching, reorder pass, notification dismiss, progress behavioral test.

### Manual Anchor Input ("Set These Anchors")
- **Feature**: `_on_set_anchors()` with comma-separated integer validation (all-or-nothing, 6 validation layers).
- Placeholder separator inserted between old and new anchor groups.
- Input cleared on success, auto-saves to disk.

### SA Solver — Delta-Cost Performance Fix
- **Critical performance fix**: `_path_cost(order, D)` was called on every SA iteration (O(n) per iteration). Replaced with:
  - `_reversal_delta()` — O(j-i) reversal cost delta (free-TSP and within-slot branches)
  - `_inter_slot_move_delta()` — O(1) inter-slot track relocation delta (at most 6 edges)
- **Float-drift safety**: Periodic `_path_cost()` recomputation every 1000 iterations, corrects if delta drifts beyond 1e-6 * (1 + |full|).
- **Performance**: Drops from O(n × iters) per run to O(iters) per run.
- **Test suite**: `tests/test_solver_delta_cost.py` with 5 tests (reversal delta equivalence, inter-slot move equivalence, solver equivalence, performance guard).

## Next Steps
- Wire `desc_generator` worker to actually call LLM (placeholder currently)
- Wire `run_descriptions()` to `desc_queue_add_many()`
- Cross-platform audio capture (macOS/Linux support)
- Export sorted playlist as M3U with relative paths
- Cached distance matrix persistence across app restarts

## Active Decisions
- **Delta-cost SA solver** is now the ONLY implementation — the full-recompute reference exists only in tests for equivalence verification.
- **`_inter_slot_move_delta`** is a module-level function (extracted for testability); the SA loop calls it directly with slot_contents and anchors.
- **Per-chunk progress notifications** use handle tracking + manual dismiss, not timeout-based auto-clear — prevents stacking but requires disciplined dismiss-before-create ordering.