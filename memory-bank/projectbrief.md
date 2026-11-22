# Project Brief

## Project Name
**Spotify Playlist Arranger** (`spotify-playlist-arranger`)

## Repository
`https://github.com/lspacey/spotify-playlist-arranger.git`

## Core Purpose
A desktop application that analyzes Spotify playlists (and local audio files), generates AI-powered track descriptions using MERT neural embeddings, and intelligently reorders tracks using a simulated annealing ATSP solver to create the optimal listening journey.

## Key Requirements
1. **Spotify Premium integration** — Full playback control via Spotify Web API for audio capture
2. **Audio analysis** — WASAPI loopback capture on Windows to extract per-track features (BPM, key, loudness, dynamic range, harmonic ratio, spectral flatness, frequency balance, onset strength)
3. **MERT neural embeddings** — 768-dimensional embeddings via the MERT-v1-95M music understanding model from HuggingFace
4. **LLM-powered descriptions** — Concise English descriptions of sonic character via Ollama (local), DeepSeek API, or Mistral API
5. **AI anchor selection** — LLM-based playlist architect picks anchor tracks and arranges mood curves (Wave, Rise & Fall, Story Arc, etc.)
6. **Smart sorting** — Simulated annealing ATSP solver with harmonic key mixing (Camelot wheel), tempo drift, and artist/album separation penalties
7. **Interactive anchor editor** — Manual add/delete/reorder anchors with placeholders
8. **Backup & recovery** — Full playlist backup before reordering
9. **Web UI** — NiceGUI web interface with sidebar navigation
10. **Local file support** — Scan folders for MP3/FLAC files with M3U export
11. **SQLite database** — Persistent track feature storage

## Target Users
- Spotify Premium users who curate large playlists
- DJs and music enthusiasts seeking harmonically-mixed track orders
- Users who want AI-assisted playlist arrangement

## Platform
- **OS**: Windows (required for WASAPI loopback capture)
- **Python**: 3.11+
- **GPU**: Optional (NVIDIA CUDA support for MERT inference acceleration)