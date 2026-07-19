"""Audio visualization: spectrogram bands, RMS, peak — sampled from the live
audio capture deque and pushed to a canvas element via JS.

Extracted from playlist_source.py — pure move, no behavior changes.
"""

import logging
import numpy as np
import json as _json

from nicegui import ui
from playlist_arranger.audio import capture as _cap
from playlist_arranger.audio.features import _to_mono

logger = logging.getLogger(__name__)

# ─── Viz-related global state ─────────────────────────────────────────────────

_viz_canvas_id = "pa-viz-canvas"
_viz_sr_label = None
_viz_rms_label = None
_viz_peak_label = None


# ─── Canvas JS setup ──────────────────────────────────────────────────────────

def init_canvas_js(canvas_id: str):
    """Run the one-time canvas setup JS (`_paVizBands` / `_paRedraw`).

    Called from `_build_now_playing_card()` in `playlist_source.py`.
    """
    ui.run_javascript(f'''
        (function() {{
            var c = document.getElementById("{canvas_id}");
            if (!c) return;
            c._paVizBands = [];
            c._paRedraw = function() {{
                var ctx = c.getContext("2d");
                var w = c.width, h = c.height;
                ctx.clearRect(0, 0, w, h);
                var bands = c._paVizBands;
                if (!bands.length) {{
                    ctx.fillStyle = "#BDBDBD";
                    ctx.font = "8px monospace";
                    ctx.textAlign = "center";
                    ctx.fillText("no signal", w/2, h/2+3);
                    return;
                }}
                var bw = (w - 4) / bands.length;
                for (var i = 0; i < bands.length; i++) {{
                    var v = Math.min(1, Math.max(0, bands[i]));
                    var barH = v * (h - 8);
                    var x = 2 + i * bw;
                    ctx.fillStyle = "#9E9E9E";
                    ctx.fillRect(x + 1, h - barH - 2, bw - 2, barH);
                }}
            }};
            c._paRedraw();
        }})();
    ''')


# ─── FFT sampling + JS push ───────────────────────────────────────────────────

def _update_viz():
    """Sample audio deque, compute FFT + RMS, push bands + stats to canvas JS."""
    global _viz_sr_label, _viz_rms_label, _viz_peak_label

    cap = _cap
    if cap is None or cap.audio_deque is None:
        return

    sr = getattr(cap, 'actual_sr', 44100)

    try:
        with cap.audio_lock:
            if cap.audio_deque is None or len(cap.audio_deque) < 64:
                return
            window = np.array(list(cap.audio_deque)[-4096:], dtype=np.float32)
    except Exception:
        return

    if len(window) < 64:
        return

    if hasattr(cap, 'actual_channels') and cap.actual_channels > 1:
        mono = _to_mono(window, cap.actual_channels)
    else:
        mono = window if window.ndim == 1 else window.mean(axis=1)

    if float(np.max(np.abs(mono))) < 1e-10:
        if _viz_sr_label is not None:
            _viz_sr_label.set_text(f"SR: {sr} Hz")
        if _viz_rms_label is not None:
            _viz_rms_label.set_text("RMS: — dB")
        if _viz_peak_label is not None:
            _viz_peak_label.set_text("Pk: — dB")
        ui.run_javascript(
            f"(function(){{var c=document.getElementById('{_viz_canvas_id}');"
            f"if(c){{c._paVizBands=[];c._paRedraw();}}}})()"
        )
        return

    rms = float(np.sqrt(np.mean(np.square(mono))) + 1e-12)
    peak = float(np.max(np.abs(mono)) + 1e-12)
    rms_db = 20.0 * np.log10(rms)
    peak_db = 20.0 * np.log10(peak)

    n_fft = min(512, len(mono))
    fft = np.abs(np.fft.rfft(mono, n=n_fft))
    num_bins = len(fft)
    num_bands = 10
    band_edges = np.logspace(0, np.log10(num_bins), num_bands + 1).astype(int)
    bands = []
    for i in range(num_bands):
        lo, hi = band_edges[i], band_edges[i + 1]
        lo = max(0, min(lo, num_bins - 1))
        hi = max(0, min(hi, num_bins))
        band_val = float(np.mean(fft[lo:hi])) if hi > lo else 0.0
        bands.append(band_val)

    max_val = float(np.max(bands) + 1e-12)
    bands = [min(1.0, b / (max_val * 1.5)) for b in bands] if max_val > 0 else [0.0] * num_bands

    bands_json = _json.dumps(bands)
    js = (
        f"(function(){{"
        f"var c=document.getElementById('{_viz_canvas_id}');"
        f"if(!c)return;"
        f"c._paVizBands={bands_json};"
        f"c._paRedraw();"
        f"}})()"
    )
    ui.run_javascript(js)

    if _viz_sr_label is not None:
        _viz_sr_label.set_text(f"SR: {sr} Hz")
    if _viz_rms_label is not None:
        _viz_rms_label.set_text(f"RMS: {rms_db:.1f} dB")
    if _viz_peak_label is not None:
        _viz_peak_label.set_text(f"Pk: {peak_db:.1f} dB")