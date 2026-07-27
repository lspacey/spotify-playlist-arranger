"""
Central configuration for Playlist Arranger.
Loads .env, defines all constants, Settings dataclass.
"""

import os
import pathlib
import logging
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler

# ─── Home directory (where .env lives) ─────────────────────────────────────────
HOME_DIR = pathlib.Path(__file__).parent.parent.resolve()

# ─── Load .env ────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv as _load_dotenv

    _load_dotenv(HOME_DIR / ".env")
except ImportError:
    pass

# ─── Path resolution helpers ──────────────────────────────────────────────────
def _to_portable(path: pathlib.Path) -> str:
    """Convert absolute path to portable form for settings.json.

    If the path is under HOME_DIR, represent it as a relative path
    starting with ``.\\``. Otherwise return the absolute path as-is.
    """
    try:
        rel = path.relative_to(HOME_DIR)
        return ".\\" + str(rel)
    except ValueError:
        return str(path)


def _resolve_path(raw: str | pathlib.Path) -> pathlib.Path:
    """Resolve a path string from settings to an absolute Path.

    - Absolute paths are kept as-is.
    - Paths starting with ``.\\`` are resolved relative to HOME_DIR.
    - Other relative paths are also resolved relative to HOME_DIR for safety.
    """
    if isinstance(raw, pathlib.Path):
        # Already a Path – if it looks like a portable relative we still resolve
        if raw.is_absolute():
            return raw
        return (HOME_DIR / str(raw).removeprefix(".\\")).resolve()
    s = str(raw).strip()
    if not s:
        return HOME_DIR
    if s.startswith(".\\"):
        return (HOME_DIR / s[2:]).resolve()
    p = pathlib.Path(s)
    if p.is_absolute():
        return p
    return (HOME_DIR / s).resolve()


# ─── Default paths (relative to HOME_DIR) ─────────────────────────────────────
DB_PATH_DEFAULT = pathlib.Path(_resolve_path(".\\database\\tracks_db.sqlite"))
EMBEDS_DIR_DEFAULT = pathlib.Path(_resolve_path(".\\embeddings"))
CACHE_DIR_DEFAULT = pathlib.Path(_resolve_path(".\\cache"))
ANCHORS_DIR_DEFAULT = pathlib.Path(_resolve_path(".\\anchors"))

# Ensure dirs exist
EMBEDS_DIR_DEFAULT.mkdir(exist_ok=True)
CACHE_DIR_DEFAULT.mkdir(exist_ok=True)
ANCHORS_DIR_DEFAULT.mkdir(exist_ok=True)

# ─── Audio constants ──────────────────────────────────────────────────────────
SAMPLE_RATE = 44100
MERT_SR = 24000
BUFFER_SECONDS = 30
SILENCE_RMS_THRESHOLD = 0.001  # RMS below this → silence (chosen via empirical testing: typical music RMS ~0.01-0.3, pure silence near 0.0001)
MIN_COVERAGE_PCT = 0.90        # Track must be >= 90% captured to submit for analysis
MAX_ANALYZE_BUFFER_S = 900      # Safety cap: max audio buffer duration (15 min, ~400 MB at 22050 Hz mono float32)

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
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip()
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:26b").strip()
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL", "mistral-large-latest").strip()
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "").strip()
MISTRAL_BASE_URL = os.getenv("MISTRAL_BASE_URL", "https://api.mistral.ai/v1").strip()
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "").strip()
TAVILY_MAX_CALLS_PER_RUN = int(os.getenv("TAVILY_MAX_CALLS_PER_RUN", "50"))

# ─── Selected audio device index (default: first WASAPI loopback) ─────────────
SELECTED_AUDIO_DEVICE_INDEX = None

# ─── Local music folder ───────────────────────────────────────────────────────
LOCAL_MUSIC_DEFAULT = ".\\local_music"
_local_music_raw = os.getenv("LOCAL_MUSIC_DIR", "").strip()
if _local_music_raw and not pathlib.Path(_local_music_raw).is_absolute():
    LOCAL_MUSIC_DIR = str(_resolve_path(_local_music_raw))
elif _local_music_raw:
    LOCAL_MUSIC_DIR = _local_music_raw
else:
    LOCAL_MUSIC_DIR = str(_resolve_path(LOCAL_MUSIC_DEFAULT))

# ─── Duration tolerance ───────────────────────────────────────────────────────
DURATION_TOLERANCE = 0.10  # 10%

# ─── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip()
LOG_FILE = HOME_DIR / "logs" / "app.log"


def setup_logging() -> None:
    """Configure RotatingFileHandler + console for playlist_arranger namespace."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger("playlist_arranger")
    root_logger.setLevel(logging.DEBUG)  # handlers filter individually

    # Console handler — respects LOG_LEVEL
    console = logging.StreamHandler()
    console.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    console.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    root_logger.addHandler(console)

    # Rotating file handler — always DEBUG for maximum detail
    file_handler = RotatingFileHandler(
        str(LOG_FILE), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s",
    ))
    root_logger.addHandler(file_handler)


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

    # LLM — settings.json values win if non-empty; .env provides fallback.
    # Use resolve_llm_settings() to merge the two sources.
    llm_backend: str = LLM_BACKEND
    ollama_model: str = OLLAMA_MODEL
    deepseek_model: str = DEEPSEEK_MODEL
    mistral_model: str = MISTRAL_MODEL

    # Audio
    selected_audio_device_index: int | None = SELECTED_AUDIO_DEVICE_INDEX

    # Anchor generation
    custom_anchor_prompt: str = ""


# ─── Fields whose settings.json values must fall back to .env when empty ────
_LLM_FIELDS = {"llm_backend", "ollama_model", "deepseek_model", "mistral_model"}

# .env-backed defaults per field — read once at startup, never change
_ENV_DEFAULTS = {
    "llm_backend": LLM_BACKEND,
    "ollama_model": OLLAMA_MODEL,
    "deepseek_model": DEEPSEEK_MODEL,
    "mistral_model": MISTRAL_MODEL,
}


def resolve_llm_settings(settings: Settings) -> Settings:
    """Apply .env→settings.json merge for LLM model fields.

    For each of the four LLM fields:
    - If settings.json has a non-empty value → use it (UI overrides .env)
    - If settings.json has an empty/None value → fall back to the .env constant

    Returns *settings* (mutated in-place) for convenience chaining.
    """
    for key in _LLM_FIELDS:
        val = getattr(settings, key, None)
        default = _ENV_DEFAULTS.get(key, "")
        if not val and default:
            setattr(settings, key, default)
    return settings


def load_settings() -> Settings:
    """Load settings from cache/settings.json, merging .env fallbacks for LLM fields.

    Path and LLM model fields use specific resolution rules:
    - LLM fields: settings.json wins if non-empty, otherwise .env acts as fallback.
    - Path fields: converted via _resolve_path.
    - All other fields: settings.json value as-is (may be empty/missing).
    """
    settings_path = CACHE_DIR_DEFAULT / "settings.json"
    if settings_path.exists():
        try:
            import json

            data = json.loads(settings_path.read_text(encoding="utf-8"))
            s = Settings()
            for key, val in data.items():
                if key in _LLM_FIELDS:
                    # Do NOT overwrite with empty string — resolve_llm_settings
                    # below will fill in the .env fallback for empty values.
                    if val and hasattr(s, key):
                        setattr(s, key, val)
                elif key in ("db_path", "embeds_dir", "cache_dir", "anchors_dir"):
                    val = _resolve_path(val)
                    if hasattr(s, key):
                        setattr(s, key, val)
                elif key == "local_music_dir":
                    if hasattr(s, key):
                        setattr(s, key, str(_resolve_path(val)))
                elif hasattr(s, key):
                    setattr(s, key, val)
            resolve_llm_settings(s)
            return s
        except Exception:
            pass
    return Settings()


def save_settings(settings: Settings) -> None:
    """Persist settings to cache/settings.json atomically.

    Paths are stored in portable form: ``.\\``-relative when under HOME_DIR,
    absolute otherwise.
    """
    from playlist_arranger.cache.store import atomic_write_json

    data = {}
    for k, v in settings.__dict__.items():
        if isinstance(v, pathlib.Path):
            data[k] = _to_portable(v)
        elif k == "local_music_dir" and v:
            data[k] = _to_portable(pathlib.Path(v))
        else:
            data[k] = v
    atomic_write_json(CACHE_DIR_DEFAULT / "settings.json", data)