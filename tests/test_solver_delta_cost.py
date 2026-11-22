"""Regression & performance tests for SA solver delta-cost optimization."""

import sys
sys.path.insert(0, r"e:\Projects\Spotify_playlists\repository")

from unittest.mock import MagicMock
import pathlib

# Mock nicegui BEFORE importing playlist_arranger modules
try:
    import nicegui  # noqa: F401
except ImportError:
    sys.modules['nicegui'] = MagicMock()
    sys.modules['nicegui.ui'] = MagicMock()

import random
import time
import math

import pytest
import numpy as np

from playlist_arranger.sorting.solver import (
    _path_cost,
    _reversal_delta,
    _inter_slot_move_delta,
    _solve_atsp_with_anchors,
)


# ── Free-TSP branch: reversal delta equivalence ────────────────────────────

def test_reversal_delta_matches_full_path_cost():
    """_reversal_delta must equal (new_cost - old_cost) computed by
    full _path_cost on small synthetic orders.  Verifies equivalence."""
    random.seed(42)
    n = 5
    rng = np.random.RandomState(99)
    D = rng.rand(n, n).astype(np.float64)
    D = (D + D.T) / 2.0
    for i in range(n):
        D[i, i] = 0.0
    for _ in range(100):
        order = list(range(n))
        random.shuffle(order)
        old_cost = _path_cost(order, D)
        i, j = sorted(random.sample(range(n), 2))
        delta = _reversal_delta(order, i, j, D)
        order[i : j + 1] = order[i : j + 1][::-1]
        new_cost = _path_cost(order, D)
        assert abs(delta - (new_cost - old_cost)) < 1e-10, (
            f"Delta mismatch: delta={delta:.10f}, actual={new_cost-old_cost:.10f}"
        )


def test_reversal_delta_handles_boundaries():
    """_reversal_delta handles i==0 and j==len-1 edge cases."""
    D = np.random.RandomState(42).rand(5, 5)
    for i in range(5):
        D[i, i] = 0.0

    order = [0, 1, 2, 3, 4]
    old = _path_cost(order, D)
    delta = _reversal_delta(order, 0, 3, D)
    order[0:4] = order[0:4][::-1]
    new = _path_cost(order, D)
    assert abs(delta - (new - old)) < 1e-10

    order = [0, 1, 2, 3, 4]
    old = _path_cost(order, D)
    delta = _reversal_delta(order, 1, 4, D)
    order[1:5] = order[1:5][::-1]
    new = _path_cost(order, D)
    assert abs(delta - (new - old)) < 1e-10

    order = [0, 1, 2, 3, 4]
    old = _path_cost(order, D)
    delta = _reversal_delta(order, 0, 4, D)
    order[0:5] = order[0:5][::-1]
    new = _path_cost(order, D)
    assert abs(delta - (new - old)) < 1e-10


# ── Anchor-constrained branch: inter-slot move delta equivalence ───────────

def test_inter_slot_move_delta_matches_full_path_cost():
    """_inter_slot_move_delta must match full _path_cost recomputation
    for ~200 randomized inter-slot moves (si != sj) across various
    slot configurations."""
    rng = np.random.RandomState(777)
    random.seed(42)

    n_total = 12
    D = rng.rand(n_total, n_total).astype(np.float64)
    D = (D + D.T) / 2.0
    for i in range(n_total):
        D[i, i] = 0.0

    anchors = [0, 5]
    slots = [True, True, True]

    def build_order(slot_contents, anchors):
        result = []
        for si in range(len(anchors) + 1):
            result.extend(slot_contents[si])
            if si < len(anchors):
                result.append(anchors[si])
        return result

    def full_recompute_delta(slot_contents, si, idx, sj, ins, tid):
        old_order = build_order(slot_contents, anchors)
        old_cost = _path_cost(old_order, D)
        sc_copy = [list(s) for s in slot_contents]
        sc_copy[si].pop(idx)
        sc_copy[sj].insert(ins, tid)
        new_order = build_order(sc_copy, anchors)
        new_cost = _path_cost(new_order, D)
        return new_cost - old_cost, old_order

    test_configs = [
        [[1, 2, 3, 4], [6, 7], [8, 9, 10, 11]],
        [[1, 2], [3, 4, 6, 7], [8, 9, 10, 11]],
        [[1, 2, 3, 4, 6, 7, 8], [9], [10, 11]],
        [[], [1, 2, 3, 4, 6, 7, 8], [9, 10, 11]],
    ]

    for slot_initial in test_configs:
        slot_contents = [list(s) for s in slot_initial]
        order = build_order(slot_contents, anchors)

        for _ in range(50):
            if not any(slot_contents):
                break
            si_candidates = [s for s in range(3) if slot_contents[s]]
            if not si_candidates:
                continue
            si = random.choice(si_candidates)
            sj_candidates = [s for s in range(3) if s != si]
            if not sj_candidates:
                continue
            sj = random.choice(sj_candidates)

            idx = random.randrange(len(slot_contents[si]))
            tid = slot_contents[si][idx]
            ins = random.randrange(len(slot_contents[sj]) + 1)

            delta, new_order = _inter_slot_move_delta(
                si, idx, sj, ins, tid, order, D, slot_contents, anchors,
            )

            ref_delta, old_order = full_recompute_delta(
                slot_contents, si, idx, sj, ins, tid,
            )

            assert abs(delta - ref_delta) < 1e-10, (
                f"_inter_slot_move_delta mismatch (slot sizes "
                f"{[len(s) for s in slot_initial]}):\n"
                f"  si={si} idx={idx} tid={tid} sj={sj} ins={ins}\n"
                f"  delta={delta:.12f}  ref_delta={ref_delta:.12f}\n"
                f"  old_order={old_order}"
            )

            # Also verify that if we apply the move to slot_contents,
            # the new_order from _inter_slot_move_delta matches what
            # build_order produces after the move.
            sc_applied = [list(s) for s in slot_contents]
            sc_applied[si].pop(idx)
            sc_applied[sj].insert(ins, tid)
            expected_new_order = build_order(sc_applied, anchors)
            assert new_order == expected_new_order, (
                "new_order from delta function must match build_order after applying move"
            )


# ── Full solver equivalence: delta vs full-recompute ──────────────────────

def _reference_solve_atsp_free_tsp(all_ids, D, iterations, T_start, T_end):
    """Reference implementation using full _path_cost every iteration.

    Identical to the delta solver's free-TSP branch but with nc = _path_cost
    instead of delta = _reversal_delta, for equivalence verification.
    Matches the exact init logic (50% NN + shuffle, same as delta solver).
    """
    shuffled = list(all_ids)
    random.shuffle(shuffled)
    if random.random() < 0.5:
        from playlist_arranger.sorting.solver import _nearest_neighbor_init
        order = _nearest_neighbor_init(shuffled[1:], shuffled[0], D)
        order = [shuffled[0]] + order
    else:
        order = shuffled
    current_cost = _path_cost(order, D)
    best_order = list(order)
    best_cost = current_cost
    cooling = (T_end / T_start) ** (1.0 / max(iterations, 1))
    T = T_start
    for _ in range(iterations):
        if len(order) < 2:
            break
        i, j = sorted(random.sample(range(len(order)), 2))
        order[i : j + 1] = order[i : j + 1][::-1]
        nc = _path_cost(order, D)
        delta = nc - current_cost
        if delta < 0 or random.random() < math.exp(-delta / T):
            current_cost = nc
            if nc < best_cost:
                best_order = list(order)
                best_cost = nc
        else:
            order[i : j + 1] = order[i : j + 1][::-1]
        T *= cooling
    return best_order


def test_delta_solver_produces_identical_best_cost_as_reference():
    """With a FIXED random seed, the delta-cost solver must produce
    the IDENTICAL best_cost as the full-recompute reference solver."""
    random.seed(42)
    rng = np.random.RandomState(123)
    n = 20
    D = rng.rand(n, n).astype(np.float64)
    D = (D + D.T) / 2.0
    for i in range(n):
        D[i, i] = 0.0

    all_ids = list(range(n))
    # Use <1000 iterations so the periodic full-cost recomputation
    # (which fires at it % 1000 == 0) never triggers — this ensures
    # the delta and reference loops are identical in behavior for
    # a clean equivalence test.
    iterations = 500

    random.seed(42)
    ref_order = _reference_solve_atsp_free_tsp(
        all_ids, D, iterations, T_start=1.0, T_end=1e-4,
    )
    ref_cost = _path_cost(ref_order, D)

    random.seed(42)
    delta_order = _solve_atsp_with_anchors(
        all_ids, anchors=[], slots=[True],
        D=D, iterations=iterations, T_start=1.0, T_end=1e-4,
    )
    delta_cost = _path_cost(delta_order, D)

    assert abs(delta_cost - ref_cost) < 1e-12, (
        f"Delta best_cost ({delta_cost:.12f}) != reference ({ref_cost:.12f})"
    )
    assert len(delta_order) == n
    assert set(delta_order) == set(all_ids)


# ── Performance regression guard ───────────────────────────────────────────

@pytest.mark.timeout(120)
def test_solver_performance_under_threshold():
    """For n=100 with default iterations, the solver should complete
    in under 10 seconds (regression guard against O(n*iters) blowup)."""
    random.seed(42)
    rng = np.random.RandomState(99)
    n = 100
    D = rng.rand(n, n).astype(np.float64)
    D = (D + D.T) / 2.0
    for i in range(n):
        D[i, i] = 0.0

    all_ids = list(range(n))
    t0 = time.perf_counter()
    result = _solve_atsp_with_anchors(
        all_ids, anchors=[], slots=[True],
        D=D, iterations=max(n * 500, 5000),
        T_start=1.0, T_end=1e-4,
    )
    elapsed = time.perf_counter() - t0
    assert len(result) == n
    assert set(result) == set(all_ids)
    assert elapsed < 10.0, (
        f"SA solver for n=100 took {elapsed:.2f}s (expected < 10s)"
    )