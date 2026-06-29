"""
Central configuration for Playlist Arranger.
Loads .env, defines all constants, Settings dataclass.
"""

import os
import pathlib
from dataclasses import dataclass, field

# ─── Load .env ────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv as _load_dotenv

    _load_dotenv(pathlib.Path(__file__).parent.parent / ".env")
except ImportError:
    pass

# ─── Base paths ────────────────────────────────────────────────────────────────
BASE_DIR = pathlib.Path(__file__).parent.parent
DB_PATH_DEFAULT = BASE_DIR / "database" / "tracks_db.sqlite"
EMBEDS_DIR_DEFAULT = BASE_DIR / "embeddings"
CACHE_DIR_DEFAULT = BASE_DIR / "cache"
ANCHORS_DIR_DEFAULT = BASE_DIR / "anchors"

# Ensure dirs exist
EMBEDS_DIR_DEFAULT.mkdir(exist_ok=True)
CACHE_DIR_DEFAULT.mkdir(exist_ok=True)
ANCHORS_DIR_DEFAULT.mkdir(exist_ok=True)

# ─── Audio constants ──────────────────────────────────────────────────────────
SAMPLE_RATE = 44100
MERT_SR = 24000
BUFFER_SECONDS = 30

POLL_NORMAL = 5.0
POLL_FAST = 2.0
POLL_SCROBBLE = 15.0

CHUNK = 16384
SEG_SECONDS = 10
SCROBBLE_PCT = 0.90
SCROBBLE_MIN_MS = 4 * 60 * 1000
FULL_BUF_MAX = SAMPLE_RATE * 10 * 60

# ─── Spotify constants ────────────────────────────────────────────────────────
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

# ─── Music theory ─────────────────────────────────────────────────────────────
KEY_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

CAMELOT = {
    (0, 1): "8B",
    (1, 1): "3B",
    (2, 1): "10B",
    (3, 1): "5B",
    (4, 1): "12B",
    (5, 1): "7B",
    (6, 1): "2B",
    (7, 1): "9B",
    (8, 1): "4B",
    (9, 1): "11B",
    (10, 1): "6B",
    (11, 1): "1B",
    (0, 0): "5A",
    (1, 0): "12A",
    (2, 0): "7A",
    (3, 0): "2A",
    (4, 0): "9A",
    (5, 0): "4A",
    (6, 0): "11A",
    (7, 0): "6A",
    (8, 0): "1A",
    (9, 0): "8A",
    (10, 0): "3A",
    (11, 0): "10A",
}

CAMELOT_TO_IDX = {
    "1A": 0,
    "2A": 1,
    "3A": 2,
    "4A": 3,
    "5A": 4,
    "6A": 5,
    "7A": 6,
    "8A": 7,
    "9A": 8,
    "10A": 9,
    "11A": 10,
    "12A": 11,
    "1B": 12,
    "2B": 13,
    "3B": 14,
    "4B": 15,
    "5B": 16,
    "6B": 17,
    "7B": 18,
    "8B": 19,
    "9B": 20,
    "10B": 21,
    "11B": 22,
    "12B": 23,
}

# ─── Sorting weights ──────────────────────────────────────────────────────────
WEIGHTS = {"mood": 0.35, "bpm": 0.15, "transition": 0.25, "key": 0.15, "energy": 0.10}
ARTIST_PENALTY = 0.18
ALBUM_PENALTY = 0.30

# ─── LLM configuration ────────────────────────────────────────────────────────
LLM_BACKEND = os.getenv("LLM", "ollama").strip().lower()
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:26b").strip()
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "ollama").strip()
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-large-latest").strip()
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "").strip()

# ─── Selected audio device index (default: first WASAPI loopback) ─────────────
SELECTED_AUDIO_DEVICE_INDEX = None

# ─── Local music folder (default server path) ────────────────────────────────
LOCAL_MUSIC_DIR = os.getenv("LOCAL_MUSIC_DIR", str(BASE_DIR)).strip()
if LOCAL_MUSIC_DIR and not pathlib.Path(LOCAL_MUSIC_DIR).is_absolute():
    LOCAL_MUSIC_DIR = str(BASE_DIR / LOCAL_MUSIC_DIR)

# ─── Duration tolerance ───────────────────────────────────────────────────────
DURATION_TOLERANCE = 0.01  # 1%


# ─── Settings dataclass ───────────────────────────────────────────────────────
@dataclass
class Settings:
    db_path: pathlib.Path = DB_PATH_DEFAULT
    embeds_dir: pathlib.Path = EMBEDS_DIR_DEFAULT
    cache_dir: pathlib.Path = CACHE_DIR_DEFAULT
    anchors_dir: pathlib.Path = ANCHORS_DIR_DEFAULT

    # SA algorithm parameters
    sa_iterations_multiplier: int = 500
    sa_n_runs: int = 100
    sa_T_start: float = 1.0
    sa_T_end: float = 1e-4

    # Weights (slider values 0.0-1.0)
    w_mood: float = 0.35
    w_bpm: float = 0.15
    w_transition: float = 0.25
    w_key: float = 0.15
    w_energy: float = 0.10

    artist_penalty: float = 0.18
    album_penalty: float = 0.30
    duration_tolerance: float = 0.01

    # Local music
    local_music_dir: str = LOCAL_MUSIC_DIR

    # LLM
    llm_backend: str = LLM_BACKEND
    ollama_model: str = OLLAMA_MODEL
    deepseek_model: str = DEEPSEEK_MODEL
    mistral_model: str = MISTRAL_MODEL

    # Audio
    selected_audio_device_index: int | None = SELECTED_AUDIO_DEVICE_INDEX


def load_settings() -> Settings:
    """Load settings from cache/settings.json or return defaults."""
    settings_path = CACHE_DIR_DEFAULT / "settings.json"
    if settings_path.exists():
        try:
            import json

            data = json.loads(settings_path.read_text(encoding="utf-8"))
            s = Settings()
            for key, val in data.items():
                if key in ("db_path", "embeds_dir", "cache_dir", "anchors_dir"):
                    val = pathlib.Path(val)
                if hasattr(s, key):
                    setattr(s, key, val)
            return s
        except Exception:
            pass
    return Settings()


def save_settings(settings: Settings) -> None:
    """Persist settings to cache/settings.json atomically."""
    from playlist_arranger.cache.store import atomic_write_json

    data = {
        k: str(v) if isinstance(v, pathlib.Path) else v
        for k, v in settings.__dict__.items()
    }
    atomic_write_json(CACHE_DIR_DEFAULT / "settings.json", data)