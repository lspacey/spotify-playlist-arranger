#!/usr/bin/env python3
"""
Spotify Playlist Analyzer
Browses own playlists, shows missing tracks, analyzes them via audio capture + MERT.
"""

import os, sys, time, threading, collections, json, pathlib, datetime, re, math, random, copy
import numpy as np

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["HF_HUB_CACHE"] = str(pathlib.Path.home() / ".cache" / "huggingface" / "hub")

# ─── Load .env file ───────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(pathlib.Path(__file__).parent / ".env")
except ImportError:
    pass

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

def _spotify_request_with_retries(sp, method, path, payload=None, max_retries=5):
    """Spotify API call with retries for 429/5xx errors. Refreshes token on each retry."""
    import requests as _req
    url = f"https://api.spotify.com/v1/{path.lstrip('/')}"
    backoff = 1.0
    for attempt in range(max_retries):
        try:
            # Refresh token on every attempt (may have expired)
            token = sp.auth_manager.get_access_token(as_dict=False)
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

            if method.upper() == "POST":
                resp = _req.post(url, headers=headers, json=payload, timeout=30)
            elif method.upper() == "PUT":
                resp = _req.put(url, headers=headers, json=payload, timeout=30)
            elif method.upper() == "GET":
                resp = _req.get(url, headers=headers, timeout=30)
            else:
                raise ValueError(f"Unsupported method: {method}")

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 5)) + 1
                time.sleep(wait)
                continue
            if resp.status_code in (500, 502, 503, 504):
                time.sleep(backoff)
                backoff = min(backoff * 2, 20)
                continue
            if not resp.ok:
                return None, f"{resp.status_code} {resp.text[:200]}"
            return resp.json() if resp.text else {}, None
        except Exception:
            time.sleep(backoff)
            backoff = min(backoff * 2, 20)
    return None, "max retries exceeded"


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
        # Verify model exists locally
        try:
            _ollama.show(OLLAMA_MODEL)
        except Exception:
            console.print(
                f"[red]✗ Model '{OLLAMA_MODEL}' not found locally. "
                f"Please run: ollama pull {OLLAMA_MODEL}[/red]"
            )
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

    if _llm_client is None:
        _init_llm_client(console)

    try:
        if backend == "ollama":
            kwargs = {
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user",   "content": user_msg},
                ],
                "options": {"temperature": temperature, "num_predict": max_tokens},
            }
            try:
                kwargs["think"] = False
                resp = _ollama.chat(**kwargs)
            except TypeError:
                # `think` parameter not supported by some models/versions
                del kwargs["think"]
                resp = _ollama.chat(**kwargs)
            except Exception as e:
                raise RuntimeError(f"Ollama API error: {e}") from e
            raw = resp.get("message", {}).get("content", "")
            if not raw:
                return ""
            raw = raw.strip()
            # strip <think>...</think> if present
            text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            if not text:
                m = re.search(r"<think>(.*?)</think>", raw, flags=re.DOTALL)
                text = m.group(1).strip() if m else raw
            return text

        elif backend in ("deepseek", "mistral"):
            model = DEEPSEEK_MODEL if backend == "deepseek" else MISTRAL_MODEL
            try:
                resp = _llm_client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_msg},
                        {"role": "user",   "content": user_msg},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception as e:
                raise RuntimeError(f"{backend.title()} API error: {e}") from e
            return resp.choices[0].message.content.strip()
    except Exception as e:
        raise RuntimeError(f"LLM error ({backend}): {e}") from e

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


def _atomic_write_json(file_path: pathlib.Path, data: object):
    """Write JSON atomically: temp file first, then rename."""
    tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(file_path)


def _save_descriptions(playlist_id: str, playlist_name: str, tracks: list, model: str):
    """Save track descriptions to anchors/descriptions_<pl_id>.json."""
    desc_file = ANCHORS_DIR / f"descriptions_{playlist_id}.json"
    data = {
        "playlist_id":   playlist_id,
        "playlist_name": playlist_name,
        "model":         model,
        "tracks":        tracks,
    }
    _atomic_write_json(desc_file, data)


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

    # ── Enrich each description entry with metrics from tracks_db.json ──────
    enriched_any = False
    for d in descs:
        tid = d.get("spotify_id")
        if tid and tid in db:
            entry = db[tid]
            f = entry.get("features") or {}
            d["bpm"]          = round(f.get("bpm", 0), 1)
            d["key"]          = f"{f.get('chroma_key', '')} {f.get('mode', '')}".strip()
            d["camelot"]      = f.get("camelot", "")
            d["loudness_db"]  = round(f.get("rms_db", 0), 1)
            d["dynamic_range"]= round(f.get("dynamic_range", 0), 1)
            d["harm_ratio"]   = round(f.get("harm_ratio", 0), 2)
            d["flatness"]     = round(f.get("flatness", 0), 3)
            d["bass_pct"]     = round(f.get("bass", 0) * 100, 1)
            d["mid_pct"]      = round(f.get("mid", 0) * 100, 1)
            d["high_pct"]     = round(f.get("high", 0) * 100, 1)
            d["onset_str"]    = round(f.get("onset_str", 0), 2)
            d["duration_ms"]  = entry.get("duration_ms", 0)
            enriched_any = True

    # Save after enrichment if any data changed (reconciles with DB updates)
    if enriched_any and descs:
        model_name = (
            OLLAMA_MODEL if LLM_BACKEND == "ollama"
            else DEEPSEEK_MODEL if LLM_BACKEND == "deepseek"
            else MISTRAL_MODEL
        )
        _save_descriptions(pl_id, pl_name, descs, model_name)
        console.print(f"[dim]✓ Descriptions updated with fresh metrics[/dim]")

    return descs


# ─── Playlist structure types ─────────────────────────────────────────────────

PLAYLIST_STRUCTURES = [
    {"id": "flat",         "name": "Flat",                "desc": "Uniform energy throughout — steady, hypnotic, no dramatic shifts.","anchor_pct": 12},
    {"id": "rise_fall",    "name": "Rise and Fall",        "desc": "Gradual build-up to a single peak, then a slow descent.","anchor_pct": 20},
    {"id": "wave",         "name": "Wave",                 "desc": "Multiple crests and troughs — tension builds, releases, then builds again.","anchor_pct": 25},
    {"id": "pulse",        "name": "Pulse / Peaks",        "desc": "Alternating high-energy and low-energy blocks, like a heartbeat.","anchor_pct": 18},
    {"id": "slow_burn",    "name": "Slow Burn / Crescendo","desc": "Starts minimal and sparse, steadily accumulates density and intensity.","anchor_pct": 20},
    {"id": "rollercoaster","name": "Rollercoaster",        "desc": "Frequent dynamic swings — intense peaks followed by deep valleys.","anchor_pct": 22},
    {"id": "alternating",  "name": "Alternation / ABAB",   "desc": "Two contrasting moods or textures trading places back and forth.","anchor_pct": 16},
    {"id": "descending",   "name": "Descending / Cooling",  "desc": "Starts heavy and intense, gradually unwinds into calm and space.","anchor_pct": 20},
    {"id": "ascension",    "name": "Ascension",             "desc": "Steady climb from darkness to light, low energy to high energy.","anchor_pct": 20},
    {"id": "story",        "name": "Story Arc",             "desc": "Introduction → development → climax → resolution — like a narrative.","anchor_pct": 25},
]


# ─── Smart sorting (Simulated Annealing ATSP with anchors) ───────────────────

CAMELOT_TO_IDX = {
    "1A":0,  "2A":1,  "3A":2,  "4A":3,  "5A":4,  "6A":5,
    "7A":6,  "8A":7,  "9A":8,  "10A":9, "11A":10,"12A":11,
    "1B":12, "2B":13, "3B":14, "4B":15, "5B":16, "6B":17,
    "7B":18, "8B":19, "9B":20, "10B":21,"11B":22,"12B":23,
}

WEIGHTS = {"mood": 0.35, "bpm": 0.15, "transition": 0.25, "key": 0.15, "energy": 0.10}
ARTIST_PENALTY = 0.18
ALBUM_PENALTY  = 0.30


def _norm_text(s: str) -> str:
    return " ".join(str(s or "").strip().lower().split())


def _artist_tokens(s: str) -> set:
    txt = str(s or "").lower().replace("&", ",").replace(";", ",")
    return {p.strip() for p in txt.split(",") if p.strip()}


def _cos_dist(a: "np.ndarray", b: "np.ndarray") -> float:
    n = np.linalg.norm(a) * np.linalg.norm(b)
    if n < 1e-9:
        return 1.0
    return float(1.0 - np.dot(a, b) / n)


def _camelot_distance(cam_a: str, cam_b: str) -> float:
    if cam_a == "?" or cam_b == "?":
        return 0.5
    ia = CAMELOT_TO_IDX.get(cam_a, -1)
    ib = CAMELOT_TO_IDX.get(cam_b, -1)
    if ia < 0 or ib < 0:
        return 0.5
    ring_a, num_a = ia // 12, ia % 12
    ring_b, num_b = ib // 12, ib % 12
    circ = min(abs(num_a - num_b), 12 - abs(num_a - num_b))
    ring_penalty = 0 if ring_a == ring_b else 1
    return min(circ + ring_penalty, 6) / 6.0


def _track_distance(ta: dict, tb: dict, emb_a, emb_b) -> float:
    fa = ta.get("features") or {}
    fb = tb.get("features") or {}
    end_a   = ta.get("end_seg", fa)
    start_b = tb.get("start_seg", fb)

    if emb_a is not None and emb_b is not None:
        d_mood = min(_cos_dist(emb_a, emb_b) / 2.0, 1.0)
    else:
        ca = fa.get("chroma_cens") or fa.get("chroma_vals")
        cb = fb.get("chroma_cens") or fb.get("chroma_vals")
        if ca and cb:
            n = min(len(ca), len(cb))
            d_mood = min(_cos_dist(np.array(ca[:n], dtype=np.float32), np.array(cb[:n], dtype=np.float32)) / 2.0, 1.0)
        else:
            d_mood = 0.5

    bpm_end_a   = end_a.get("bpm", fa.get("bpm", 120))
    bpm_start_b = start_b.get("bpm", fb.get("bpm", 120))
    d_bpm = min(abs(bpm_end_a - bpm_start_b) / 200.0, 1.0)

    mfcc_ea = end_a.get("mfcc20") or end_a.get("mfcc13") or fa.get("mfcc20") or fa.get("mfcc13")
    mfcc_sb = start_b.get("mfcc20") or start_b.get("mfcc13") or fb.get("mfcc20") or fb.get("mfcc13")
    if mfcc_ea and mfcc_sb:
        va = np.array(mfcc_ea, dtype=np.float32)
        vb = np.array(mfcc_sb, np.float32)
        n  = min(len(va), len(vb))
        d_transition = min(_cos_dist(va[:n], vb[:n]) / 2.0, 1.0)
    else:
        d_transition = 0.5

    cam_a = end_a.get("camelot", fa.get("camelot", "?"))
    cam_b = start_b.get("camelot", fb.get("camelot", "?"))
    d_key = _camelot_distance(cam_a, cam_b)

    rms_ea = end_a.get("rms_db", fa.get("rms_db", -20))
    rms_sb = start_b.get("rms_db", fb.get("rms_db", -20))
    d_energy = min(abs(rms_ea - rms_sb) / 60.0, 1.0)

    base = (WEIGHTS["mood"]*d_mood + WEIGHTS["bpm"]*d_bpm
            + WEIGHTS["transition"]*d_transition + WEIGHTS["key"]*d_key
            + WEIGHTS["energy"]*d_energy)

    artist_a = _artist_tokens(ta.get("artist", ""))
    artist_b = _artist_tokens(tb.get("artist", ""))
    album_a  = _norm_text(ta.get("album", ""))
    album_b  = _norm_text(tb.get("album", ""))
    if album_a and album_b and album_a == album_b:
        base += ALBUM_PENALTY
    elif artist_a and artist_b and (artist_a & artist_b):
        base += ARTIST_PENALTY

    return min(base, 1.0 + ALBUM_PENALTY)


def _load_embedding(tid: str, db: dict):
    entry = db.get(tid)
    if not entry:
        return None
    ef = entry.get("embedding_file")
    if not ef:
        return None
    p = BASE_DIR / ef
    return np.load(str(p)).astype(np.float32) if p.exists() else None


_SORTING_CACHE = {}  # {playlist_id: {"D": ndarray, "track_ids": list, "all_tracks": list, "embeddings": list}}

def _build_distance_matrix(track_indices: list, all_tracks: list, embeddings: list) -> "np.ndarray":
    n = len(track_indices)
    D = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        for j in range(n):
            if i != j:
                D[i, j] = _track_distance(all_tracks[i], all_tracks[j], embeddings[i], embeddings[j])
    return D


def _path_cost(order: list, D: "np.ndarray") -> float:
    return sum(D[order[i], order[i+1]] for i in range(len(order)-1))


def _nearest_neighbor_init(free_ids: list, start: int, D: "np.ndarray") -> list:
    if not free_ids:
        return []
    remaining = set(free_ids)
    current   = start
    path      = []
    while remaining:
        best = min(remaining, key=lambda x: D[current, x])
        path.append(best)
        remaining.discard(best)
        current = best
    return path


def _solve_atsp_with_anchors(all_ids, anchors, slots, D, iterations=None, T_start=1.0, T_end=1e-4):
    n = len(all_ids)
    if iterations is None:
        iterations = max(n * 500, 5000)

    free_ids = [x for x in all_ids if x not in set(anchors)]

    if not anchors:
        if not free_ids:
            return all_ids
        shuffled = list(free_ids)
        random.shuffle(shuffled)
        if random.random() < 0.5:
            order = _nearest_neighbor_init(shuffled[1:], shuffled[0], D)
            order = [shuffled[0]] + order
        else:
            order = shuffled
        current_cost = _path_cost(order, D)
        best_order   = list(order)
        best_cost    = current_cost
        cooling = (T_end / T_start) ** (1.0 / max(iterations, 1))
        T = T_start
        for _ in range(iterations):
            if len(order) < 2:
                break
            i, j = sorted(random.sample(range(len(order)), 2))
            order[i:j+1] = order[i:j+1][::-1]
            nc = _path_cost(order, D)
            delta = nc - current_cost
            if delta < 0 or random.random() < math.exp(-delta / T):
                current_cost = nc
                if nc < best_cost:
                    best_order = list(order)
                    best_cost  = nc
            else:
                order[i:j+1] = order[i:j+1][::-1]
            T *= cooling
        return best_order

    n_slots    = len(anchors) + 1
    slot_open  = (list(slots) + [False] * n_slots)[:n_slots]
    open_slots = [i for i, v in enumerate(slot_open) if v]

    if not open_slots:
        return list(anchors)

    shuffled_free = list(free_ids)
    random.shuffle(shuffled_free)
    slot_contents = [[] for _ in range(n_slots)]
    for idx, fid in enumerate(shuffled_free):
        slot_contents[open_slots[idx % len(open_slots)]].append(fid)

    for si in open_slots:
        if random.random() < 0.5:
            start_anchor = anchors[si - 1] if si > 0 else (slot_contents[si][0] if slot_contents[si] else 0)
            slot_contents[si] = _nearest_neighbor_init(slot_contents[si], start_anchor, D)
        else:
            random.shuffle(slot_contents[si])

    def build_order():
        result = []
        for si in range(n_slots):
            result.extend(slot_contents[si])
            if si < len(anchors):
                result.append(anchors[si])
        return result

    current_cost = _path_cost(build_order(), D)
    best_slots   = copy.deepcopy(slot_contents)
    best_cost    = current_cost
    cooling      = (T_end / T_start) ** (1.0 / max(iterations, 1))
    T            = T_start

    for _ in range(iterations):
        if random.random() < 0.6 and open_slots:
            si = random.choice(open_slots)
            sl = slot_contents[si]
            if len(sl) < 2:
                T *= cooling; continue
            i, j = sorted(random.sample(range(len(sl)), 2))
            sl[i:j+1] = sl[i:j+1][::-1]
            nc = _path_cost(build_order(), D)
            delta = nc - current_cost
            if delta < 0 or random.random() < math.exp(-delta / T):
                current_cost = nc
                if nc < best_cost:
                    best_slots = copy.deepcopy(slot_contents)
                    best_cost  = nc
            else:
                sl[i:j+1] = sl[i:j+1][::-1]
        elif len(open_slots) >= 2 and free_ids:
            si = random.choice(open_slots)
            sj = random.choice([s for s in open_slots if s != si])
            if not slot_contents[si]:
                T *= cooling; continue
            idx = random.randrange(len(slot_contents[si]))
            tid = slot_contents[si].pop(idx)
            ins = random.randrange(len(slot_contents[sj]) + 1)
            slot_contents[sj].insert(ins, tid)
            nc = _path_cost(build_order(), D)
            delta = nc - current_cost
            if delta < 0 or random.random() < math.exp(-delta / T):
                current_cost = nc
                if nc < best_cost:
                    best_slots = copy.deepcopy(slot_contents)
                    best_cost  = nc
            else:
                slot_contents[sj].pop(ins)
                slot_contents[si].insert(idx, tid)
        T *= cooling

    slot_contents[:] = best_slots
    return build_order()


def _run_smart_sorting(console, db: dict, descs: list, pl_id: str, pl_name: str, sp):
    """Run SA sorting, return (ordered_descs, result). Result: 'retry', 'editor', 'home', 'exit'."""
    plan = _load_anchors_file(pl_id)
    if not plan:
        console.print("[yellow]No anchors found. Please create anchors first.[/yellow]")
        return descs, "editor"

    n_anchors = sum(1 for e in plan if e["type"] == "anchor")
    if n_anchors == 0:
        console.print("[yellow]No anchors in plan — nothing to sort.[/yellow]")
        return descs, "editor"

    # Build index arrays
    desc_by_id = {d["spotify_id"]: d for d in descs}
    track_ids   = [d["spotify_id"] for d in descs]
    tid_to_idx  = {tid: i for i, tid in enumerate(track_ids)}

    anchors_idx = [tid_to_idx[e["spotify_id"]] for e in plan if e["type"] == "anchor"]
    slots = []
    ap = [j for j, e in enumerate(plan) if e["type"] == "anchor"]
    if ap:
        slots = [any(plan[k]["type"] == "placeholder" for k in range(0, ap[0]))]
        for i in range(len(ap)-1):
            slots.append(any(plan[k]["type"] == "placeholder" for k in range(ap[i]+1, ap[i+1])))
        slots.append(any(plan[k]["type"] == "placeholder" for k in range(ap[-1]+1, len(plan))))

    # Load track data from DB + embeddings
    all_tracks = [db[tid] for tid in track_ids if tid in db]
    if len(all_tracks) != len(track_ids):
        console.print("[yellow]Some tracks not in DB — sorting may be degraded.[/yellow]")
        # Re-index track list and rebuild slots to match filtered anchors
        valid_ids     = [t.get("spotify_id", tid) for tid, t in zip(track_ids, all_tracks)]
        new_tid_to_idx = {tid: i for i, tid in enumerate(valid_ids)}
        # Build filtered plan (only anchors whose tracks exist in DB) and recalc slots
        filtered_anchors = []
        new_plan = []
        for e in plan:
            if e["type"] == "anchor" and e["spotify_id"] in new_tid_to_idx:
                filtered_anchors.append(new_tid_to_idx[e["spotify_id"]])
                new_plan.append(e)
            elif e["type"] == "placeholder":
                new_plan.append(e)
            # skip anchors with missing tracks
        anchors_idx = filtered_anchors
        # Rebuild slots from filtered plan
        slots = []
        ap_new = [j for j, e in enumerate(new_plan) if e["type"] == "anchor"]
        if ap_new:
            slots = [any(new_plan[k]["type"] == "placeholder" for k in range(0, ap_new[0]))]
            for i in range(len(ap_new)-1):
                slots.append(any(new_plan[k]["type"] == "placeholder" for k in range(ap_new[i]+1, ap_new[i+1])))
            slots.append(any(new_plan[k]["type"] == "placeholder" for k in range(ap_new[-1]+1, len(new_plan))))
        all_tracks = [db[tid] for tid in valid_ids]
        track_ids  = valid_ids
        tid_to_idx = new_tid_to_idx

    embeddings = [_load_embedding(tid, db) for tid in track_ids]

    n_total = len(all_tracks)
    # Use cached distance matrix if track_ids haven't changed
    cache_key = tuple(track_ids)
    cached = _SORTING_CACHE.get(pl_id)
    if cached and cached.get("track_ids") == cache_key:
        D = cached["D"]
        console.print(f"[dim]Using cached distance matrix ({n_total}×{n_total})[/dim]")
    else:
        console.print(f"[dim]Building distance matrix for {n_total} tracks ({n_total*n_total} pairs)...[/dim]")
        D = _build_distance_matrix(list(range(n_total)), all_tracks, embeddings)
        _SORTING_CACHE[pl_id] = {"D": D, "track_ids": cache_key, "all_tracks": all_tracks, "embeddings": embeddings}

    all_indices = list(range(len(all_tracks)))
    iters = max(len(all_tracks) * 500, 5000)
    N_RUNS = 100

    console.print(f"[cyan]SA: {N_RUNS} runs × {iters} iterations...[/cyan]")
    best_order, best_cost = None, float("inf")
    for run in range(N_RUNS):
        candidate = _solve_atsp_with_anchors(all_indices, anchors_idx, slots, D, iterations=iters)
        cost = _path_cost(candidate, D)
        if cost < best_cost:
            best_cost, best_order = cost, candidate
        if (run + 1) % 20 == 0 or run == 0:
            console.print(f"  [dim]Run {run+1}/{N_RUNS}  best={best_cost:.4f}[/dim]")

    ordered = best_order
    console.print(f"[bold green]Best cost: {best_cost:.4f}[/bold green]")

    # Build ordered list
    ordered_descs = []
    anchor_set = set(anchors_idx)
    for idx in ordered:
        tid = track_ids[idx]
        d = desc_by_id.get(tid, {"spotify_id": tid, "name": "?", "artist": "?"})
        ordered_descs.append(d)

    # Save result
    result_file = ANCHORS_DIR / f"result_{pl_id}.json"
    result_data = {
        "playlist_id": pl_id,
        "playlist_name": pl_name,
        "saved_at": datetime.datetime.now().isoformat(),
        "cost": float(best_cost),
        "tracks": [
            {
                "spotify_id": d["spotify_id"],
                "name": d.get("name", ""),
                "artist": d.get("artist", ""),
                "bpm": d.get("bpm", 0),
                "camelot": d.get("camelot", ""),
            }
            for d in ordered_descs
        ],
    }
    result_file.write_text(json.dumps(result_data, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"[green]✓ Result saved to {result_file.name}[/green]")

    # Display result table
    tbl = Table(title=f"Sorted — {pl_name}", header_style="bold")
    tbl.add_column("#", style="dim", width=4)
    tbl.add_column("Track", width=44)
    tbl.add_column("BPM", width=5)
    tbl.add_column("Key", width=7)
    for i, d in enumerate(ordered_descs):
        tbl.add_row(
            str(i + 1),
            f"{d['name'][:32]} — {d.get('artist','')[:16]}",
            f"{d.get('bpm',0):.0f}",
            f"{d.get('camelot','?')}",
        )
    console.print(tbl)

    # Post-sort menu
    while True:
        console.print()
        console.print("  [cyan]S[/cyan] — Run another sorting")
        console.print("  [cyan]N[/cyan] — Save as New Playlist in Spotify")
        console.print(f"  [cyan]U[/cyan] — Update existing playlist \"{pl_name}\"")
        console.print("  [cyan]E[/cyan] — Return to Anchor Editor")
        console.print("  [cyan]H[/cyan] — Return to Home")
        console.print("  [cyan]X[/cyan] — Exit")
        raw = Prompt.ask("[bold]Action[/bold] [S/N/U/E/H/X]").strip().upper()
        if not raw:
            continue

        ch = raw[0]
        if ch == "S":
            return descs, "retry"

        elif ch == "N":
            # Save as new playlist in Spotify
            if not sp:
                console.print("[red]Spotify not connected.[/red]")
                continue
            try:
                uris = [f"spotify:track:{d['spotify_id']}" for d in ordered_descs if d.get("spotify_id")]
                ts_tag = datetime.datetime.now().strftime("%Y%m%d%H%M")
                new_name = f"{pl_name} {ts_tag}"
                new_pl, err = _spotify_request_with_retries(
                    sp, "POST", "me/playlists",
                    payload={"name": new_name, "public": False},
                )
                if err or not new_pl:
                    console.print(f"[red]Error creating playlist: {err}[/red]")
                else:
                    all_ok = True
                    for i in range(0, len(uris), 100):
                        chunk = uris[i:i+100]
                        _, err2 = _spotify_request_with_retries(
                            sp, "POST", f"playlists/{new_pl['id']}/items",
                            payload={"uris": chunk},
                        )
                        if err2:
                            console.print(f"[red]Error adding batch {i//100 + 1}: {err2}[/red]")
                            all_ok = False
                            break
                        time.sleep(0.3)  # gentle throttle between chunks
                    if all_ok:
                        console.print(f"[bold green]✓ New playlist created: {new_name}[/bold green]")
                        url = (new_pl.get("external_urls") or {}).get("spotify")
                        if url:
                            console.print(f"[cyan]  {url}[/cyan]")
            except Exception as exc:
                console.print(f"[red]Error: {exc}[/red]")

        elif ch == "U":
            # Update existing playlist
            if not sp:
                console.print("[red]Spotify not connected.[/red]")
                continue
            console.print(f"[cyan]Backing up existing playlist...[/cyan]")
            try:
                existing = get_playlist_tracks(sp, pl_id)
            except Exception:
                existing = []
            bk_file = ANCHORS_DIR / f"backup_{pl_id}.json"
            _atomic_write_json(bk_file, {"playlist_id": pl_id, "playlist_name": pl_name,
                                          "saved_at": datetime.datetime.now().isoformat(), "tracks": existing})
            console.print(f"[green]✓ Backup saved to {bk_file.name}[/green]")

            # Reorder: PUT first 100, then POST rest in batches (API limit)
            uris = [f"spotify:track:{d['spotify_id']}" for d in ordered_descs if d.get("spotify_id")]
            if uris:
                first_chunk = uris[:100]
                rest_chunks = [uris[i:i+100] for i in range(100, len(uris), 100)]
                _, err = _spotify_request_with_retries(sp, "PUT", f"playlists/{pl_id}/items", {"uris": first_chunk})
                if err:
                    console.print(f"[red]Error updating playlist: {err}[/red]")
                else:
                    for chunk in rest_chunks:
                        _, err2 = _spotify_request_with_retries(sp, "POST", f"playlists/{pl_id}/items", {"uris": chunk})
                        if err2:
                            console.print(f"[red]Error adding batch: {err2}[/red]")
                            break
                        time.sleep(0.3)  # gentle throttle between chunks
                    else:
                        console.print(f"[bold green]✓ Playlist \"{pl_name}\" updated![/bold green]")
            else:
                console.print("[yellow]No tracks to update.[/yellow]")

        elif ch == "E":
            return descs, "editor"

        elif ch == "H":
            return descs, "home"

        elif ch == "X":
            return descs, "exit"


# ─── Anchor editor ────────────────────────────────────────────────────────────

ANCHOR_SYSTEM_PROMPT = (
    "You are a music curator and playlist architect with deep knowledge of "
    "electronic, ambient, downtempo, and experimental music.\n\n"
    "You will receive a list of tracks from a single playlist, each with:\n"
    "- A short audio-based description\n"
    "- Key audio features (BPM, key, loudness, harmonic ratio, dynamics)\n"
    "- Artist and track name\n\n"
    "Your task: select exactly N anchor tracks that best realise the requested "
    "playlist structure type, and arrange them in the correct order.\n"
    "Choose tracks whose descriptions and features match the energy arc, mood "
    "progression, and dynamic contour described by the structure. "
    "Prioritise diversity of textures and keys.\n\n"
    "OUTPUT FORMAT — strictly follow this structure, no extra text:\n"
    "ANCHORS:\n"
    "1. Track Name — Artist\n"
    "2. Track Name — Artist\n"
    "...\n"
    "N. Track Name — Artist"
)


def _load_anchors_file(playlist_id: str) -> list | None:
    """Load anchors from anchors/anchors_<pl_id>.json. Returns plan list or None."""
    af = ANCHORS_DIR / f"anchors_{playlist_id}.json"
    if not af.exists():
        return None
    try:
        return json.loads(af.read_text(encoding="utf-8")).get("plan", None)
    except Exception:
        return None


def _save_anchors_file(playlist_id: str, playlist_name: str, plan: list):
    """Save anchors plan to anchors/anchors_<pl_id>.json."""
    af = ANCHORS_DIR / f"anchors_{playlist_id}.json"
    data = {
        "playlist_id":   playlist_id,
        "playlist_name": playlist_name,
        "saved_at":      datetime.datetime.now().isoformat(),
        "plan":          plan,
    }
    af.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _plan_from_track_list(track_list: list, descs: list) -> list:
    """Convert a list of {"spotify_id":...} dicts into an editor plan."""
    plan = []
    for t in track_list:
        plan.append({"type": "anchor", "spotify_id": t["spotify_id"]})
    return plan


def _build_descriptions_block(descs: list) -> str:
    """Build a compact block of track descriptions for the LLM prompt."""
    lines = []
    for d in descs:
        sid  = d.get("spotify_id", "")
        name = d.get("name", "?")
        art  = d.get("artist", "?")
        desc = d.get("description", "")[:150]
        bpm  = d.get("bpm", 0)
        key  = d.get("key", "")
        loud = d.get("loudness_db", 0)
        harm = d.get("harm_ratio", 0)
        dyn  = d.get("dynamic_range", 0)
        onset= d.get("onset_str", 0)
        bass = d.get("bass_pct", 0)
        feat = f"BPM={bpm:.0f} Key={key} Loud={loud:.1f}dB Harm={harm:.2f} Dyn={dyn:.1f}dB Onset={onset:.2f} Bass={bass:.1f}%"
        lines.append(f'  "{name}" — {art}  [{feat}]\n    {desc}')
    return "\n\n".join(lines)


def _parse_llm_anchor_list(raw_text: str, descs: list, expected_n: int) -> list:
    """Parse LLM response like 'ANCHORS:\n1. Name — Artist\n...' into track dicts."""
    section = re.search(r"ANCHORS\s*:(.*)", raw_text, flags=re.DOTALL | re.IGNORECASE)
    block   = section.group(1).strip() if section else raw_text

    parsed = []
    for line in block.splitlines():
        m = re.match(r"\s*\d+\.\s*(.+?)\s*[-\u2014\u2013]\s*(.+)", line)
        if m:
            parsed.append({
                "name":   m.group(1).strip().strip('\"\' '),
                "artist": m.group(2).strip().strip('\"\' '),
            })
        if len(parsed) >= expected_n:
            break

    # Match against descriptions
    results = []
    descs_by_name = {d["name"].lower(): d for d in descs}
    for a in parsed:
        key = a["name"].lower()
        if key in descs_by_name:
            results.append(descs_by_name[key])
        else:
            # fuzzy match
            found = next(
                (d for d in descs if a["name"].lower() in d["name"].lower()
                 or d["name"].lower() in a["name"].lower()),
                None,
            )
            if found:
                results.append(found)

    return results


def _run_ai_anchor_generation(console, descs: list, pl_name: str, pl_id: str) -> list:
    """Ask LLM to generate anchors. Returns plan list."""
    _init_llm_client(console)

    # Show structure options
    console.print("\n[bold cyan]Playlist structure types:[/bold cyan]\n")
    tbl = Table(show_header=True, header_style="bold")
    tbl.add_column("#", style="dim", width=3)
    tbl.add_column("Type", width=18)
    tbl.add_column("Description", width=80)
    for i, s in enumerate(PLAYLIST_STRUCTURES, 1):
        tbl.add_row(str(i), s["name"], s["desc"])
    console.print(tbl)

    while True:
        raw = Prompt.ask(
            f"\n[bold]Select structure type # (1-{len(PLAYLIST_STRUCTURES)})[/bold]",
            default="1",
        ).strip()
        if raw.isdigit() and 1 <= int(raw) <= len(PLAYLIST_STRUCTURES):
            chosen = PLAYLIST_STRUCTURES[int(raw) - 1]
            break
        console.print(f"[red]Please enter 1-{len(PLAYLIST_STRUCTURES)}[/red]")

    # Compute adaptive anchor count from percentage of total tracks
    n_anchors = max(3, int(len(descs) * chosen.get("anchor_pct", 20) / 100))
    console.print(
        f"\n[cyan]Structure: [bold]{chosen['name']}[/bold] — {chosen['desc']}[/cyan]"
    )
    console.print(
        f"[dim]Target anchors: {n_anchors}  ({chosen.get('anchor_pct',20)}% of {len(descs)} tracks, "
        f"~{max(len(descs) // max(n_anchors, 1), 1)} per segment)[/dim]"
    )

    # Build user message
    desc_block = _build_descriptions_block(descs)
    user_msg = (
        f'Playlist name: "{pl_name}"\n'
        f'Total tracks: {len(descs)}\n\n'
        f'Requested structure: {chosen["name"]}\n'
        f'{chosen["desc"]}\n\n'
        f'Select exactly {n_anchors} anchor tracks and arrange them '
        f'to create this structure. Below is the full track list '
        f'with audio descriptions and technical features:\n\n'
        f'{desc_block}\n\n'
        f'Now select {n_anchors} anchor tracks and order them.'
    )

    console.print("[cyan]Asking LLM to choose anchors...[/cyan]", end=" ", highlight=False)
    raw_resp = ""
    try:
        raw_resp = _llm_chat(
            console, ANCHOR_SYSTEM_PROMPT, user_msg,
            temperature=0.4, max_tokens=6000,
        )
        console.print("[green]✓[/green]")
    except Exception as e:
        console.print(f"[red]LLM error: {e}[/red]")
        return []

    matched = _parse_llm_anchor_list(raw_resp, descs, n_anchors)
    if not matched:
        console.print("[yellow]⚠ Could not parse anchors from LLM response[/yellow]")
        return []

    console.print(f"[green]✓ LLM selected {len(matched)} anchors[/green]")
    plan = _plan_from_track_list(matched, descs)
    _save_anchors_file(pl_id, pl_name, plan)
    console.print(f"[green]✓ Anchors saved to anchors/anchors_{pl_id}.json[/green]")
    return plan


def _print_anchor_plan(console, plan: list, descs: list):
    """Display current anchor plan with track info."""
    if not plan:
        console.print("[yellow]Plan is empty[/yellow]")
        return
    desc_by_id = {d["spotify_id"]: d for d in descs}
    lines = []
    for i, e in enumerate(plan):
        if e["type"] == "anchor":
            sid = e.get("spotify_id", "")
            d   = desc_by_id.get(sid, {})
            lines.append(
                f"  [bold]{i+1}.[/bold] ⚓ {d.get('name','?')[:40]} "
                f"[cyan]{d.get('artist','?')[:20]}[/cyan] "
                f"[green]{d.get('bpm',0):.0f} BPM[/green] "
                f"[yellow]{d.get('camelot','?')}[/yellow]"
            )
        else:
            lines.append(f"  [bold]{i+1}.[/bold] [dim]── [ placeholder ] ──[/dim]")
    console.print(Panel("\n".join(lines), title="Anchor Plan", border_style="green"))


_ANCHOR_EDITOR_HELP = Panel(
    "[bold]Anchor Editor[/bold]\n"
    "  [cyan]a <#>[/cyan]      — add track as anchor (number from track list)\n"
    "  [cyan]tracks[/cyan]     — show all tracks with IDs and descriptions\n"
    "  [cyan]ph[/cyan]         — add placeholder at end\n"
    "  [cyan]del <#>[/cyan]    — delete element at position\n"
    "  [cyan]u <#>[/cyan]      — move element up\n"
    "  [cyan]dn <#>[/cyan]     — move element down\n"
    "  [cyan]show[/cyan]       — show current plan\n"
    "  [cyan]ai[/cyan]         — generate anchors with AI\n"
    "  [cyan]go[/cyan]         — run smart sorting\n"
    "  [cyan]home[/cyan]       — return to playlist selection\n"
    "  [cyan]exit[/cyan]       — close the script",
    border_style="blue",
)


def anchor_editor_loop(console, descs: list, db: dict, pl_id: str, pl_name: str, sp=None) -> str:
    """
    Interactive anchor editor.
    Returns "home" to go back to playlist selection, "exit" to quit.
    """
    # Load existing anchors or start empty
    plan = _load_anchors_file(pl_id) or []
    # Validate: keep only anchors whose spotify_ids are in the current playlist
    desc_ids = set(d["spotify_id"] for d in descs)
    valid_plan = []
    removed = 0
    for e in plan:
        if e["type"] != "anchor":
            valid_plan.append(e)
        elif e.get("spotify_id", "") in desc_ids:
            valid_plan.append(e)
        else:
            removed += 1
    if removed:
        console.print(f"[yellow]{removed} anchor(s) removed — no longer in playlist[/yellow]")
    plan = valid_plan

    desc_by_id = {d["spotify_id"]: d for d in descs}

    console.print(_ANCHOR_EDITOR_HELP)

    if plan:
        console.print("[green]Loaded anchor plan:[/green]")
        _print_anchor_plan(console, plan, descs)

    while True:
        cmd = Prompt.ask("\n[bold]anchor>[/bold]").strip().lower()

        if cmd.startswith("a "):
            try:
                idx = int(cmd[2:]) - 1
                if not (0 <= idx < len(descs)):
                    console.print(f"[red]No track #{idx+1}. Use 'tracks' to list.[/red]")
                else:
                    sid = descs[idx]["spotify_id"]
                    plan.append({"type": "anchor", "spotify_id": sid})
                    console.print(f"[green]+ {descs[idx]['name']}[/green]")
                    _save_anchors_file(pl_id, pl_name, plan)
            except ValueError:
                console.print("[red]Usage: a <number>[/red]")

        elif cmd == "tracks":
            # Show compact track list with IDs
            tbl = Table(show_header=True, header_style="bold")
            tbl.add_column("#", style="dim", width=4)
            tbl.add_column("Track", width=34)
            tbl.add_column("BPM", width=5)
            tbl.add_column("Key", width=7)
            tbl.add_column("Desc", width=60)
            for i, d in enumerate(descs):
                desc = d.get("description", "")
                desc_display = desc[:58] + ("..." if len(desc) > 58 else "")
                tbl.add_row(
                    str(i + 1),
                    f"{d['name'][:30]} — {d['artist'][:16]}",
                    f"{d.get('bpm',0):.0f}",
                    f"{d.get('camelot','?')}",
                    desc_display,
                )
            console.print(tbl)

        elif cmd == "ph":
            plan.append({"type": "placeholder"})
            console.print(f"[cyan]Placeholder at position {len(plan)}[/cyan]")

        elif cmd.startswith("del "):
            try:
                pos = int(cmd[4:]) - 1
                if 0 <= pos < len(plan):
                    removed = plan.pop(pos)
                    label = desc_by_id.get(removed.get("spotify_id",""), {}).get("name", "placeholder")
                    console.print(f"[yellow]Removed: {label}[/yellow]")
                    _save_anchors_file(pl_id, pl_name, plan)
                else:
                    console.print("[red]Invalid position[/red]")
            except ValueError:
                console.print("[red]Usage: del <number>[/red]")

        elif cmd.startswith("u "):
            try:
                pos = int(cmd[2:]) - 1
                if 1 <= pos < len(plan):
                    plan[pos-1], plan[pos] = plan[pos], plan[pos-1]
                    console.print("[green]↑[/green]")
                    _save_anchors_file(pl_id, pl_name, plan)
                else:
                    console.print("[red]Cannot move up[/red]")
            except ValueError:
                console.print("[red]Usage: u <number>[/red]")

        elif cmd.startswith("dn "):
            try:
                pos = int(cmd[3:]) - 1
                if 0 <= pos < len(plan) - 1:
                    plan[pos], plan[pos+1] = plan[pos+1], plan[pos]
                    console.print("[green]↓[/green]")
                    _save_anchors_file(pl_id, pl_name, plan)
                else:
                    console.print("[red]Cannot move down[/red]")
            except ValueError:
                console.print("[red]Usage: dn <number>[/red]")

        elif cmd == "show":
            _print_anchor_plan(console, plan, descs)

        elif cmd == "ai":
            new_plan = _run_ai_anchor_generation(console, descs, pl_name, pl_id)
            if new_plan:
                plan = new_plan
                console.print("[green]Anchors generated by AI — loaded into editor[/green]")
                _print_anchor_plan(console, plan, descs)
            else:
                console.print("[yellow]AI generation returned no results[/yellow]")

        elif cmd == "go":
            _save_anchors_file(pl_id, pl_name, plan)
            n_anchors = sum(1 for e in plan if e["type"] == "anchor")
            if n_anchors == 0:
                console.print("[yellow]No anchors in plan — nothing to sort.[/yellow]")
                continue
            ordered, action = _run_smart_sorting(console, db, descs, pl_id, pl_name, sp)
            if action == "retry":
                continue  # re-run sorting
            elif action == "editor":
                _print_anchor_plan(console, plan, descs)
                continue
            elif action == "home":
                return "home"
            elif action == "exit":
                return "exit"

        elif cmd == "home":
            _save_anchors_file(pl_id, pl_name, plan)
            console.print("[dim]Anchors saved — returning to playlists.[/dim]")
            return "home"

        elif cmd == "exit":
            _save_anchors_file(pl_id, pl_name, plan)
            console.print("[dim]Anchors saved — exiting.[/dim]")
            return "exit"

        else:
            console.print(_ANCHOR_EDITOR_HELP)


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
                    # ── Recover from backup → go directly to anchor editor ──
                    bk_file = ANCHORS_DIR / f"backup_{pl_id}.json"
                    if not bk_file.exists():
                        console.print("[red]Backup file not found.[/red]")
                        break
                    console.print(
                        f"\n[bold magenta]Recovering from backup:[/bold magenta] "
                        f"[dim]{bk_file.name}[/dim]"
                    )
                    # Load backup as track list
                    try:
                        bk_data = json.loads(bk_file.read_text(encoding="utf-8"))
                        bk_tracks = bk_data.get("tracks", [])
                        if not bk_tracks:
                            console.print("[yellow]Backup is empty — nothing to recover.[/yellow]")
                            break
                        # Build descriptions from backup tracks
                        descs = []
                        for t in bk_tracks:
                            tid = t.get("id") or t.get("spotify_id", "")
                            descs.append({
                                "spotify_id": tid,
                                "name": t.get("name", "?"),
                                "artist": t.get("artist", "?"),
                                "album": t.get("album", "?"),
                                "description": "",
                                "playlist": pl_name,
                                "bpm": 0, "key": "", "camelot": "",
                                "loudness_db": 0, "dynamic_range": 0,
                                "harm_ratio": 0, "flatness": 0,
                                "bass_pct": 0, "mid_pct": 0, "high_pct": 0,
                                "onset_str": 0, "duration_ms": 0,
                            })
                        # Enrich from DB
                        for d in descs:
                            tid = d.get("spotify_id")
                            if tid and tid in db:
                                entry = db[tid]
                                f = entry.get("features") or {}
                                d["bpm"] = round(f.get("bpm", 0), 1)
                                d["key"] = f"{f.get('chroma_key','')} {f.get('mode','')}".strip()
                                d["camelot"] = f.get("camelot", "")
                                d["loudness_db"] = round(f.get("rms_db", 0), 1)
                                d["dynamic_range"] = round(f.get("dynamic_range", 0), 1)
                                d["harm_ratio"] = round(f.get("harm_ratio", 0), 2)
                                d["flatness"] = round(f.get("flatness", 0), 3)
                                d["bass_pct"] = round(f.get("bass", 0)*100, 1)
                                d["mid_pct"] = round(f.get("mid", 0)*100, 1)
                                d["high_pct"] = round(f.get("high", 0)*100, 1)
                                d["onset_str"] = round(f.get("onset_str", 0), 2)
                                d["duration_ms"] = entry.get("duration_ms", 0)
                        console.print(f"[green]✓ Loaded {len(descs)} tracks from backup[/green]")
                        result = anchor_editor_loop(console, descs, db, pl_id, pl_name, sp)
                        if result == "exit":
                            raise KeyboardInterrupt()
                        # if home, fall through to break inner loop
                    except Exception as e:
                        console.print(f"[red]Error loading backup: {e}[/red]")
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

                    # ── Step 2.2: Launch anchor editor ───────────────────────
                    console.print(
                        f"\n[bold green]✓ {len(descs)} track descriptions ready[/bold green]"
                    )
                    # Check if anchors file exists and load it
                    af = ANCHORS_DIR / f"anchors_{pl_id}.json"
                    if af.exists():
                        console.print(
                            f"[green]✓ Existing anchor plan found: "
                            f"[dim]{af.name}[/dim][/green]"
                        )
                    else:
                        console.print("[dim]No existing anchor plan — starting fresh.[/dim]")

                    result = anchor_editor_loop(console, descs, db, pl_id, pl_name, sp)
                    if result == "exit":
                        console.print("[yellow]Exiting...[/yellow]")
                        # Break both loops — exit script
                        raise KeyboardInterrupt()
                    elif result == "home":
                        break  # back to playlist selection

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
