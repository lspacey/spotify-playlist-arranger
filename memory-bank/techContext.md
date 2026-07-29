# Technical Context

## Technologies Used

### Runtime
| Technology | Version | Purpose |
|---|---|---|
| Python | 3.11+ | Runtime language |
| NiceGUI | 3.13.0 | Web UI framework (Quasar/Vue-based) |
| SQLite | (stdlib) | Track feature database (WAL mode) |

### Audio Processing
| Technology | Version | Purpose |
|---|---|---|
| PyAudioWPatch | 0.2.12.8 | WASAPI loopback audio capture (Windows) |
| Librosa | 0.11.0 | Audio feature extraction (BPM, key, spectral features, MFCC) |
| NumPy | 2.4.6 | Numerical arrays for audio and embeddings |
| SoundFile | 0.14.0 | Audio file I/O |
| Mutagen | 1.48.1 | MP3/FLAC metadata reading (local files) |

### Machine Learning
| Technology | Version | Purpose |
|---|---|---|
| PyTorch | 2.12.1 | Neural network runtime (CPU or CUDA) |
| Transformers (HuggingFace) | 4.38.0 | MERT model loading (pinned < 4.44.0 for compatibility) |
| MERT-v1-95M | — | Music understanding model (768-dim embeddings) |

### LLM Backends
| Technology | Version | Purpose |
|---|---|---|
| Ollama | 0.6.2 | Local LLM inference (Gemma 4 26B default) |
| OpenAI (Python) | 2.44.0 | API client for DeepSeek and Mistral |

### Spotify API
| Technology | Version | Purpose |
|---|---|---|
| Spotipy | 2.26.0 | Spotify Web API wrapper |
| Requests | 2.34.2 | HTTP client (direct Spotify API calls with retry logic) |

### Utilities
| Technology | Version | Purpose |
|---|---|---|
| Rich | 15.0.0 | Terminal output formatting |
| python-dotenv | 1.2.2 | .env file loading |

### Testing
| Technology | Version | Purpose |
|---|---|---|
| pytest | 9.1.1 | Test framework |
| pytest-timeout | 2.4.0 | Hard timeout fail-safe for tests (prevents CI hangs from incomplete mocks) |

## Development Setup

### Prerequisites
1. Windows OS (required for WASAPI loopback)
2. Python 3.11+
3. Spotify Premium account
4. Spotify Developer App (Client ID + Secret)
5. LLM backend (one of):
   - Ollama installed locally with a model pulled
   - DeepSeek API key
   - Mistral API key

### Installation Steps
1. `git clone https://github.com/lspacey/spotify-playlist-arranger.git`
2. `python -m venv venv` + activate
3. `pip install -r requirements.txt`
4. For NVIDIA GPU (RTX 50-series): `pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu130`
5. For NVIDIA GPU (RTX 20/30/40-series): `pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121`
6. Copy `.env.example` to `.env` and fill in credentials
7. Run: `python -m playlist_arranger.main`

### Configuration (.env)
```
SPOTIPY_CLIENT_ID=...
SPOTIPY_CLIENT_SECRET=...
SPOTIPY_REDIRECT_URI=http://127.0.0.1:8888/callback
LLM=ollama|deepseek|mistral
OLLAMA_MODEL=gemma4:26b
DEEPSEEK_API_KEY=...
DEEPSEEK_MODEL=deepseek-v4-flash
MISTRAL_API_KEY=...
MISTRAL_MODEL=mistral-large-latest
```

## Technical Constraints

### Platform Constraints
- **Windows-only** — WASAPI loopback capture is Windows-specific
- **Spotify Premium required** — Playback control API is Premium-only
- **Python 3.11+** — Uses modern typing syntax

### Version Constraints
- **transformers < 4.44.0** — MERT-v1-95M breaks on newer versions (removed `conv_pos_batch_norm` from Wav2Vec2Config)
- **PyTorch + torchaudio must be a matched pair** — Installed from the same CUDA index
- **CUDA ≥ 12.8 required for RTX 50-series (Blackwell)** — `sm_120` architecture support

### Audio Constraints
- **Sample rate**: 44100 Hz (configurable via device)
- **MERT input**: 24000 Hz, max 30 seconds
- **Buffer**: 30-second rolling window, 10-minute max full buffer
- **Minimum audio**: 5 seconds required for analysis

### Performance
- MERT inference: ~1-3 seconds per track on GPU, ~5-15 seconds on CPU
- Distance matrix: O(n²) — ~40,000 pairs for 200 tracks
- SA sorting: 100 runs × max(n×500, 5000) iterations
- Spotify API rate limits handled with exponential backoff and Retry-After headers

## Dependencies

### Direct
All pinned in `requirements.txt`:
- spotipy, pyaudiowpatch, librosa, numpy, soundfile
- rich, python-dotenv, nicegui, mutagen
- torch, transformers, ollama, openai, requests

### External Services
- **Spotify Web API** — OAuth, playlist CRUD, playback control
- **HuggingFace Hub** — MERT-v1-95M model download (one-time)
- **DeepSeek API** — Optional LLM backend
- **Mistral API** — Optional LLM backend

### File System
```
playlist_arranger/          # Source package
database/
  tracks_db.sqlite          # SQLite track database
embeddings/                 # MERT embedding .npy files (per track ID)
cache/
  settings.json             # Persisted app settings
anchors/
  anchors_<pl_id>.json      # Anchor plans per playlist
  descriptions_<pl_id>.json # Track descriptions per playlist
  backup_<pl_id>.json       # Playlist backups
  result_<pl_id>.json       # Sorting results
logs/
  app.log                   # Rotating log (5MB × 3 backups)
local_music/                # Default local music folder
```

## Tool Usage Patterns

### Running the App
```bash
python -m playlist_arranger.main    # Starts on http://127.0.0.1:8082
# or double-click start.bat
```

### Ollama Setup
```bash
ollama pull gemma4:26b    # Default model
# Then verify: ollama show gemma4:26b
```

### GPU Verification
```bash
python -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
```

### Database Location
The SQLite database is at `database/tracks_db.sqlite` by default. Path is configurable via `cache/settings.json` → `db_path`.

## API Documentation

Detailed endpoint, authentication, and recent-change summaries for each external API:

| API | Documentation | Summary |
|---|---|---|
| Spotify / Spotipy | [Spotify Web API](https://developer.spotify.com/documentation/web-api) · [Spotipy](https://spotipy.readthedocs.io) | [docs/api/spotipy.md](../docs/api/spotipy.md) |
| DeepSeek | [api-docs.deepseek.com](https://api-docs.deepseek.com) | [docs/api/deepseek.md](../docs/api/deepseek.md) |
| Mistral AI | [docs.mistral.ai](https://docs.mistral.ai) | [docs/api/mistral.md](../docs/api/mistral.md) |
| HuggingFace Transformers | [huggingface.co/docs/transformers](https://huggingface.co/docs/transformers) | [docs/api/huggingface-transformers.md](../docs/api/huggingface-transformers.md) |
| Ollama (local LLM) | [docs.ollama.com](https://docs.ollama.com) | [docs/api/ollama.md](../docs/api/ollama.md) |
| NiceGUI | [nicegui.io](https://nicegui.io) | [docs/api/nicegui.md](../docs/api/nicegui.md) |
