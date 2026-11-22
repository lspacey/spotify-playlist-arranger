"""Unit tests for seek-back detection and analyze-mode start logic."""

import pathlib
import sys
import unittest

_repo_root = pathlib.Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from unittest.mock import patch

from playlist_arranger.ui.pages import playlist_source as _ps


def _new_state(last_progress_ms=50000):
    return {
        "last_progress_ms": last_progress_ms,
        "last_timestamp": 1000,
        "pending_seek_back_from_ms": None,
    }


# ── Seek-back state machine ──────────────────────────────────────────────────

def test_single_downward_step_is_not_seek_back():
    state = _new_state()
    action = _ps._classify_seek_back_step(
        state, tid="t1", last_track_id="t1",
        progress_ms=45000, current_type="track", current_timestamp=2000,
    )
    assert action == "possible"
    assert state["pending_seek_back_from_ms"] == 50000


def test_two_consecutive_downward_steps_are_seek_back():
    state = _new_state()
    a1 = _ps._classify_seek_back_step(
        state, tid="t1", last_track_id="t1",
        progress_ms=45000, current_type="track", current_timestamp=2000,
    )
    assert a1 == "possible"
    a2 = _ps._classify_seek_back_step(
        state, tid="t1", last_track_id="t1",
        progress_ms=40000, current_type="track", current_timestamp=3000,
    )
    assert a2 == "drain"


def test_forward_step_clears_pending():
    state = _new_state()
    _ps._classify_seek_back_step(
        state, tid="t1", last_track_id="t1",
        progress_ms=45000, current_type="track", current_timestamp=2000,
    )
    assert state["pending_seek_back_from_ms"] == 50000
    action = _ps._classify_seek_back_step(
        state, tid="t1", last_track_id="t1",
        progress_ms=46000, current_type="track", current_timestamp=3000,
    )
    assert action is None
    assert state["pending_seek_back_from_ms"] is None


def test_degenerate_snapshot_ignored():
    state = _new_state()
    action = _ps._classify_seek_back_step(
        state, tid=None, last_track_id="t1",
        progress_ms=1000, current_type="unknown", current_timestamp=2000,
    )
    assert action is None
    assert state["last_progress_ms"] == 50000  # unchanged


def test_stale_snapshot_ignored_and_warns():
    state = _new_state()
    action = _ps._classify_seek_back_step(
        state, tid="t1", last_track_id="t1",
        progress_ms=45000, current_type="track", current_timestamp=1000,  # unchanged ts
    )
    assert action == "stale"
    assert state["last_progress_ms"] == 50000  # unchanged

    with unittest.TestCase().assertLogs(
        "playlist_arranger.ui.pages.playlist_source", level="INFO"
    ) as logs:
        new_last = _ps._log_stale_snapshot(1000, 0.0)
    assert new_last > 0.0
    assert any("frozen" in line for line in logs.output)


# ── _start_analyzing() ───────────────────────────────────────────────────────

def _save_modes():
    return _ps._listen_mode, _ps._analyze_mode


def _restore_modes(saved):
    _ps._listen_mode, _ps._analyze_mode = saved


def test_start_analyzing_false_when_listen_inactive():
    saved = _save_modes()
    _ps._listen_mode = 0
    _ps._analyze_mode = 0
    try:
        with unittest.TestCase().assertLogs(
            "playlist_arranger.ui.pages.playlist_source", level="WARNING"
        ) as logs:
            result = _ps._start_analyzing()
        assert result is False
        assert any("listen mode is not active" in line for line in logs.output)
    finally:
        _restore_modes(saved)


def test_start_analyzing_true_when_active():
    saved = _save_modes()
    _ps._listen_mode = 1
    _ps._analyze_mode = 0
    try:
        with patch.object(_ps._live_ctx, "start_poll_thread") as m_start, \
             patch.object(_ps, "_log_analyze_started"):
            result = _ps._start_analyzing()
        assert result is True
        assert _ps._analyze_mode == 1
        assert m_start.call_count == 1
    finally:
        _restore_modes(saved)


def test_start_analyzing_true_idempotent():
    saved = _save_modes()
    _ps._listen_mode = 1
    _ps._analyze_mode = 1
    try:
        with patch.object(_ps._live_ctx, "start_poll_thread") as m_start:
            result = _ps._start_analyzing()
        assert result is True
        assert m_start.call_count == 0  # no second thread
    finally:
        _restore_modes(saved)
