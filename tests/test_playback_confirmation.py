"""Unit tests for confirmation-driven playback and the batch start timeout.

Covers ``confirm_playback()``, the new ``play_track_on_device()`` ladder,
``_player_request()`` and ``batch_check_start_timeout()`` — all with mocks and
no real network calls.
"""

import pathlib
import sys
import unittest

_repo_root = pathlib.Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from unittest.mock import MagicMock, patch

from playlist_arranger.sources.spotify_source import (
    confirm_playback,
    play_track_on_device,
    _player_request,
)
from playlist_arranger.analysis import batch_analyzer as _ba
from playlist_arranger.ui import state as _state
from playlist_arranger.ui.pages import playlist_source as _ps  # noqa: F401  (configures _ba)


def _snap(is_playing, progress_ms=100, track_id="abc", device_id="dev1", **kw):
    s = {
        "is_playing": is_playing,
        "progress_ms": progress_ms,
        "currently_playing_type": "track",
        "track_id": track_id,
        "track_name": "Track",
        "context_uri": None,
        "device_id": device_id,
        "device_name": "Dev",
        "device_type": "Computer",
        "device_is_active": True,
        "device_is_restricted": False,
        "device_is_private_session": False,
        "device_volume_percent": 50,
        "disallows": {},
    }
    s.update(kw)
    return s


# ── confirm_playback() ───────────────────────────────────────────────────────

def test_confirm_playback_confirms_on_third_poll():
    fake_sp = MagicMock()
    seq = [None, None, _snap(True, progress_ms=100, track_id="abc", device_id="dev1")]
    with patch("playlist_arranger.sources.spotify_source.playback_snapshot", side_effect=seq), \
         patch("playlist_arranger.sources.spotify_source.time.sleep"):
        confirmed, snap = confirm_playback(fake_sp, device_id="dev1", expected_track_id="abc")
    assert confirmed is True
    assert snap["track_id"] == "abc"


def test_confirm_playback_times_out_when_not_playing():
    fake_sp = MagicMock()
    with patch("playlist_arranger.sources.spotify_source.playback_snapshot",
               return_value=_snap(False, progress_ms=0)):
        with unittest.TestCase().assertLogs(
            "playlist_arranger.sources.spotify_source", level="WARNING"
        ) as logs:
            confirmed, snap = confirm_playback(
                fake_sp, device_id="dev1", expected_track_id="abc",
                timeout_s=0.0, interval_s=0.0,
            )
    assert confirmed is False
    assert any("TIMEOUT" in line for line in logs.output)


def test_confirm_playback_track_mismatch():
    fake_sp = MagicMock()
    with patch("playlist_arranger.sources.spotify_source.playback_snapshot",
               return_value=_snap(True, progress_ms=100, track_id="other")):
        confirmed, snap = confirm_playback(
            fake_sp, device_id="dev1", expected_track_id="abc",
            timeout_s=0.0, interval_s=0.0,
        )
    assert confirmed is False


# ── play_track_on_device() ───────────────────────────────────────────────────

def _device(id_="dev1", is_active=True, is_restricted=False):
    return {
        "id": id_, "name": "Dev", "type": "Computer",
        "is_active": is_active, "is_restricted": is_restricted, "volume_percent": 50,
    }


def test_play_track_on_device_escalates_and_raises():
    fake_sp = MagicMock()
    with patch("playlist_arranger.sources.spotify_source._get_device_list",
               return_value=[_device()]), \
         patch("playlist_arranger.sources.spotify_source._player_request",
               return_value=(204, None, "")) as mpr, \
         patch("playlist_arranger.sources.spotify_source.confirm_playback",
               return_value=(False, {})) as mcp, \
         patch("playlist_arranger.sources.spotify_source.time.sleep"):
        try:
            play_track_on_device(fake_sp, "spotify:track:abc", device_id="dev1")
            raised = False
        except RuntimeError as exc:
            raised = True
            assert "paths_attempted" in str(exc)

    assert raised is True
    order = [(c.args[1], c.args[2]) for c in mpr.call_args_list]
    assert order == [
        ("PUT", "me/player/play"),
        ("PUT", "me/player"),
        ("PUT", "me/player/play"),
        ("POST", "me/player/queue"),
        ("POST", "me/player/next"),
    ]
    assert mcp.call_count == 3


def test_play_track_on_device_confirms_play_without_fallback():
    fake_sp = MagicMock()
    snap = _snap(True)
    with patch("playlist_arranger.sources.spotify_source._get_device_list",
               return_value=[_device()]), \
         patch("playlist_arranger.sources.spotify_source._player_request",
               return_value=(204, None, "")) as mpr, \
         patch("playlist_arranger.sources.spotify_source.confirm_playback",
               return_value=(True, snap)), \
         patch("playlist_arranger.sources.spotify_source.time.sleep"):
        path, snapshot = play_track_on_device(fake_sp, "spotify:track:abc", device_id="dev1")

    assert path == "play"
    assert snapshot["track_id"] == "abc"
    assert mpr.call_count == 1


def test_play_track_on_device_raises_when_device_absent():
    fake_sp = MagicMock()
    with patch("playlist_arranger.sources.spotify_source._get_device_list", return_value=[]), \
         patch("playlist_arranger.sources.spotify_source._player_request") as mpr:
        try:
            play_track_on_device(fake_sp, "spotify:track:abc", device_id="dev1")
            raised = False
        except RuntimeError as exc:
            raised = True
            assert "not in the device list" in str(exc)

    assert raised is True
    assert mpr.call_count == 0


def test_play_track_on_device_raises_when_device_restricted():
    fake_sp = MagicMock()
    with patch("playlist_arranger.sources.spotify_source._get_device_list",
               return_value=[_device(is_restricted=True)]), \
         patch("playlist_arranger.sources.spotify_source._player_request") as mpr:
        try:
            play_track_on_device(fake_sp, "spotify:track:abc", device_id="dev1")
            raised = False
        except RuntimeError as exc:
            raised = True
            assert "restricted" in str(exc)

    assert raised is True
    assert mpr.call_count == 0


# ── _player_request() ────────────────────────────────────────────────────────

def test_player_request_returns_204_none_empty():
    fake_sp = MagicMock()
    fake_sp.auth_manager.get_access_token.return_value = "token"
    resp = MagicMock()
    resp.status_code = 204
    resp.text = ""
    with patch("requests.get", return_value=resp):
        status, data, text = _player_request(fake_sp, "GET", "me/player")

    assert status == 204
    assert data is None
    assert text == ""


# ── batch_check_start_timeout() ──────────────────────────────────────────────

def _setup_batch():
    _ba._batch_processing = False
    _ba._batch_expected_track_id = None
    _ba._batch_play_issued_at = 0.0
    _ba._batch_current_track_id = None
    _state.sp = None
    with _ps._ui_pending_lock:
        _ps._ui_pending_queue.clear()


def test_batch_check_start_timeout_returns_false_before_timeout():
    _setup_batch()
    _ba._batch_processing = True
    _ba._batch_expected_track_id = "track1"
    with patch("playlist_arranger.analysis.batch_analyzer.time.time", return_value=1000.0):
        _ba._batch_play_issued_at = 990.0  # 10 s elapsed < 20 s
        assert _ba.batch_check_start_timeout() is False


def test_batch_check_start_timeout_stops_after_timeout():
    _setup_batch()
    saved_stop = _ba._stop_batch_analysis
    stop_calls = []
    _ba._stop_batch_analysis = lambda: stop_calls.append(1)
    try:
        _ba._batch_processing = True
        _ba._batch_expected_track_id = "track1"
        with patch("playlist_arranger.analysis.batch_analyzer.time.time", return_value=1000.0):
            _ba._batch_play_issued_at = 970.0  # 30 s elapsed > 20 s
            assert _ba.batch_check_start_timeout() is True
        assert len(stop_calls) == 1
    finally:
        _ba._stop_batch_analysis = saved_stop


def test_batch_check_start_timeout_returns_false_when_inactive():
    _setup_batch()

    _ba._batch_processing = False
    _ba._batch_expected_track_id = "track1"
    _ba._batch_play_issued_at = 970.0
    assert _ba.batch_check_start_timeout() is False

    _ba._batch_processing = True
    _ba._batch_expected_track_id = None
    _ba._batch_play_issued_at = 970.0
    assert _ba.batch_check_start_timeout() is False

    _ba._batch_processing = True
    _ba._batch_expected_track_id = "track1"
    _ba._batch_play_issued_at = 0.0
    assert _ba.batch_check_start_timeout() is False


# ── resume rung ──────────────────────────────────────────────────────────────

def test_play_track_on_device_resume_rung():
    fake_sp = MagicMock()
    paused = _snap(False, progress_ms=0, track_id="abc", device_id="dev1",
                   disallows={"pausing": True})
    playing = _snap(True, progress_ms=100, track_id="abc", device_id="dev1")
    with patch("playlist_arranger.sources.spotify_source._get_device_list",
               return_value=[_device()]), \
         patch("playlist_arranger.sources.spotify_source._player_request",
               return_value=(204, None, "")) as mpr, \
         patch("playlist_arranger.sources.spotify_source.confirm_playback",
               side_effect=[(False, paused), (True, playing)]), \
         patch("playlist_arranger.sources.spotify_source.time.sleep"):
        path, snapshot = play_track_on_device(fake_sp, "spotify:track:abc", device_id="dev1")

    assert path == "resume"
    assert snapshot["track_id"] == "abc"

    calls = mpr.call_args_list
    assert len(calls) == 2
    # 1st: play with uris payload; 2nd: bodyless resume.
    assert calls[0].args[1] == "PUT" and calls[0].args[2] == "me/player/play"
    assert calls[0].kwargs.get("payload") == {"uris": ["spotify:track:abc"]}
    assert calls[1].args[1] == "PUT" and calls[1].args[2] == "me/player/play"
    assert calls[1].kwargs.get("payload") is None  # bodyless


def test_play_track_on_device_resume_skipped_when_resuming_disallowed():
    fake_sp = MagicMock()
    snap = _snap(False, progress_ms=0, track_id="abc", device_id="dev1",
                 disallows={"resuming": True})
    with patch("playlist_arranger.sources.spotify_source._get_device_list",
               return_value=[_device()]), \
         patch("playlist_arranger.sources.spotify_source._player_request",
               return_value=(204, None, "")) as mpr, \
         patch("playlist_arranger.sources.spotify_source.confirm_playback",
               return_value=(False, snap)), \
         patch("playlist_arranger.sources.spotify_source.time.sleep"):
        try:
            play_track_on_device(fake_sp, "spotify:track:abc", device_id="dev1")
            raised = False
        except RuntimeError:
            raised = True

    assert raised is True

    # Resume rung skipped (no bodyless play); falls through to transfer + queue.
    order = [(c.args[1], c.args[2]) for c in mpr.call_args_list]
    assert order == [
        ("PUT", "me/player/play"),
        ("PUT", "me/player"),
        ("PUT", "me/player/play"),
        ("POST", "me/player/queue"),
        ("POST", "me/player/next"),
    ]
    for c in mpr.call_args_list:
        if c.args[2] == "me/player/play":
            assert c.kwargs.get("payload") == {"uris": ["spotify:track:abc"]}


# ── start-offset warning ─────────────────────────────────────────────────────

def test_confirm_playback_offset_warning_emitted():
    fake_sp = MagicMock()
    snap = _snap(True, progress_ms=71184)
    with patch("playlist_arranger.sources.spotify_source.playback_snapshot", return_value=snap):
        with unittest.TestCase().assertLogs(
            "playlist_arranger.sources.spotify_source", level="WARNING"
        ) as logs:
            confirmed, _ = confirm_playback(
                fake_sp, device_id="dev1", expected_track_id="abc",
                timeout_s=0.0, interval_s=0.0,
            )
    assert confirmed is True
    assert any("confirmed at progress_ms=71184" in line for line in logs.output)


def test_confirm_playback_no_offset_warning():
    fake_sp = MagicMock()
    snap = _snap(True, progress_ms=120)
    with patch("playlist_arranger.sources.spotify_source.playback_snapshot", return_value=snap):
        with unittest.TestCase().assertLogs(
            "playlist_arranger.sources.spotify_source", level="INFO"
        ) as logs:
            confirmed, _ = confirm_playback(
                fake_sp, device_id="dev1", expected_track_id="abc",
                timeout_s=0.0, interval_s=0.0,
            )
    assert confirmed is True
    assert not any("confirmed at progress_ms" in line for line in logs.output)
