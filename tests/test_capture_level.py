"""Unit tests for the pre-submit RMS capture-level gate in live_buffer."""

import pathlib
import sys
import threading
import unittest

_repo_root = pathlib.Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

import numpy as np
from unittest.mock import patch

from playlist_arranger.analysis.live_buffer import LiveAnalyzeContext, AnalyzeBuffer


def _fake_save_track_worker(track_info, playlist_name, playlist_uri, y_full,
                            y_start_snap=None, status_cb=None, sr_override=None):
    pass


class MockCapture:
    actual_sr = 44100
    actual_channels = 2
    audio_deque = None
    audio_lock = threading.Lock()


def _new_ctx():
    lock = threading.Lock()
    cap = MockCapture()
    ctx = LiveAnalyzeContext(
        mode_lock=lock,
        is_analyze_mode=lambda: True,
        capture_module=cap,
        save_track_worker_fn=_fake_save_track_worker,
    )
    return ctx, lock


def _amplitude_for_db(db):
    return 10.0 ** (db / 20.0)


def _make_buf(amplitude):
    buf = AnalyzeBuffer(
        track_id="t1",
        track_info={"id": "t1", "name": "Test", "duration_ms": 300000},
        sample_rate=22050,
    )
    buf.chunks = [np.full(22050, amplitude, dtype=np.float32)]
    buf.samples_count = 22050
    return buf


def test_buffer_below_low_signal_rejected():
    ctx, lock = _new_ctx()
    discarded = []
    ctx.on_buffer_discarded_cb = lambda ti: discarded.append(ti)
    buf = _make_buf(_amplitude_for_db(-52.0))  # below -50 → reject
    with patch.object(ctx, "_submit_analyze_task") as m_submit:
        with lock:
            result = ctx._flush_analyze_buffer(buf, 0.95)
    assert result is False
    assert m_submit.call_count == 0
    assert len(discarded) == 1  # discard path fired


def test_buffer_low_level_warns_and_submits():
    ctx, lock = _new_ctx()
    ctx.on_buffer_discarded_cb = lambda ti: None
    buf = _make_buf(_amplitude_for_db(-40.0))  # between -50 and -35 → warn
    with patch.object(ctx, "_submit_analyze_task") as m_submit:
        with unittest.TestCase().assertLogs(
            "playlist_arranger.analysis.live_buffer", level="WARNING"
        ) as logs:
            with lock:
                result = ctx._flush_analyze_buffer(buf, 0.95)
    assert result is True
    assert m_submit.call_count == 1
    assert any("low level" in line for line in logs.output)


def test_buffer_normal_submits_silently():
    ctx, lock = _new_ctx()
    ctx.on_buffer_discarded_cb = lambda ti: None
    buf = _make_buf(_amplitude_for_db(-18.0))  # above -35 → silent
    with patch.object(ctx, "_submit_analyze_task") as m_submit:
        with unittest.TestCase().assertLogs(
            "playlist_arranger.analysis.live_buffer", level="INFO"
        ) as logs:
            with lock:
                result = ctx._flush_analyze_buffer(buf, 0.95)
    assert result is True
    assert m_submit.call_count == 1
    assert not any("low level" in line for line in logs.output)
    assert not any("rejected" in line for line in logs.output)
