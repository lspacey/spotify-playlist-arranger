"""Audio feature extraction functions."""

import numpy as np

try:
    import librosa

    HAS_LIBROSA = True
except ImportError:
    HAS_LIBROSA = False

from playlist_arranger.config import KEY_NAMES, CAMELOT, SAMPLE_RATE, SEG_SECONDS


def _to_mono(raw, channels):
    if channels == 2 and len(raw) % 2 == 0:
        return raw.reshape(-1, 2).mean(axis=1)
    return raw


def _trim_silence(y, sr=SAMPLE_RATE, top_db=40):
    """Trim leading/trailing silence while keeping at least 5 seconds of audio."""
    if y is None or len(y) < sr * 2:
        return y
    try:
        y_trimmed, _ = librosa.effects.trim(y, top_db=top_db)
        if len(y_trimmed) < sr * 5:
            return y
        return y_trimmed
    except Exception:
        return y


def _extract_features(y, sr=SAMPLE_RATE):
    if y is None or len(y) < sr * 2:
        return {"_err": "not enough data"}
    out = {}

    rms = librosa.feature.rms(y=y)[0].mean()
    out["rms_db"] = float(20 * np.log10(max(rms, 1e-9)))
    out["rms_norm"] = float(np.clip((out["rms_db"] + 60) / 60, 0, 1))

    try:
        tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
        out["bpm"] = float(np.atleast_1d(tempo)[0])
        if len(beats) > 2:
            intervals = np.diff(librosa.frames_to_time(beats, sr=sr))
            out["beat_reg"] = float(1.0 / (np.std(intervals) + 1e-6))
        else:
            out["beat_reg"] = 0.0
    except Exception:
        out["bpm"] = 0.0
        out["beat_reg"] = 0.0

    out["centroid_hz"] = float(
        librosa.feature.spectral_centroid(y=y, sr=sr)[0].mean()
    )
    out["rolloff_hz"] = float(
        librosa.feature.spectral_rolloff(y=y, sr=sr, roll_percent=0.95)[0].mean()
    )
    out["bandwidth_hz"] = float(
        librosa.feature.spectral_bandwidth(y=y, sr=sr)[0].mean()
    )
    out["zcr"] = float(librosa.feature.zero_crossing_rate(y)[0].mean())
    out["onset_str"] = float(librosa.onset.onset_strength(y=y, sr=sr).mean())

    S = np.abs(librosa.stft(y))
    freqs = librosa.fft_frequencies(sr=sr)
    e_tot = S.mean(axis=1).sum() + 1e-9
    out["bass"] = float(S[freqs < 200].mean(axis=1).sum() / e_tot)
    out["mid"] = float(
        S[(freqs >= 200) & (freqs < 2000)].mean(axis=1).sum() / e_tot
    )
    out["high"] = float(S[freqs >= 2000].mean(axis=1).sum() / e_tot)

    chroma = librosa.feature.chroma_stft(y=y, sr=sr).mean(axis=1)
    key_idx = int(np.argmax(chroma))
    out["chroma_key"] = KEY_NAMES[key_idx]
    out["chroma_idx"] = key_idx
    out["chroma_vals"] = chroma.tolist()

    try:
        tonal = (
            librosa.feature.tonnetz(y=librosa.effects.harmonic(y), sr=sr)
            .mean(axis=1)
        )
        out["mode"] = "Major" if float(tonal[0]) > 0 else "minor"
        out["camelot"] = CAMELOT.get(
            (key_idx, 1 if out["mode"] == "Major" else 0), "?"
        )
    except Exception:
        out["mode"] = "?"
        out["camelot"] = "?"

    out["mfcc13"] = (
        librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13).mean(axis=1).tolist()
    )
    return out


def _extract_features_full(y, sr=SAMPLE_RATE):
    if y is None or len(y) < sr * 2:
        return {}
    out = _extract_features(y, sr)
    try:
        out["flatness"] = float(librosa.feature.spectral_flatness(y=y)[0].mean())

        y_harm, y_perc = librosa.effects.hpss(y)
        harm_e = float(np.mean(y_harm**2)) + 1e-9
        perc_e = float(np.mean(y_perc**2)) + 1e-9
        out["harm_ratio"] = float(harm_e / (harm_e + perc_e))

        rms_frames = librosa.feature.rms(y=y)[0]
        rms_db_f = 20 * np.log10(np.maximum(rms_frames, 1e-9))
        out["dynamic_range"] = float(
            np.percentile(rms_db_f, 95) - np.percentile(rms_db_f, 5)
        )

        out["chroma_cens"] = (
            librosa.feature.chroma_cens(y=y, sr=sr).mean(axis=1).tolist()
        )
        tgram = librosa.feature.tempogram(y=y, sr=sr)
        out["tempo_complexity"] = float(np.std(tgram.mean(axis=1)))
        out["mfcc20"] = (
            librosa.feature.mfcc(y=y, sr=sr, n_mfcc=20).mean(axis=1).tolist()
        )
    except Exception:
        pass
    return out