"""Audio capture via PyAudioWPatch (WASAPI loopback)."""

import collections
import threading
import numpy as np

from playlist_arranger.config import (
    SAMPLE_RATE,
    BUFFER_SECONDS,
    CHUNK,
    SEG_SECONDS,
    FULL_BUF_MAX,
)

# ─── Optional dependency ─────────────────────────────────────────────────────
try:
    import pyaudiowpatch as _pa_mod

    AUDIO_BACKEND = "PyAudioWPatch (WASAPI loopback)"
except ImportError:
    try:
        import pyaudio as _pa_mod

        AUDIO_BACKEND = "PyAudio (fallback)"
    except ImportError:
        _pa_mod = None
        AUDIO_BACKEND = "none"

# ─── Module-level state ──────────────────────────────────────────────────────
actual_channels = 2
actual_sr = SAMPLE_RATE
audio_deque: collections.deque = collections.deque(
    maxlen=SAMPLE_RATE * BUFFER_SECONDS * 2
)
audio_lock = threading.Lock()

full_buf_lock = threading.Lock()
full_buf = None

start_buf_lock = threading.Lock()
start_buf = None
start_buf_done = False


def list_loopback_devices():
    """Return list of all input/loopback devices."""
    if _pa_mod is None:
        return []
    pa = _pa_mod.PyAudio()
    devices = []
    for i in range(pa.get_device_count()):
        dev = pa.get_device_info_by_index(i)
        ch_in = dev.get("maxInputChannels", 0)
        if ch_in > 0:
            devices.append(
                {
                    "index": i,
                    "name": dev["name"],
                    "channels": ch_in,
                    "sr": int(dev.get("defaultSampleRate", SAMPLE_RATE)),
                    "loopback": dev.get("isLoopbackDevice", False),
                }
            )
    pa.terminate()
    return devices


def _make_callback(channels):
    def callback(in_data, frame_count, time_info, status):
        global start_buf, start_buf_done, full_buf
        samples = np.frombuffer(in_data, dtype=np.int16).astype(np.float32) / 32768.0
        with audio_lock:
            audio_deque.extend(samples)
        with start_buf_lock:
            if not start_buf_done:
                chunk_mono = samples[::channels] if channels == 2 else samples
                start_buf = (
                    chunk_mono
                    if start_buf is None
                    else np.concatenate([start_buf, chunk_mono])
                )
                if len(start_buf) >= actual_sr * SEG_SECONDS:
                    start_buf = start_buf[: actual_sr * SEG_SECONDS]
                    start_buf_done = True
        with full_buf_lock:
            chunk_mono = samples[::channels] if channels == 2 else samples
            if full_buf is None:
                full_buf = chunk_mono
            else:
                full_buf = np.concatenate([full_buf, chunk_mono])
            if len(full_buf) > FULL_BUF_MAX:
                full_buf = full_buf[-FULL_BUF_MAX:]
        return (None, _pa_mod.paContinue)

    return callback


def start_audio_capture(device_index=None):
    """Start audio capture on given device (or first loopback)."""
    global actual_channels, actual_sr, audio_deque, FULL_BUF_MAX
    global full_buf, start_buf, start_buf_done
    full_buf = None
    start_buf = None
    start_buf_done = False

    if _pa_mod is None:
        return None, None, "unavailable"

    pa = _pa_mod.PyAudio()
    dev = None

    if device_index is not None:
        try:
            d = pa.get_device_info_by_index(device_index)
            if d.get("maxInputChannels", 0) > 0:
                dev = d
        except Exception:
            pass

    if dev is None:
        for i in range(pa.get_device_count()):
            d = pa.get_device_info_by_index(i)
            if d.get("isLoopbackDevice", False) and d.get("maxInputChannels", 0) > 0:
                dev = d
                break

    if dev is not None:
        ch = min(int(dev.get("maxInputChannels", 2)), 2)
        sr = int(dev.get("defaultSampleRate", SAMPLE_RATE))
        actual_sr = sr
        FULL_BUF_MAX = actual_sr * 10 * 60
        actual_channels = ch
        audio_deque = collections.deque(
            maxlen=actual_sr * BUFFER_SECONDS * actual_channels
        )
        stream = pa.open(
            format=_pa_mod.paInt16,
            channels=ch,
            rate=sr,
            input=True,
            input_device_index=dev["index"],
            frames_per_buffer=CHUNK,
            stream_callback=_make_callback(actual_channels),
        )
        device_name = dev["name"][:60]
    else:
        actual_channels = 1
        audio_deque = collections.deque(maxlen=SAMPLE_RATE * BUFFER_SECONDS)
        stream = pa.open(
            format=_pa_mod.paInt16,
            channels=1,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK,
            stream_callback=_make_callback(actual_channels),
        )
        device_name = "Default input (no loopback)"

    stream.start_stream()
    return pa, stream, device_name


def stop_capture(pa, stream):
    """Stop audio capture and clean up."""
    if stream:
        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass
    if pa:
        try:
            pa.terminate()
        except Exception:
            pass