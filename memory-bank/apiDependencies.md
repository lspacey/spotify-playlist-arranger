# API Dependencies

## External APIs

### Spotify Web API
| Detail | Value |
|---|---|
| **Base URL** | `https://api.spotify.com/v1/` |
| **Auth** | OAuth 2.0 (Authorization Code flow with PKCE) |
| **Client Library** | `spotipy==2.26.0` |
| **Direct HTTP** | `requests==2.34.2` (with custom retry logic) |
| **Scopes Used** | `user-read-currently-playing`, `user-read-playback-state`, `user-modify-playback-state`, `playlist-read-private`, `playlist-read-collaborative`, `playlist-modify-public`, `playlist-modify-private` |
| **Redirect URI** | `http://127.0.0.1:8888/callback` |
| **Rate Limit Handling** | Exponential backoff + `Retry-After` header parsing for 429s; retry 5xx errors |
| **Key Endpoints** | |
| `GET /me` | Current user profile |
| `GET /me/playlists` | User's playlists (paginated) |
| `GET /playlists/{id}/tracks` | Playlist track listing (paginated) |
| `PUT /me/player/play` | Start playback (via spotipy) |
| `GET /me/player` | Current playback state |
| `PUT /playlists/{id}/items` | Replace first 100 tracks |
| `POST /playlists/{id}/items` | Add tracks (chunks of 100) |
| `POST /me/playlists` | Create new playlist |

### DeepSeek API
| Detail | Value |
|---|---|
| **Base URL** | `https://api.deepseek.com` |
| **Auth** | API key (`DEEPSEEK_API_KEY` env var) |
| **Client Library** | `openai==2.44.0` (OpenAI-compatible client) |
| **Default Model** | `deepseek-v4-flash` (configurable via `DEEPSEEK_MODEL` env var) |
| **Usage** | Track description generation, AI anchor selection |
| **Integration** | `llm/client.py` — `_OpenAI(api_key=..., base_url="https://api.deepseek.com")` |

### Mistral API
| Detail | Value |
|---|---|
| **Base URL** | `https://api.mistral.ai/v1` |
| **Auth** | API key (`MISTRAL_API_KEY` env var) |
| **Client Library** | `openai==2.44.0` (OpenAI-compatible client) |
| **Default Model** | `mistral-large-latest` (configurable via `MISTRAL_MODEL` env var) |
| **Usage** | Track description generation, AI anchor selection |
| **Integration** | `llm/client.py` — `_OpenAI(api_key=..., base_url="https://api.mistral.ai/v1")` |

### HuggingFace Hub (Model Repository)
| Detail | Value |
|---|---|
| **URL** | `https://huggingface.co/m-a-p/MERT-v1-95M` |
| **Model** | MERT-v1-95M (Music undERstanding model with large-scale self-supervised Training) |
| **Client Library** | `transformers==4.38.0` |
| **Usage** | Neural audio embeddings (768-dim) for mood analysis and sorting distance |
| **Download** | One-time model download via `AutoModel.from_pretrained()` |
| **Integration** | `audio/mert.py` — `Wav2Vec2FeatureExtractor` + `AutoModel` |

## Python Dependencies (Pinned Versions)

All pinned in `requirements.txt`:

| Package | Version | Category | Purpose |
|---|---|---|---|
| `spotipy` | 2.26.0 | Spotify API | OAuth + Web API wrapper |
| `pyaudiowpatch` | 0.2.12.8 | Audio Capture | WASAPI loopback (Windows) |
| `librosa` | 0.11.0 | Audio Analysis | Feature extraction, BPM, key detection |
| `numpy` | 2.4.6 | Numerical | Array operations, embeddings |
| `soundfile` | 0.14.0 | Audio I/O | Audio file reading/writing |
| `rich` | 15.0.0 | Terminal UI | Console output formatting |
| `python-dotenv` | 1.2.2 | Config | `.env` file loading |
| `nicegui` | 3.13.0 | Web UI | Quasar/Vue-based web framework |
| `mutagen` | 1.48.1 | Audio Metadata | MP3/FLAC tag reading |
| `torch` | 2.12.1 | ML Runtime | Neural network engine (CPU/CUDA) |
| `transformers` | 4.38.0 | ML Models | MERT model loading (pinned for compat) |
| `ollama` | 0.6.2 | LLM (local) | Ollama Python client |
| `openai` | 2.44.0 | LLM (cloud) | DeepSeek + Mistral API client |
| `requests` | 2.34.2 | HTTP | Direct Spotify API calls |

### Transitive Dependencies (auto-installed)
| Package | Origin | Purpose |
|---|---|---|
| `torchaudio` | torch (matched pair) | Audio processing for PyTorch |
| `accelerate` | transformers | Model device placement |
| `tokenizers` | transformers | HuggingFace tokenizers |
| `huggingface-hub` | transformers | Model download from HF Hub |
| `fastapi` | nicegui | Web server framework |
| `uvicorn` | nicegui | ASGI server |
| `aiofiles` | nicegui | Async file operations |
| `watchfiles` | nicegui | Hot reload for development |

## Local LLM (Ollama)

| Detail | Value |
|---|---|
| **Default Model** | `gemma4:26b` (Google Gemma 4, 26B parameters) |
| **Default API Key** | `ollama` (placeholder, not used for auth) |
| **Integration** | `ollama==0.6.2` Python module — direct `ollama.chat()` calls |
| **Pre-flight Check** | `ollama.show(MODEL)` verifies model exists locally |
| **Setup** | `ollama pull gemma4:26b` |

## PyTorch CUDA Backends (Optional)

| GPU Series | CUDA Index | Torch Version |
|---|---|---|
| RTX 5080 / 50-series (Blackwell) | `https://download.pytorch.org/whl/cu130` | 2.12.1+cu130 |
| RTX 20/30/40 series | `https://download.pytorch.org/whl/cu121` | 2.12.1+cu121 |

## Data Persistence

| Storage | Format | Location |
|---|---|---|
| Track features | SQLite (WAL mode) | `database/tracks_db.sqlite` |
| MERT embeddings | NumPy `.npy` | `embeddings/{track_id}.npy` |
| App settings | JSON (atomic write) | `cache/settings.json` |
| Anchor plans | JSON (atomic write) | `anchors/anchors_{pl_id}.json` |
| Track descriptions | JSON (atomic write) | `anchors/descriptions_{pl_id}.json` |
| Playlist backups | JSON (atomic write) | `anchors/backup_{pl_id}.json` |
| Sorting results | JSON (atomic write) | `anchors/result_{pl_id}.json` |
| App logs | Rotating text files | `logs/app.log` |

## No Longer Used (Removed/Migrated)
- `tracks_db.json` — Replaced by SQLite (`database/migrate.py` handles migration)
- OpenAI API (direct) — Replaced by Ollama as default; DeepSeek/Mistral use OpenAI-compatible endpoints
- Rich console UI — Replaced by NiceGUI web interface