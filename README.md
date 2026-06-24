# 🎧 Spotify Playlist Arranger

> **Analyze your Spotify playlists, generate AI-powered track descriptions, and reorder tracks with a smart sorting algorithm to create the perfect listening journey.**

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Spotify](https://img.shields.io/badge/Spotify-Premium-1DB954.svg)](https://www.spotify.com/premium/)

---

## ✨ Features

- 🔊 **Audio Analysis** — Captures system audio via WASAPI loopback and extracts per‑track features (BPM, key, loudness, dynamic range, harmonic ratio, spectral flatness, frequency balance, onset strength)
- 🧠 **MERT Embeddings** — Generates 768‑dimensional neural embeddings with the [MERT‑v1‑95M](https://huggingface.co/m-a-p/MERT-v1-95M) music understanding model
- 🤖 **LLM‑Powered Descriptions** — Produces vivid, concise English descriptions of each track's sonic character using a local or cloud LLM (Ollama, DeepSeek, Mistral)
- 📐 **AI Anchor Selection** — An LLM‑based playlist architect picks anchor tracks and arranges them into a mood curve (Wave, Rise & Fall, Story Arc, and more)
- 🧮 **Smart Sorting** — Simulated‑annealing ATSP solver respects harmonic key mixing (Camelot wheel), tempo drift, and artist/album separation
- 🎚️ **Interactive Anchor Editor** — Manually add, delete, reorder anchors with placeholders; regenerate anchors with AI at any time
- 💾 **Full Backup & Recovery** — Backs up existing playlists before reordering; recoverable at any time via the main menu
- 🖥️ **Rich Terminal UI** — Beautiful tables, panels, and prompts powered by [Rich](https://github.com/Textualize/rich)

---

## 📋 Requirements

| Component | Requirement |
|-----------|-------------|
| **Spotify** | **Premium account** (required — the API does not support free accounts for playback control) |
| **OS** | Windows (WASAPI loopback capture) |
| **Python** | 3.11 or newer |
| **LLM** | One of: [Ollama](https://ollama.com) (local, free), [DeepSeek](https://platform.deepseek.com/) API, or [Mistral](https://mistral.ai/) API |

---

## 🚀 Installation

### 1. Clone the repository

```bash
git clone https://github.com/lspacey/spotify-playlist-arranger.git
cd spotify-playlist-arranger
```

### 2. Create a virtual environment

```bash
python -m venv venv
venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

> ⚠️ If you have an NVIDIA GPU and want CUDA acceleration, install PyTorch with the appropriate CUDA index. See the comments in `requirements.txt` for examples.

### 4. Set up your Spotify credentials

Rename `.env.example` to `.env` (or create a new `.env` file) and fill in your credentials:

```env
# Spotify Web API
SPOTIPY_CLIENT_ID=your_spotify_client_id
SPOTIPY_CLIENT_SECRET=your_spotify_client_secret
SPOTIPY_REDIRECT_URI=http://127.0.0.1:8888/callback

# LLM backend (ollama | deepseek | mistral)
LLM=ollama

# Ollama (local)
OLLAMA_MODEL=gemma4:26b
OLLAMA_API_KEY=ollama

# DeepSeek API
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_MODEL=deepseek-v4-flash

# Mistral API
MISTRAL_API_KEY=...
MISTRAL_MODEL=mistral-large-latest
```

> ℹ️ To obtain Spotify API credentials visit the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) and create a new app. The redirect URI **must** be set to `http://127.0.0.1:8888/callback` in both the `.env` file and the Spotify app settings.

---

## 🎮 Usage

### Quick start (Windows)

Double‑click **`run_playlist_analyzer.cmd`**, or run from the terminal:

```bash
venv\Scripts\activate
python playlist_analyzer.py
```

### Step‑by‑step guide

1. **Authenticate** — A browser window will open asking you to log into Spotify and grant permissions
2. **Select audio device** — Choose a WASAPI loopback device to capture system audio
3. **Select playback device** — Pick the Spotify device that will play tracks during analysis
4. **Choose a playlist** — Browse your owned playlists and select one
5. **Analyze missing tracks** — Tracks not in the local database are marked. Play them (Spotify will stream them automatically) while the analyzer captures the audio
6. **Choose anchors and reorder** — Once all tracks are analyzed:
   - LLM generates text descriptions of each track
   - Open the **Anchor Editor** to manually select waypoints, add placeholders, or let AI pick anchors
   - AI can arrange anchors into a mood curve (Wave, Rise & Fall, Story Arc, etc.)
7. **Run smart sorting** (`go` in the editor) — The SA‑ATSP solver arranges all non‑anchor tracks between the anchors
8. **Save to Spotify** — Update the existing playlist (with automatic backup), or create a new one

---

## 📁 File Structure

```
spotify-playlist-arranger/
├── playlist_analyzer.py          # Main application
├── run_playlist_analyzer.cmd     # Windows launcher
├── requirements.txt              # Python dependencies
├── .env                          # Environment variables (not committed)
├── README.md                     # This file
├── tracks_db.json                # Local track feature database
├── anchors/                      # Anchor plans & descriptions
│   ├── descriptions_<id>.json
│   ├── anchors_<id>.json
│   ├── result_<id>.json
│   └── backup_<id>.json
└── embeds/                       # MERT embedding vectors (.npy)
```

---

## 🔧 Configuration

| Environment Variable | Description | Default |
|----------------------|-------------|---------|
| `SPOTIPY_CLIENT_ID` | Spotify app client ID | *required* |
| `SPOTIPY_CLIENT_SECRET` | Spotify app client secret | *required* |
| `SPOTIPY_REDIRECT_URI` | OAuth redirect URI | `http://127.0.0.1:8888/callback` |
| `LLM` | LLM backend (`ollama`, `deepseek`, `mistral`) | `ollama` |
| `OLLAMA_MODEL` | Ollama model name | `gemma4:26b` |
| `DEEPSEEK_MODEL` | DeepSeek model name | `deepseek-v4-flash` |
| `DEEPSEEK_API_KEY` | DeepSeek API key | *required if LLM=deepseek* |
| `MISTRAL_MODEL` | Mistral model name | `mistral-large-latest` |
| `MISTRAL_API_KEY` | Mistral API key | *required if LLM=mistral* |

---

## ❓ FAQ

<details>
<summary><b>Why does it need Spotify Premium?</b></summary>

The script uses Spotify's playback control API to queue and play specific tracks for audio capture. This functionality is only available to Premium subscribers.
</details>

<details>
<summary><b>Can I use it without a GPU?</b></summary>

Yes. PyTorch installs in CPU‑only mode by default with `pip install torch`. MERT embeddings will work, just a bit slower.
</details>

<details>
<summary><b>What if a track isn't being captured?</b></summary>

Make sure the selected audio device is a **loopback** device (WASAPI). The script only captures audio that your system is playing — not audio from a different device.
</details>

<details>
<summary><b>How long does the sorting take?</b></summary>

The SA solver runs 100 iterations. For small playlists (~12 tracks) it takes seconds; for large playlists (200+ tracks) building the distance matrix may take a minute or two.
</details>

<details>
<summary><b>Can I edit AI‑generated anchors?</b></summary>

Absolutely. Use the Anchor Editor commands (`a`, `del`, `u`, `dn`) to add, remove, or reorder anchors manually. You can also add placeholders (`ph`) to leave gaps where the solver will fill in tracks.
</details>

---

## 📄 License

MIT — see [LICENSE](LICENSE) file.

---

<p align="center">
  Made with ☕ and 🎵 by <a href="https://github.com/lspacey">@lspacey</a>
</p>