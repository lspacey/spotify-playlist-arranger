"""MERT neural embedding model wrapper."""

import threading
import warnings
import numpy as np

try:
    from transformers import AutoModel, Wav2Vec2FeatureExtractor
    from transformers import logging as hf_logging
    import torch

    HAS_MERT = True
except ImportError:
    HAS_MERT = False

from playlist_arranger.config import MERT_SR, SAMPLE_RATE

_mert_model = None
_mert_extractor = None
_mert_lock = threading.Lock()
_device = None  # torch device: "cuda" if GPU available, else "cpu"


def _get_device():
    """Return torch device, preferring CUDA if available."""
    global _device
    if _device is None:
        _device = "cuda" if torch.cuda.is_available() else "cpu"
    return _device


def load_mert(progress_cb=None):
    """Load MERT model lazily. progress_cb(msg) for UI feedback.

    The model is moved to GPU if CUDA is available, otherwise stays on CPU.
    """
    global _mert_model, _mert_extractor
    if not HAS_MERT:
        return
    with _mert_lock:
        if _mert_model is not None:
            return
        device = _get_device()
        if progress_cb:
            progress_cb(f"Loading MERT model on {device}...")

        # ── Scoped suppression of known-safe startup noise ──────────────
        # (1) FutureWarning from huggingface_hub (internal default with
        #     transformers 4.38.0 + newer huggingface_hub — our code never
        #     passes resume_download=)
        # (2) transformers INFO/WARNING: nnAudio CQT message (unused
        #     optional dependency) + MERT weight mismatch messages (model
        #     is used purely as frozen feature extractor — no training)
        old_hf_verbosity = hf_logging.get_verbosity()
        hf_logging.set_verbosity_error()
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                ".*resume_download.*",
                FutureWarning,
                module="huggingface_hub",
            )
            _mert_extractor = Wav2Vec2FeatureExtractor.from_pretrained(
                "m-a-p/MERT-v1-95M", trust_remote_code=True
            )
            _mert_model = AutoModel.from_pretrained(
                "m-a-p/MERT-v1-95M", trust_remote_code=True
            )
        hf_logging.set_verbosity(old_hf_verbosity)
        # ── End scoped suppression ─────────────────────────────────────

        _mert_model.to(device)
        _mert_model.eval()
    if progress_cb:
        progress_cb(f"MERT loaded on {device}")


def _mert_embedding(y_mono, sr=SAMPLE_RATE):
    """Compute MERT embedding for mono audio. Returns numpy array or None.

    Moves input tensors to the same device as the model (GPU/CPU).
    """
    if not HAS_MERT or _mert_model is None:
        return None
    try:
        from playlist_arranger.audio.features import _trim_silence
        import librosa

        device = _get_device()
        y_trim = _trim_silence(y_mono, sr=sr, top_db=40)
        y24 = librosa.resample(y_trim, orig_sr=sr, target_sr=MERT_SR)
        y24 = y24[: MERT_SR * 30]
        inputs = _mert_extractor(
            y24, sampling_rate=MERT_SR, return_tensors="pt", padding=True
        )
        # Move inputs to the same device as the model
        inputs = {k: v.to(device) for k, v in inputs.items()}
        inputs.pop("use_return_dict", None)
        with torch.no_grad():
            out = _mert_model(
                **inputs, output_hidden_states=True, return_dict=True
            )
            hidden = torch.stack(out.hidden_states[-4:]).mean(dim=0)
            emb = hidden.mean(dim=1).cpu().squeeze().numpy()
        return emb.tolist()
    except Exception:
        return None
