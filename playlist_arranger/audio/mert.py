"""MERT neural embedding model wrapper."""

import threading
import numpy as np

try:
    from transformers import AutoModel, Wav2Vec2FeatureExtractor
    import torch

    HAS_MERT = True
except ImportError:
    HAS_MERT = False

from playlist_arranger.config import MERT_SR, SAMPLE_RATE

_mert_model = None
_mert_extractor = None
_mert_lock = threading.Lock()


def load_mert(progress_cb=None):
    """Load MERT model lazily. progress_cb(msg) for UI feedback."""
    global _mert_model, _mert_extractor
    if not HAS_MERT:
        return
    with _mert_lock:
        if _mert_model is not None:
            return
        if progress_cb:
            progress_cb("Loading MERT model...")
        _mert_extractor = Wav2Vec2FeatureExtractor.from_pretrained(
            "m-a-p/MERT-v1-95M", trust_remote_code=True
        )
        _mert_model = AutoModel.from_pretrained(
            "m-a-p/MERT-v1-95M", trust_remote_code=True
        )
        _mert_model.eval()
    if progress_cb:
        progress_cb("MERT loaded")


def _mert_embedding(y_mono, sr=SAMPLE_RATE):
    """Compute MERT embedding for mono audio. Returns numpy array or None."""
    if not HAS_MERT or _mert_model is None:
        return None
    try:
        from playlist_arranger.audio.features import _trim_silence
        import librosa

        y_trim = _trim_silence(y_mono, sr=sr, top_db=40)
        y24 = librosa.resample(y_trim, orig_sr=sr, target_sr=MERT_SR)
        y24 = y24[: MERT_SR * 30]
        inputs = _mert_extractor(
            y24, sampling_rate=MERT_SR, return_tensors="pt", padding=True
        )
        inputs.pop("use_return_dict", None)
        with torch.no_grad():
            out = _mert_model(
                **inputs, output_hidden_states=True, return_dict=True
            )
            hidden = torch.stack(out.hidden_states[-4:]).mean(dim=0)
            emb = hidden.mean(dim=1).squeeze().numpy()
        return emb.tolist()
    except Exception:
        return None