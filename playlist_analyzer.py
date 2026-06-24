#!/usr/bin/env python3
"""
Spotify Playlist Analyzer
Browses own playlists, shows missing tracks, analyzes them via audio capture + MERT.
"""

import os, sys, time, threading, collections, json, pathlib, datetime, re
import numpy as np

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["HF_HUB_CACHE"] = str(pathlib.Path.home() / ".cache" / "huggingface" / "hub")

_db_lock = threading.Lock()

# ─── Optional dependencies ────────────────────────────────────────────────────

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

try:
    import librosa
    HAS_LIBROSA = True
except ImportError:
    HAS_LIBROSA = False

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
    HAS_SPOTIPY = True
except ImportError:
    HAS_SPOTIPY = False

try:
    from transformers import AutoModel, Wav2Vec2FeatureExtractor
    import torch
    HAS_MERT = True
except ImportError:
    HAS_MERT = False

try:
    import ollama as _ollama
    HAS_OLLAMA = True
except ImportError:
    HAS_OLLAMA = False

try:
    from openai import OpenAI as _OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt

# ─── Constants ────────────────────────────────────────────────────────────────

SAMPLE_RATE     = 44100
MERT_SR         = 24000
BUFFER_SECONDS  = 30

POLL_NORMAL     = 5.0
POLL_FAST       = 2.0
POLL_SCROBBLE   = 15.0

CHUNK           = 16384
SEG_SECONDS     = 10
SCROBBLE_PCT    = 0.90
SCROBBLE_MIN_MS = 4 * 60 * 1000
FULL_BUF_MAX    = SAMPLE_RATE * 10 * 60

SPOTIFY_SCOPE = (
    "user-read-currently-playing "
    "user-read-playback-state "
    "user-modify-playback-state "
    "playlist-read-private "
    "playlist-read-collaborative "
    "playlist-modify-public "
    "playlist-modify-private"
)
REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

KEY_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

CAMELOT = {
    (0,1):"8B",(1,1):"3B",(2,1):"10B",(3,1):"5B",(4,1):"12B",(5,1):"7B",
    (6,1):"2B",(7,1):"9B",(8,1):"4B",(9,1):"11B",(10,1):"6B",(11,1):"1B",
    (0,0):"5A",(1,0):"12A",(2,0):"7A",(3,0):"2A",(4,0):"9A",(5,0):"4A",
    (6,0):"11A",(7,0):"6A",(8,0):"1A",(9,0):"8A",(10,0):"3A",(11,0):"10A",
}

# ─── Paths ────────────────────────────────────────────────────────────────────

BASE_DIR   = pathlib.Path(__file__).parent
DB_FILE    = BASE_DIR / "tracks_db.json"
EMBEDS_DIR = BASE_DIR / "embeds"
EMBEDS_DIR.mkdir(exist_ok=True)
ANCHORS_DIR = BASE_DIR / "anchors"
ANCHORS_DIR.mkdir(exist_ok=True)

# ─── LLM configuration ────────────────────────────────────────────────────────

LLM_BACKEND  = os.getenv("LLM", "ollama").strip().lower()
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:26b").strip()
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "ollama").strip()
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-large-latest").strip()
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "").strip()

# ─── LLM helpers ──────────────────────────────────────────────────────────────

_llm_client = None
_llm_backend_used = None

def _init_llm_client(console):
    """Initialize the LLM client based on LLM env var: ollama | deepseek | mistral."""
    global _llm_client, _llm_backend_used
    if _llm_client is not None:
        return _llm_client

    backend = LLM_BACKEND

    if backend == "ollama":
        if not HAS_OLLAMA:
            console.print("[red]ollama package not installed: pip install ollama[/red]")
            sys.exit(1)
        console.print(f"[cyan]Using Ollama — model: {OLLAMA_MODEL}[/cyan]")
        _llm_backend_used = "ollama"
        _llm_client = "ollama"  # ollama module is used directly, not a client object

    elif backend == "deepseek":
        if not HAS_OPENAI:
            console.print("[red]openai package not installed: pip install openai[/red]")
            sys.exit(1)
        if not DEEPSEEK_API_KEY:
            console.print("[red]DEEPSEEK_API_KEY not set in environment[/red]")
            sys.exit(1)
        console.print(f"[cyan]Using DeepSeek API — model: {DEEPSEEK_MODEL}[/cyan]")
        _llm_backend_used = "deepseek"
        _llm_client = _OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com",
        )

    elif backend == "mistral":
        if not HAS_OPENAI:
            console.print("[red]openai package not installed: pip install openai[/red]")
            sys.exit(1)
        if not MISTRAL_API_KEY:
            console.print("[red]MISTRAL_API_KEY not set in environment[/red]")
            sys.exit(1)
        console.print(f"[cyan]Using Mistral API — model: {MISTRAL_MODEL}[/cyan]")
        _llm_backend_used = "mistral"
        _llm_client = _OpenAI(
            api_key=MISTRAL_API_KEY,
            base_url="https://api.mistral.ai/v1",
        )

    else:
        console.print(f"[red]Unknown LLM backend: {backend}. Use ollama | deepseek | mistral[/red]")
        sys.exit(1)

    return _llm_client


def _llm_chat(console, system_msg, user_msg, temperature=0.7, max_tokens=300):
    """Send a chat request to the configured LLM backend. Returns response text."""
    backend = LLM_BACKEND

    if backend == "ollama":
        resp = _ollama.chat(
            model=OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user",   "content": user_msg},
            ],
            options={"temperature": temperature, "num_predict": max_tokens},
            think=False,
        )
        raw = resp["message"]["content"].strip()
        # strip <think>...</think> if present
        text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        if not text:
            m = re.search(r"<think>(.*?)</think>", raw, flags=re.DOTALL)
            text = m.group(1).strip() if m else raw
        return text

    elif backend in ("deepseek", "mistral"):
        model = DEEPSEEK_MODEL if backend == "deepseek" else MISTRAL_MODEL
        resp = _llm_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user",   "content": user_msg},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content.strip()

    return ""


def _feat_summary(t: dict) -> str:
    """Build a human-readable summary of audio features for an LLM prompt."""
    f  = t.get("features") or {}
    ss = t.get("start_seg") or {}
    es = t.get("end_seg")   or {}
    lines = []
    if f.get("bpm"):
        lines.append(f"BPM: {f['bpm']:.1f}")
    if f.get("chroma_key") and f.get("mode"):
        cam = f.get("camelot", "")
        lines.append(f"Key: {f['chroma_key']} {f['mode']}" + (f" (Camelot {cam})" if cam else ""))
    if "rms_db" in f:
        lines.append(f"Loudness: {f['rms_db']:.1f} dBFS")
    if "dynamic_range" in f:
        lines.append(f"Dynamic range: {f['dynamic_range']:.1f} dB")
    if "harm_ratio" in f:
        lines.append(f"Harmonic ratio: {f['harm_ratio']:.2f}  (1=fully tonal, 0=percussive)")
    if "flatness" in f:
        lines.append(f"Spectral flatness: {f['flatness']:.3f}  (0=tonal, 1=noise-like)")
    if "bass" in f:
        lines.append(f"Freq balance — bass: {f['bass']*100:.1f}%,  "
                     f"mid: {f.get('mid',0)*100:.1f}%,  high: {f.get('high',0)*100:.1f}%")
    if "centroid_hz" in f:
        lines.append(f"Spectral centroid: {f['centroid_hz']:.0f} Hz")
    if "beat_reg" in f:
        lines.append(f"Beat regularity: {f['beat_reg']:.1f}  (higher = more regular)")
    if "onset_str" in f:
        lines.append(f"Onset strength: {f['onset_str']:.2f}")
    if "tempo_complexity" in f:
        lines.append(f"Tempo complexity: {f['tempo_complexity']:.3f}")
    if ss.get("bpm") and es.get("bpm"):
        lines.append(f"BPM drift: start {ss['bpm']:.1f} → end {es['bpm']:.1f}")
    if ss.get("rms_db") and es.get("rms_db"):
        lines.append(f"Energy drift: start {ss['rms_db']:.1f} dBFS → end {es['rms_db']:.1f} dBFS")
    return "\n".join(lines) if lines else "(no numerical features available)"


def _emb_stats(emb: np.ndarray) -> str:
    """Return a textual summary of MERT embedding statistics for an LLM prompt."""
    v = (emb - emb.mean()) / (emb.std() + 1e-9)
    q = np.percentile(v, [10, 25, 50, 75, 90])
    return (f"MERT embedding (768-dim, normalized): "
            f"p10={q[0]:.2f}, p25={q[1]:.2f}, median={q[2]:.2f}, "
            f"p75={q[3]:.2f}, p90={q[4]:.2f}, raw_std={emb.std():.4f}")


DESCRIPTION_SYSTEM_PROMPT = (
    "You are a music expert and audio analyst. "
    "You receive quantitative audio features extracted directly from a recording "
    "(BPM, key, spectral features, MERT neural-embedding statistics) "
    "together with the track metadata. "
    "Write a concise but vivid English description (3-5 sentences) of the track's "
    "sonic character, mood, energy, and genre hints. "
    "Be specific — mention tempo feel, harmonic colour, texture, and dynamics. "
    "Do NOT invent biographical facts about the artist. "
    "Do NOT start with the track name or artist name as the first word. "
    "Reply with the description only, no preamble."
)


def _load_descriptions(playlist_id: str) -> list | None:
    """Load previously saved track descriptions from anchors/descriptions_<pl_id>.json."""
    desc_file = ANCHORS_DIR / f"descriptions_{playlist_id}.json"
    if desc_file.exists():
        try:
            data = json.loads(desc_file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "tracks" in data:
                return data["tracks"]
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return None


def _save_descriptions(playlist_id: str, playlist_name: str, tracks: list, model: str):
    """Save track descriptions to anchors/descriptions_<pl_id>.json."""
    desc_file = ANCHORS_DIR / f"descriptions_{playlist_id}.json"
    data = {
        "playlist_id":   playlist_id,
        "playlist_name": playlist_name,
        "model":         model,
        "tracks":        tracks,
    }
    desc_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _backup_exists(playlist_id: str) -> bool:
    """Check if a backup file exists for this playlist."""
    bk_file = ANCHORS_DIR / f"backup_{playlist_id}.json"
    return bk_file.exists()


def generate_track_descriptions(console, db, tracks, pl_name, pl_id):
    """
    Steps 2.1 — Generate text descriptions for all tracks in a playlist.
    Uses MERT embeddings + track_db.json parameters + LLM.
    Saves intermediate results to ./anchors/descriptions_<pl_id>.json.
    Returns the list of track description dicts.
    """
    # 2.1. Prepare text descriptions
    descs = _load_descriptions(pl_id) or []

    # Build set of spotify IDs already described
    existing_ids = set(d.get("spotify_id") or d.get("id") for d in descs if d)

    # Find tracks missing descriptions
    playlist_ids = set(t["id"] for t in tracks)
    missing_ids = playlist_ids - existing_ids

    if missing_ids:
        console.print(
            f"\n[bold cyan]Generating descriptions for {len(missing_ids)} new track(s)...[/bold cyan]"
        )

        # Init LLM
        _init_llm_client(console)
        model_name = (
            OLLAMA_MODEL if LLM_BACKEND == "ollama"
            else DEEPSEEK_MODEL if LLM_BACKEND == "deepseek"
            else MISTRAL_MODEL
        )

        missing_list = [t for t in tracks if t["id"] in missing_ids]
        for idx, track in enumerate(missing_list, 1):
            tid = track["id"]
            console.print(
                f"  [{idx}/{len(missing_list)}] [cyan]{track['name']}[/cyan] — {track['artist']} ...",
                end=" ", highlight=False,
            )

            # Get DB entry
            db_entry = db.get(tid)
            if not db_entry:
                console.print("[red]✗ (not in DB)[/red]")
                descs.append({
                    "spotify_id": tid,
                    "name":       track["name"],
                    "artist":     track["artist"],
                    "album":      track["album"],
                    "description": "(not in DB — analysis needed)",
                    "playlist":   pl_name,
                })
                continue

            # Build feature summary
            feat_text = _feat_summary(db_entry)

            # Build MERT embedding hint
            emb_hint = ""
            ef = db_entry.get("embedding_file")
            if ef and (BASE_DIR / ef).exists():
                try:
                    emb = np.load(str(BASE_DIR / ef))
                    emb_hint = _emb_stats(emb)
                except Exception:
                    pass

            user_msg = (
                f'Track: "{track["name"]}"\n'
                f'Artist: {track["artist"]}\n'
                f'Album: {track["album"]}\n\n'
                f'Audio features:\n{feat_text}\n'
                + (f"\n{emb_hint}\n" if emb_hint else "")
                + "\nWrite a description of this track."
            )

            description = ""
            try:
                description = _llm_chat(
                    console, DESCRIPTION_SYSTEM_PROMPT, user_msg,
                    temperature=0.7, max_tokens=2000,
                )
                console.print("[green]✓[/green]")
            except Exception as e:
                description = f"[Error: {e}]"
                console.print("[red]⚠[/red]")

            descs.append({
                "spotify_id":  tid,
                "name":        track["name"],
                "artist":      track["artist"],
                "album":       track["album"],
                "description": description,
                "playlist":    pl_name,
            })

        # Save after processing new tracks
        _save_descriptions(pl_id, pl_name, descs, model_name)
        console.print(f"[green]✓ Descriptions saved to anchors/descriptions_{pl_id}.json[/green]")

    else:
        console.print(
            f"[green]✓ All {len(tracks)} tracks already have descriptions "
            f"(anchors/descriptions_{pl_id}.json)[/green]"
        )

    # Verify all playlist tracks are in the list (reconcile)
    final_ids = set(d.get("spotify_id") or d.get("id") for d in descs if d)
    still_missing = playlist_ids - final_ids
    if still_missing:
        console.print(
            f"[yellow]⚠ {len(still_missing)} track(s) still missing descriptions "
            f"(likely not in DB). Run analysis first.[/yellow]"
        )

    return descs


# ─── Global audio state ───────────────────────────────────────────────────────

actual_channels = 2
actual_sr       = SAMPLE_RATE
audio_deque: collections.deque = collections.deque(maxlen=actual_sr * BUFFER_SECONDS * 2)
audio_lock  = threading.Lock()

full_buf_lock = threading.Lock()
full_buf      = None

start_buf_lock  = threading.Lock()
start_buf       = None
start_buf_done  = False

_mert_model     = None
_mert_extractor = None
_mert_lock      = threading.Lock()

# ─── MERT ─────────────────────────────────────────────────────────────────────

def load_mert(console):
    global _mert_model, _mert_extractor
    if not HAS_MERT:
        return
    with _mert_lock:
        if _mert_model is not None:
            return
        console.print("[cyan]Loading MERT model...[/cyan]")
        _mert_extractor = Wav2Vec2FeatureExtractor.from_pretrained(
            "m-a-p/MERT-v1-95M", trust_remote_code=True)
        _mert_model = AutoModel.from_pretrained(
            "m-a-p/MERT-v1-95M", trust_remote_code=True)
        _mert_model.eval()
    console.print("[green]✓ MERT loaded[/green]")


def _mert_embedding(y_mono, sr=SAMPLE_RATE):
    if not HAS_MERT or _mert_model is None:
        return None
    try:
        y_trim = _trim_silence(y_mono, sr=sr, top_db=40)
        y24    = librosa.resample(y_trim, orig_sr=sr, target_sr=MERT_SR)
        y24    = y24[:MERT_SR * 30]
        inputs = _mert_extractor(y24, sampling_rate=MERT_SR, return_tensors="pt", padding=True)
        inputs.pop("use_return_dict", None)
        with torch.no_grad():
            out    = _mert_model(**inputs, output_hidden_states=True, return_dict=True)
            hidden = torch.stack(out.hidden_states[-4:]).mean(dim=0)
            emb    = hidden.mean(dim=1).squeeze().numpy()
        return emb.tolist()
    except Exception:
        return None

# ─── Database ─────────────────────────────────────────────────────────────────

def load_db():
    if DB_FILE.exists():
        try:
            return json.loads(DB_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_db(db):
    DB_FILE.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")


DURATION_TOLERANCE = 0.01   # 1% — re-listen if stored duration differs by more than this

def track_needs_analysis(track_id, db, real_duration_ms=None):
    """
    Return a reason string if the track needs (re)analysis, or None if it is fine.
    Reasons: "Missing in db" | "Missing file" | "Wrong time"
    """
    if track_id not in db:
        return "Missing in db"
    entry    = db[track_id]
    emb_file = entry.get("embedding_file")
    if not emb_file:
        return "Missing file"
    if not (BASE_DIR / emb_file).exists():
        return "Missing file"
    if real_duration_ms is not None:
        stored_ms = entry.get("duration_ms", 0)
        if stored_ms > 0:
            diff = abs(stored_ms - real_duration_ms) / real_duration_ms
            if diff > DURATION_TOLERANCE:
                return "Wrong time"
    return None

# ─── Audio features ───────────────────────────────────────────────────────────

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
    out["rms_db"]   = float(20 * np.log10(max(rms, 1e-9)))
    out["rms_norm"] = float(np.clip((out["rms_db"] + 60) / 60, 0, 1))

    try:
        tempo, beats    = librosa.beat.beat_track(y=y, sr=sr)
        out["bpm"]      = float(np.atleast_1d(tempo)[0])
        if len(beats) > 2:
            intervals       = np.diff(librosa.frames_to_time(beats, sr=sr))
            out["beat_reg"] = float(1.0 / (np.std(intervals) + 1e-6))
        else:
            out["beat_reg"] = 0.0
    except Exception:
        out["bpm"] = 0.0
        out["beat_reg"] = 0.0

    out["centroid_hz"]  = float(librosa.feature.spectral_centroid(y=y, sr=sr)[0].mean())
    out["rolloff_hz"]   = float(librosa.feature.spectral_rolloff(y=y, sr=sr, roll_percent=0.95)[0].mean())
    out["bandwidth_hz"] = float(librosa.feature.spectral_bandwidth(y=y, sr=sr)[0].mean())
    out["zcr"]          = float(librosa.feature.zero_crossing_rate(y)[0].mean())
    out["onset_str"]    = float(librosa.onset.onset_strength(y=y, sr=sr).mean())

    S     = np.abs(librosa.stft(y))
    freqs = librosa.fft_frequencies(sr=sr)
    e_tot = S.mean(axis=1).sum() + 1e-9
    out["bass"] = float(S[freqs < 200].mean(axis=1).sum()                     / e_tot)
    out["mid"]  = float(S[(freqs >= 200) & (freqs < 2000)].mean(axis=1).sum() / e_tot)
    out["high"] = float(S[freqs >= 2000].mean(axis=1).sum()                   / e_tot)

    chroma         = librosa.feature.chroma_stft(y=y, sr=sr).mean(axis=1)
    key_idx        = int(np.argmax(chroma))
    out["chroma_key"]  = KEY_NAMES[key_idx]
    out["chroma_idx"]  = key_idx
    out["chroma_vals"] = chroma.tolist()

    try:
        tonal          = librosa.feature.tonnetz(y=librosa.effects.harmonic(y), sr=sr).mean(axis=1)
        out["mode"]    = "Major" if float(tonal[0]) > 0 else "minor"
        out["camelot"] = CAMELOT.get((key_idx, 1 if out["mode"] == "Major" else 0), "?")
    except Exception:
        out["mode"] = "?"
        out["camelot"] = "?"

    out["mfcc13"] = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13).mean(axis=1).tolist()
    return out


def _extract_features_full(y, sr=SAMPLE_RATE):
    if y is None or len(y) < sr * 2:
        return {}
    out = _extract_features(y, sr)
    try:
        out["flatness"] = float(librosa.feature.spectral_flatness(y=y)[0].mean())

        y_harm, y_perc    = librosa.effects.hpss(y)
        harm_e            = float(np.mean(y_harm ** 2)) + 1e-9
        perc_e            = float(np.mean(y_perc ** 2)) + 1e-9
        out["harm_ratio"] = float(harm_e / (harm_e + perc_e))

        rms_frames           = librosa.feature.rms(y=y)[0]
        rms_db_f             = 20 * np.log10(np.maximum(rms_frames, 1e-9))
        out["dynamic_range"] = float(np.percentile(rms_db_f, 95) - np.percentile(rms_db_f, 5))

        out["chroma_cens"]      = librosa.feature.chroma_cens(y=y, sr=sr).mean(axis=1).tolist()
        tgram                   = librosa.feature.tempogram(y=y, sr=sr)
        out["tempo_complexity"] = float(np.std(tgram.mean(axis=1)))
        out["mfcc20"]           = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=20).mean(axis=1).tolist()
    except Exception:
        pass
    return out

# ─── Save track worker ────────────────────────────────────────────────────────

def save_track_worker(track_info, playlist_name, playlist_uri, y_full,
                      y_start_snap=None, status_cb=None):
    tid = track_info["id"]
    if status_cb:
        status_cb(f"Extracting features: {track_info['name'][:30]}...")

    y_main      = _trim_silence(y_full, sr=actual_sr, top_db=40)
    full_feats  = _extract_features_full(y_main, sr=actual_sr)
    y_start     = (y_start_snap if (y_start_snap is not None and len(y_start_snap) >= actual_sr)
                   else y_full[:actual_sr * SEG_SECONDS])
    y_end       = y_full[-actual_sr * SEG_SECONDS:]
    start_feats = _extract_features(y_start, sr=actual_sr)
    end_feats   = _extract_features(y_end,   sr=actual_sr)

    if status_cb:
        status_cb(f"Computing MERT embedding: {track_info['name'][:30]}...")
    emb      = _mert_embedding(y_main, sr=actual_sr)
    emb_file = None
    if emb is not None:
        emb_path = EMBEDS_DIR / f"{tid}.npy"
        np.save(str(emb_path), np.array(emb, dtype=np.float32))
        emb_file = str(emb_path.relative_to(BASE_DIR))

    with _db_lock:
        db = load_db()
        db[tid] = {
            "spotify_id"    : tid,
            "name"          : track_info["name"],
            "artist"        : track_info["artist"],
            "album"         : track_info["album"],
            "duration_ms"   : track_info["duration_ms"],
            "playlist_name" : playlist_name,
            "playlist_uri"  : playlist_uri,
            "saved_at"      : datetime.datetime.now().isoformat(),
            "features"      : full_feats,
            "start_seg"     : start_feats,
            "end_seg"       : end_feats,
            "embedding_file": emb_file,
            "embedding_dim" : 768 if emb else None,
        }
        save_db(db)

    if status_cb:
        emb_str = "+ MERT" if emb else "(no MERT)"
        status_cb(f"✓ Saved: {track_info['name'][:30]} {emb_str}")

# ─── Audio capture ────────────────────────────────────────────────────────────

def _make_callback(channels):
    def callback(in_data, frame_count, time_info, status):
        global start_buf, start_buf_done, full_buf
        samples = np.frombuffer(in_data, dtype=np.int16).astype(np.float32) / 32768.0
        with audio_lock:
            audio_deque.extend(samples)
        with start_buf_lock:
            if not start_buf_done:
                chunk_mono = samples[::channels] if channels == 2 else samples
                start_buf  = chunk_mono if start_buf is None else np.concatenate([start_buf, chunk_mono])
                if len(start_buf) >= actual_sr * SEG_SECONDS:
                    start_buf      = start_buf[:actual_sr * SEG_SECONDS]
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


def list_loopback_devices():
    """Return list of all input/loopback devices."""
    if _pa_mod is None:
        return []
    pa      = _pa_mod.PyAudio()
    devices = []
    for i in range(pa.get_device_count()):
        dev   = pa.get_device_info_by_index(i)
        ch_in = dev.get("maxInputChannels", 0)
        if ch_in > 0:
            devices.append({
                "index"   : i,
                "name"    : dev["name"],
                "channels": ch_in,
                "sr"      : int(dev.get("defaultSampleRate", SAMPLE_RATE)),
                "loopback": dev.get("isLoopbackDevice", False),
            })
    pa.terminate()
    return devices


def start_audio_capture(device_index=None):
    global actual_channels, actual_sr, audio_deque, FULL_BUF_MAX
    global full_buf, start_buf, start_buf_done
    full_buf       = None
    start_buf      = None
    start_buf_done = False

    if _pa_mod is None:
        return None, None, "unavailable"

    pa  = _pa_mod.PyAudio()
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
        ch              = min(int(dev.get("maxInputChannels", 2)), 2)
        sr              = int(dev.get("defaultSampleRate", SAMPLE_RATE))
        actual_sr       = sr
        FULL_BUF_MAX    = actual_sr * 10 * 60
        actual_channels = ch
        audio_deque     = collections.deque(maxlen=actual_sr * BUFFER_SECONDS * actual_channels)
        stream = pa.open(
            format=_pa_mod.paInt16, channels=ch, rate=sr,
            input=True, input_device_index=dev["index"],
            frames_per_buffer=CHUNK,
            stream_callback=_make_callback(actual_channels),
        )
        device_name = dev["name"][:60]
    else:
        actual_channels = 1
        audio_deque     = collections.deque(maxlen=SAMPLE_RATE * BUFFER_SECONDS)
        stream = pa.open(
            format=_pa_mod.paInt16, channels=1, rate=SAMPLE_RATE,
            input=True, frames_per_buffer=CHUNK,
            stream_callback=_make_callback(actual_channels),
        )
        device_name = "Default input (no loopback)"

    stream.start_stream()
    return pa, stream, device_name

# ─── Spotify helpers ──────────────────────────────────────────────────────────

def init_spotify(console):
    console.print("[cyan]Connecting to Spotify API...[/cyan]")
    sp   = spotipy.Spotify(auth_manager=SpotifyOAuth(
        scope=SPOTIFY_SCOPE,
        redirect_uri=REDIRECT_URI,
        open_browser=True,
    ))
    user = sp.current_user()
    console.print(f"[green]✓ Authenticated as: {user['display_name']} ({user['id']})[/green]")
    return sp, user["id"]


def get_own_playlists(sp, user_id):
    """Fetch only playlists owned by the current user (paginates fully)."""
    playlists = []
    limit     = 50
    offset    = 0
    while True:
        result = sp.current_user_playlists(limit=limit, offset=offset)
        items  = result.get("items") or []
        for pl in items:
            if pl and pl.get("owner", {}).get("id") == user_id:
                playlists.append(pl)
        if not result.get("next"):
            break
        offset += limit
        time.sleep(0.2)
    return playlists


def get_playlist_tracks(sp, playlist_id):
    """
    Fetch all tracks from a playlist using the raw requests approach to
    avoid spotipy parameter serialization bugs with additional_types.
    Skips local files, episodes, and null items.
    """
    tracks = []
    limit  = 100
    offset = 0

    while True:
        # Call without fields= and without additional_types list —
        # just plain positional args to avoid any spotipy serialization issue.
        try:
            result = sp.playlist_items(playlist_id, limit=limit, offset=offset)
        except Exception as exc:
            raise RuntimeError(f"API error fetching tracks: {exc}") from exc

        items = result.get("items") or []

        for item in items:
            if not item:
                continue
            # API returns the track object under "item" key (not "track")
            t = item.get("item")
            if not t:
                continue
            # Skip local files (no Spotify ID) and episodes
            if item.get("is_local"):
                continue
            if t.get("type") != "track":
                continue
            tid = t.get("id")
            if not tid:
                continue
            tracks.append({
                "id"         : tid,
                "name"       : t.get("name", "Unknown"),
                "artist"     : ", ".join(a["name"] for a in (t.get("artists") or [])),
                "album"      : (t.get("album") or {}).get("name", "Unknown"),
                "duration_ms": t.get("duration_ms", 0),
            })

        if not result.get("next"):
            break
        offset += limit
        time.sleep(0.3)   # gentle rate limiting

    return tracks


def play_track_on_device(sp, track_uri, device_id=None):
    """Start playback of a specific track URI on the given Spotify device."""
    try:
        if device_id:
            sp.start_playback(device_id=device_id, uris=[track_uri])
        else:
            sp.start_playback(uris=[track_uri])
    except Exception as exc:
        raise RuntimeError(f"Playback failed: {exc}") from exc

# ─── UI helpers ───────────────────────────────────────────────────────────────

def show_playlists_table(console, playlists):
    """Display playlist table, return chosen 0-based index, or -1 to exit."""
    table = Table(title="Your Playlists (owned by you)", show_header=True, header_style="bold cyan")
    table.add_column("#",      style="dim",        width=4)
    table.add_column("Name",   style="bold white", min_width=38)
    table.add_column("Tracks", style="cyan",       width=8)

    for i, pl in enumerate(playlists, 1):
        total = (pl.get("tracks") or {}).get("total", "?")
        table.add_row(str(i), pl["name"], str(total))

    console.print(table)
    console.print("  [dim]0 = Exit[/dim]")
    while True:
        choice = IntPrompt.ask("[bold yellow]Select playlist # (0=exit)[/bold yellow]", default=1)
        if choice == 0:
            return -1
        if 1 <= choice <= len(playlists):
            return choice - 1
        console.print(f"[red]Please enter a number between 0 and {len(playlists)}[/red]")


def show_tracks_table(console, tracks, db, playlist_name):
    """Display tracks with DB/missing status (with reason). Returns missing count."""
    table = Table(
        title=f"Playlist: {playlist_name}",
        show_header=True, header_style="bold cyan",
    )
    table.add_column("#",      style="dim",  width=4)
    table.add_column("Track",  min_width=38)
    table.add_column("Artist", min_width=24)
    table.add_column("Dur",    width=7)
    table.add_column("Status", width=18)

    missing = 0
    for i, t in enumerate(tracks, 1):
        dur_ms  = t.get("duration_ms", 0)
        dur_str = f"{dur_ms//60000}:{(dur_ms//1000)%60:02d}" if dur_ms else "?"
        reason  = track_needs_analysis(t["id"], db, real_duration_ms=dur_ms or None)
        if reason:
            missing += 1
            status = f"[red]⚠ {reason}[/red]"
        else:
            status = "[green]✓ In DB[/green]"
        table.add_row(str(i), t["name"][:42], t["artist"][:26], dur_str, status)

    console.print(table)
    console.print(
        f"[dim]Total: {len(tracks)} | "
        f"[red]{missing} need analysis[/red] | "
        f"[green]{len(tracks) - missing} in DB[/green][/dim]"
    )
    return missing


def show_playlist_menu(console, tracks, db, playlist_id=None):
    """Show action menu and return chosen action string."""
    missing   = sum(1 for t in tracks if track_needs_analysis(t["id"], db, real_duration_ms=t.get("duration_ms")) is not None)
    all_done  = missing == 0 and len(tracks) > 0

    console.print()
    options = []
    if missing > 0:
        options.append(("A", "[bold yellow][A][/bold yellow] Analyze missing tracks", "analyze"))
    if all_done:
        options.append(("C", "[bold green][C][/bold green] Choose anchors and reorder", "reorder"))
    # Check backup existence — offer recovery regardless of DB status
    if playlist_id and _backup_exists(playlist_id):
        options.append(("R", "[bold magenta][R][/bold magenta] Recover from backup", "recover"))
    options.append(("B", "[bold blue][B][/bold blue] Back to playlists", "back"))
    options.append(("E", "[bold red][E][/bold red] Exit", "exit"))

    for _, label, _ in options:
        console.print(f"  {label}")
    console.print()

    key_map = {k: action for k, _, action in options}
    valid   = "/".join(k for k, _, _ in options)

    while True:
        raw = Prompt.ask(f"[bold]Action[/bold] [{valid}]").strip().upper()
        if raw and raw[0] in key_map:
            return key_map[raw[0]]
        console.print(f"[red]Invalid choice. Options: {valid}[/red]")

# ─── Analysis session ─────────────────────────────────────────────────────────

class AnalysisSession:
    """Manages sequential playback + analysis of missing tracks."""

    def __init__(self, sp, tracks, playlist_name, playlist_uri, spotify_device_id, console):
        self.sp                = sp
        self.tracks            = tracks
        self.playlist_name     = playlist_name
        self.playlist_uri      = playlist_uri
        self.spotify_device_id = spotify_device_id
        self.console           = console
        self._stop             = threading.Event()

    def _retry_after(self, exc):
        try:
            h = getattr(exc, "headers", None) or {}
            return int(h.get("Retry-After", 30)) + 1
        except Exception:
            return 31

    def _wait_start(self, track_id, timeout=30.0):
        """
        Poll API until this track is confirmed playing.
        Returns (True, progress_ms) so we know exact start offset,
        or (False, 0) on timeout.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                cp = self.sp.current_playback()
                if cp and (cp.get("item") or {}).get("id") == track_id and cp.get("is_playing"):
                    return True, cp.get("progress_ms", 0)
            except spotipy.exceptions.SpotifyException as exc:
                if exc.http_status == 429:
                    time.sleep(self._retry_after(exc))
                    continue
            time.sleep(POLL_FAST)
        return False, 0

    def _wait_for_end(self, duration_ms, start_wall, start_offset_ms):
        """
        Wait until the track finishes using wall-clock time only — no API calls.
        Displays a live timer using time.time() - start_wall + start_offset_ms.
        Returns when the track should have ended.
        Respects self._stop for early exit.
        """
        dm, ds = divmod(duration_ms // 1000, 60)
        while not self._stop.is_set():
            elapsed_ms  = int((time.time() - start_wall) * 1000) + start_offset_ms
            elapsed_ms  = min(elapsed_ms, duration_ms)
            remaining_s = max(0, (duration_ms - elapsed_ms) // 1000)
            em, es      = divmod(elapsed_ms // 1000, 60)
            pct         = elapsed_ms / max(duration_ms, 1) * 100
            self.console.print(
                f"    ▶ {em}:{es:02d} / {dm}:{ds:02d}  ({pct:.0f}%)  "
                f"[dim]~{remaining_s}s left[/dim]   ",
                end="\r",
            )
            if elapsed_ms >= duration_ms:
                self.console.print()  # clear \r line
                return True
            # Sleep in short chunks so Ctrl+C is responsive
            time.sleep(min(1.0, remaining_s + 0.2))
        self.console.print()
        return False

    def run(self):
        global full_buf, start_buf, start_buf_done
        console = self.console
        total   = len(self.tracks)
        console.print(f"\n[bold cyan]Analyzing {total} missing track(s)[/bold cyan]")
        console.print("[dim]Ctrl+C to stop early[/dim]\n")

        for idx, track in enumerate(self.tracks, 1):
            if self._stop.is_set():
                break

            console.print(
                f"[bold white][{idx}/{total}][/bold white] "
                f"[cyan]{track['name']}[/cyan]  [dim]— {track['artist']}[/dim]"
            )

            # Reset audio buffers
            with full_buf_lock:
                full_buf = None
            with start_buf_lock:
                start_buf      = None
                start_buf_done = False

            # Start playback
            try:
                play_track_on_device(self.sp, f"spotify:track:{track['id']}", self.spotify_device_id)
            except RuntimeError as exc:
                console.print(f"  [red]{exc} — skipping[/red]")
                time.sleep(2.0)
                continue

            started, offset_ms = self._wait_start(track["id"])
            if not started:
                console.print("  [yellow]⚠ Did not start within 30s — skipping[/yellow]")
                continue

            start_wall = time.time() - offset_ms / 1000.0
            dur_ms     = track["duration_ms"]
            dm, ds     = divmod(dur_ms // 1000, 60)
            console.print(f"  [green]▶ Playing... ({dm}:{ds:02d})[/green]")

            if not self._wait_for_end(dur_ms, start_wall, offset_ms):
                # stopped by Ctrl+C
                break

            # Snapshot audio
            with full_buf_lock:
                y_snap = np.array(full_buf) if full_buf is not None else None
            with start_buf_lock:
                y_start_snap = np.array(start_buf) if start_buf is not None else None
            with audio_lock:
                y_live = _to_mono(np.array(audio_deque, dtype=np.float32), actual_channels)

            y_save = y_snap if (y_snap is not None and len(y_snap) > actual_sr * 5) else y_live
            if y_save is None or len(y_save) < actual_sr * 5:
                console.print("  [yellow]⚠ Not enough audio — skipping[/yellow]")
                continue

            save_track_worker(
                track_info    = track,
                playlist_name = self.playlist_name,
                playlist_uri  = self.playlist_uri,
                y_full        = y_save,
                y_start_snap  = y_start_snap,
                status_cb     = lambda msg: console.print(f"  [dim]{msg}[/dim]"),
            )
            console.print("  [bold green]✓ Done[/bold green]")

            if idx < total and not self._stop.is_set():
                time.sleep(1.5)

        console.print("\n[bold green]Analysis session complete![/bold green]")

    def stop(self):
        self._stop.set()

# ─── Device pickers ───────────────────────────────────────────────────────────

def pick_audio_device(console):
    devices = list_loopback_devices()
    if not devices:
        console.print("[yellow]⚠ No audio input devices found[/yellow]")
        return None

    table = Table(title="Audio Input / Loopback Devices", show_header=True, header_style="bold cyan")
    table.add_column("#",    style="dim", width=4)
    table.add_column("Type", width=8)
    table.add_column("Name", min_width=44)
    table.add_column("Ch",   width=4)
    table.add_column("SR",   width=8)

    for i, dev in enumerate(devices, 1):
        dtype = "[magenta]LOOP[/magenta]" if dev["loopback"] else "[dim]IN  [/dim]"
        table.add_row(str(i), dtype, dev["name"], str(dev["channels"]), str(dev["sr"]))

    console.print(table)
    while True:
        choice = IntPrompt.ask("[bold yellow]Select audio device #[/bold yellow]", default=1)
        if 1 <= choice <= len(devices):
            return devices[choice - 1]
        console.print(f"[red]Please enter a number between 1 and {len(devices)}[/red]")


def pick_spotify_playback_device(console, sp):
    try:
        devices = (sp.devices() or {}).get("devices") or []
    except Exception as exc:
        console.print(f"[yellow]Could not list Spotify devices: {exc}[/yellow]")
        return None

    if not devices:
        console.print("[yellow]No active Spotify devices. Open Spotify on any device first.[/yellow]")
        return None

    table = Table(title="Spotify Playback Devices", show_header=True, header_style="bold cyan")
    table.add_column("#",      style="dim", width=4)
    table.add_column("Name",   min_width=32)
    table.add_column("Type",   width=14)
    table.add_column("Active", width=8)
    table.add_column("Vol",    width=6)

    for i, dev in enumerate(devices, 1):
        active = "[green]✓[/green]" if dev.get("is_active") else ""
        table.add_row(str(i), dev["name"], dev["type"], active,
                      str(dev.get("volume_percent", "?")) + "%")

    console.print(table)
    console.print("  [dim]0 = use currently active Spotify device[/dim]")

    while True:
        choice = IntPrompt.ask("[bold yellow]Select Spotify device # (0 = active)[/bold yellow]", default=0)
        if choice == 0:
            return None
        if 1 <= choice <= len(devices):
            return devices[choice - 1]["id"]
        console.print(f"[red]Please enter 0–{len(devices)}[/red]")

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    console = Console()
    console.print(Panel("[bold cyan]Spotify Playlist Analyzer[/bold cyan]", border_style="cyan"))

    # Dependency check
    missing_deps = []
    if not HAS_SPOTIPY:  missing_deps.append("spotipy")
    if not HAS_LIBROSA:  missing_deps.append("librosa")
    if _pa_mod is None:  missing_deps.append("pyaudiowpatch")
    if missing_deps:
        console.print(f"[red]Missing packages:[/red] pip install {' '.join(missing_deps)}")
        sys.exit(1)

    if not HAS_MERT:
        console.print("[yellow]⚠ MERT unavailable — embeddings will not be saved.[/yellow]")
        console.print("[dim]  pip install torch transformers accelerate[/dim]")

    if HAS_MERT:
        load_mert(console)

    # Spotify auth
    try:
        sp, user_id = init_spotify(console)
    except Exception as exc:
        console.print(f"[red]Spotify auth failed: {exc}[/red]")
        sys.exit(1)

    # Audio capture device
    console.print("\n[bold]Step 1: Select audio capture device[/bold]")
    audio_dev   = pick_audio_device(console)
    audio_idx   = audio_dev["index"] if audio_dev else None

    console.print("\n[cyan]Opening audio stream...[/cyan]")
    pa, stream, dev_name = start_audio_capture(audio_idx)
    if stream is None:
        console.print("[yellow]⚠ Audio capture unavailable[/yellow]")
    else:
        console.print(f"[green]✓ Audio: {dev_name}[/green]")

    # Spotify playback device
    console.print("\n[bold]Step 2: Select Spotify playback device[/bold]")
    spotify_dev_id = pick_spotify_playback_device(console, sp)
    if spotify_dev_id:
        console.print("[green]✓ Playback device selected[/green]")
    else:
        console.print("[dim]Using currently active Spotify device[/dim]")

    # Main loop
    try:
        while True:
            console.print("\n[cyan]Fetching your playlists...[/cyan]")
            playlists = get_own_playlists(sp, user_id)
            if not playlists:
                console.print("[red]No playlists found.[/red]")
                break

            pl_idx  = show_playlists_table(console, playlists)
            if pl_idx == -1:
                console.print("[yellow]Exiting...[/yellow]")
                break

            chosen  = playlists[pl_idx]
            pl_id   = chosen["id"]
            pl_name = chosen["name"]
            pl_uri  = chosen["uri"]

            while True:
                console.print(f"\n[cyan]Fetching tracks for:[/cyan] [bold]{pl_name}[/bold]")
                try:
                    tracks = get_playlist_tracks(sp, pl_id)
                except RuntimeError as exc:
                    console.print(f"[red]{exc}[/red]")
                    break

                if not tracks:
                    console.print("[yellow]Playlist is empty or contains no playable tracks.[/yellow]")
                    break

                db = load_db()
                show_tracks_table(console, tracks, db, pl_name)
                action = show_playlist_menu(console, tracks, db, playlist_id=pl_id)

                if action in ("back", "exit"):
                    break

                elif action == "recover":
                    # ── Recover from backup → move to Playlist editor (stub) ──
                    bk_file = ANCHORS_DIR / f"backup_{pl_id}.json"
                    console.print(
                        f"\n[bold magenta]Recovering from backup:[/bold magenta] "
                        f"[dim]{bk_file.name}[/dim]"
                    )
                    console.print(
                        "[yellow]Playlist editor not yet implemented. "
                        "Backup file is ready at:[/yellow] "
                        f"[dim]{bk_file}[/dim]"
                    )
                    console.print(
                        "[dim](Backup loaded — returning to playlist list.)[/dim]"
                    )
                    break

                elif action == "analyze":
                    db         = load_db()
                    to_analyze = [t for t in tracks if track_needs_analysis(t["id"], db, real_duration_ms=t.get("duration_ms")) is not None]
                    if not to_analyze:
                        console.print("[green]All tracks are already in the DB![/green]")
                        continue

                    session = AnalysisSession(
                        sp=sp, tracks=to_analyze,
                        playlist_name=pl_name, playlist_uri=pl_uri,
                        spotify_device_id=spotify_dev_id, console=console,
                    )
                    try:
                        session.run()
                    except KeyboardInterrupt:
                        session.stop()
                        console.print("\n[yellow]Analysis interrupted.[/yellow]")

                    db      = load_db()
                    still   = sum(1 for t in tracks if track_needs_analysis(t["id"], db, real_duration_ms=t.get("duration_ms")) is not None)
                    if still == 0:
                        console.print(f"[bold green]All {len(tracks)} tracks are now in the DB![/bold green]")
                    else:
                        console.print(f"[yellow]{still} track(s) still missing.[/yellow]")

                elif action == "reorder":
                    console.print(
                        f"\n[bold cyan]═══ Choose anchors and reorder ═══[/bold cyan]"
                    )

                    # ── Check if descriptions file already exists ────────────
                    desc_file = ANCHORS_DIR / f"descriptions_{pl_id}.json"
                    regen = True   # default: generate fresh
                    if desc_file.exists():
                        console.print(
                            f"\n[yellow]Existing descriptions found:[/yellow] "
                            f"[dim]{desc_file.name}[/dim]"
                        )
                        while True:
                            raw = Prompt.ask(
                                "[bold]Generate [N]ew descriptions or [U]se existing?[/bold] [N/U]",
                                default="N",
                            ).strip().upper()
                            if raw and raw[0] in ("N", "U"):
                                regen = (raw[0] == "N")
                                break
                            console.print("[red]Please enter N or U[/red]")
                        if regen:
                            desc_file.unlink(missing_ok=True)
                            console.print("[yellow]Old descriptions deleted — regenerating...[/yellow]")

                    # ── Step 2.1: Generate track descriptions ────────────────
                    descs = generate_track_descriptions(
                        console, db, tracks, pl_name, pl_id,
                    )

                    # ── Step 2.2: Launch playlist editor (stub) ──────────────
                    console.print(
                        f"\n[bold green]✓ {len(descs)} track descriptions ready[/bold green]"
                    )
                    console.print(
                        "[yellow]Playlist editor (editing descriptions + sorting) "
                        "not yet implemented.[/yellow]"
                    )
                    console.print(
                        f"[dim]Descriptions saved to anchors/descriptions_{pl_id}.json[/dim]"
                    )
                    # For now, return to playlist list after generating descriptions
                    break

    except KeyboardInterrupt:
        console.print("\n[yellow]Exiting...[/yellow]")
    finally:
        if stream:
            stream.stop_stream()
            stream.close()
        if pa:
            pa.terminate()
        console.print("[dim]Audio capture stopped. Bye![/dim]")


if __name__ == "__main__":
    main()
