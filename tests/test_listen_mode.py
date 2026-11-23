"""Unit tests for seek-back detection and analyze-mode start logic."""

import pathlib
import sys
import unittest

_repo_root = pathlib.Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from unittest.mock import patch

from playlist_arranger.config import FREEZE_MIN_STALL_SECONDS, FREEZE_STALL_POLLS, POLL_FAST
from playlist_arranger.ui.pages import playlist_source as _ps


def _new_state(last_progress_ms=50000, now=100.0):
    return {
        "last_progress_ms": last_progress_ms,
        "pending_seek_back_from_ms": None,
        "no_progress_steps": 0,
        "last_progress_change_at": now,
    }


# ── Seek-back state machine ──────────────────────────────────────────────────

def test_single_downward_step_is_not_seek_back():
    state = _new_state()
    action = _ps._classify_seek_back_step(
        state, tid="t1", last_track_id="t1",
        progress_ms=45000, current_type="track", is_playing=True, now=100.0,
    )
    assert action == "possible"
    assert state["pending_seek_back_from_ms"] == 50000
    assert state["last_progress_ms"] == 45000


def test_two_consecutive_downward_steps_are_seek_back():
    """Two consecutive regressions confirm a seek-back.  The Spotify
    ``timestamp`` stays constant for a whole track and is deliberately not an
    input to the state machine."""
    state = _new_state()
    a1 = _ps._classify_seek_back_step(
        state, tid="t1", last_track_id="t1",
        progress_ms=45000, current_type="track", is_playing=True, now=100.0,
    )
    assert a1 == "possible"
    a2 = _ps._classify_seek_back_step(
        state, tid="t1", last_track_id="t1",
        progress_ms=40000, current_type="track", is_playing=True, now=102.0,
    )
    assert a2 == "drain"
    assert state["last_progress_ms"] == 40000


def test_forward_step_clears_pending():
    state = _new_state()
    _ps._classify_seek_back_step(
        state, tid="t1", last_track_id="t1",
        progress_ms=45000, current_type="track", is_playing=True, now=100.0,
    )
    assert state["pending_seek_back_from_ms"] == 50000
    action = _ps._classify_seek_back_step(
        state, tid="t1", last_track_id="t1",
        progress_ms=46000, current_type="track", is_playing=True, now=102.0,
    )
    assert action is None
    assert state["pending_seek_back_from_ms"] is None


def test_progress_increasing_is_never_stale_and_never_warns():
    """Same (unchanged) Spotify timestamp + increasing progress_ms + playing —
    normal playback.  Regression test for the false 'frozen' warning: an
    unchanged timestamp must never be treated as a freeze."""
    state = _new_state(now=100.0)
    actions = []
    with patch.object(_ps.logger, "info") as m_info:
        for i in range(1, 6):
            actions.append(_ps._classify_seek_back_step(
                state, tid="t1", last_track_id="t1",
                progress_ms=50000 + i * POLL_FAST * 1000,
                current_type="track", is_playing=True,
                now=100.0 + i * POLL_FAST,
            ))
    assert actions == [None] * 5
    assert state["no_progress_steps"] == 0
    assert state["last_progress_ms"] == 50000 + 5 * POLL_FAST * 1000
    assert m_info.call_count == 0  # no "frozen" warning


def test_frozen_progress_after_n_polls_is_stale():
    state = _new_state(now=100.0)
    actions = []
    for i in range(1, FREEZE_STALL_POLLS + 1):
        actions.append(_ps._classify_seek_back_step(
            state, tid="t1", last_track_id="t1",
            progress_ms=50000, current_type="track", is_playing=True,
            now=100.0 + i * POLL_FAST,
        ))
    assert actions[:-1] == [None] * (FREEZE_STALL_POLLS - 1)
    assert actions[-1] == "stale"
    assert state["no_progress_steps"] >= FREEZE_STALL_POLLS


def test_polls_faster_than_configured_interval_do_not_trip_freeze():
    """N unchanged polls are not enough — the elapsed time must also cover the
    configured poll interval (POLL_FAST/POLL_NORMAL), so a tight loop cannot
    produce false freezes."""
    state = _new_state(now=100.0)
    for _ in range(FREEZE_STALL_POLLS + 2):
        action = _ps._classify_seek_back_step(
            state, tid="t1", last_track_id="t1",
            progress_ms=50000, current_type="track", is_playing=True,
            now=100.0,  # no wall-clock time passes between polls
        )
        assert action is None
    assert FREEZE_MIN_STALL_SECONDS > 0


def test_paused_playback_is_not_stale():
    state = _new_state(now=100.0)
    for i in range(1, FREEZE_STALL_POLLS + 4):
        action = _ps._classify_seek_back_step(
            state, tid="t1", last_track_id="t1",
            progress_ms=50000, current_type="track", is_playing=False,
            now=100.0 + i * POLL_FAST,
        )
        assert action is None


def test_progress_tracking_resumes_after_freeze():
    """A freeze result must not stop the state machine: progress updates keep
    being recorded and a later increase clears the stall counter."""
    state = _new_state(now=100.0)
    action = None
    for i in range(1, FREEZE_STALL_POLLS + 1):
        action = _ps._classify_seek_back_step(
            state, tid="t1", last_track_id="t1",
            progress_ms=50000, current_type="track", is_playing=True,
            now=100.0 + i * POLL_FAST,
        )
    assert action == "stale"

    action = _ps._classify_seek_back_step(
        state, tid="t1", last_track_id="t1",
        progress_ms=60000, current_type="track", is_playing=True,
        now=100.0 + (FREEZE_STALL_POLLS + 1) * POLL_FAST,
    )
    assert action is None
    assert state["no_progress_steps"] == 0
    assert state["last_progress_ms"] == 60000


def test_freeze_warning_is_rate_limited_to_once_per_30s():
    with patch.object(_ps.logger, "info") as m_info:
        first = _ps._log_stale_snapshot(1234, 0.0)
    assert m_info.call_count == 1
    assert "frozen" in m_info.call_args.args[0]
    assert first > 0.0

    with patch.object(_ps.logger, "info") as m_info:
        second = _ps._log_stale_snapshot(1234, first)
    assert m_info.call_count == 0  # suppressed inside the 30 s window
    assert second == first


def test_degenerate_snapshot_ignored():
    state = _new_state(now=100.0)
    # track_id None (unknown item)
    for i in range(1, FREEZE_STALL_POLLS + 3):
        action = _ps._classify_seek_back_step(
            state, tid=None, last_track_id="t1",
            progress_ms=50000, current_type="unknown", is_playing=True,
            now=100.0 + i * POLL_FAST,
        )
        assert action is None
    # Non-track type with a valid id (e.g. a podcast episode)
    action = _ps._classify_seek_back_step(
        state, tid="ep1", last_track_id="ep1",
        progress_ms=1000, current_type="episode", is_playing=True, now=200.0,
    )
    assert action is None
    assert state["pending_seek_back_from_ms"] is None


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
