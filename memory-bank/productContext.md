# Product Context

## Why This Project Exists
Spotify's native playlist management lacks intelligent, sonically-aware reordering. Users who curate large playlists want tracks to flow naturally — considering tempo, key compatibility, energy, and mood progression. Manually reordering hundreds of tracks is tedious. This tool automates the process with audio analysis, neural embeddings, and AI.

## Problems It Solves
1. **Manual playlist ordering is time-consuming** — DJs spend hours arranging tracks by ear
2. **Harmonic mixing requires music theory knowledge** — Camelot wheel key compatibility is not intuitive
3. **No sonic understanding in existing tools** — Spotify doesn't analyze actual audio, only metadata
4. **No AI-assisted curation** — Existing tools can't suggest mood arcs or story arcs for playlists
5. **Local file playlists lack any ordering intelligence** — M3U files are just flat lists

## How It Works

### Overall Workflow
1. **Connect to Spotify** (OAuth) or browse **Local Files** — select a playlist
2. **Analyze missing tracks** — For Spotify: plays each track and captures system audio via WASAPI loopback. For local files: reads directly from disk.
3. **Extract audio features** — Librosa extracts BPM, key, loudness, dynamic range, harmonic ratio, spectral flatness, frequency balance, onset strength, MFCCs, chroma
4. **Generate MERT embeddings** — 768-dimensional neural embedding from MERT-v1-95M model (HuggingFace)
5. **Generate descriptions** — LLM creates 3-5 sentence English descriptions of each track's sonic character based on features + embedding statistics
6. **Create anchors** — Manually add anchor tracks and placeholders, or use AI generation with playlist structure types (Wave, Rise & Fall, Story Arc, etc.)
7. **Run smart sorting** — Simulated annealing ATSP solver arranges all tracks between the anchors using a weighted distance metric (mood 35%, transition 25%, BPM 15%, key 15%, energy 10%)
8. **Save to Spotify** (new playlist or update existing) or **Export as M3U** for local files

### Analysis Pipeline (Spotify tracks)
1. Play track on Spotify device
2. Capture system audio via WASAPI loopback (PyAudioWPatch)
3. Wait for track to finish (polling Spotify API for playback state)
4. Snapshot audio buffer
5. Trim silence (librosa)
6. Extract full-track features + start-of-track + end-of-track features (for transition analysis)
7. Compute MERT embedding
8. Save to SQLite database

### Analysis Pipeline (Local files)
1. Read MP3/FLAC from disk
2. Load audio with librosa (fast path, no playback)
3. Extract features + MERT embedding
4. Save to SQLite database

### Sorting Algorithm
- **Simulated Annealing ATSP** (Asymmetric Traveling Salesman Problem)
- Respects anchor constraints (fixed track positions)
- Supports placeholders (open slots where solver fills free tracks)
- 100 runs × adaptive iterations (max(n×500, 5000))
- Cost function = weighted combination of 7 components:
  - **Mood** (48%): cosine distance of MERT embeddings or chroma vectors
  - **Transition** (20%): MFCC cosine distance (per-playlist calibrated via `_robust_range()`)
  - **BPM** (12%): normalized BPM difference (`|diff|/200`)
  - **Key** (12%): Camelot wheel distance
  - **Texture** (10%): harmonic ratio + flatness + dynamic range + onset strength (per-playlist calibrated)
  - **Energy** (8%): RMS loudness difference (`|diff|/60`)
  - **Frequency balance** (8%): Euclidean distance of normalized bass/mid/high vectors
- **Per-playlist calibration**: `flatness`, `dynamic_range`, `onset_str`, and `transition` scales computed via `_robust_range()` on the playlist's actual feature distributions
- **Penalties**: +0.18 for same artist, +0.30 for same album consecutive tracks; duration mismatch penalty

### Playlist Structure Types (for AI anchor selection)
| Structure | Description |
|---|---|
| Flat | Uniform energy, steady, hypnotic |
| Rise and Fall | Gradual build-up to single peak, slow descent |
| Wave | Multiple crests and troughs |
| Pulse / Peaks | Alternating high/low energy blocks |
| Slow Burn / Crescendo | Minimal start, accumulating intensity |
| Rollercoaster | Frequent dynamic swings |
| Alternation / ABAB | Two contrasting moods trading places |
| Descending / Cooling | Heavy start, gradually unwinding |
| Ascension | Steady climb from dark to light |
| Story Arc | Introduction → development → climax → resolution |

## User Experience Goals
- **Simple onboarding** — Minimal configuration, guided OAuth flow
- **Visual feedback** — Progress indicators for analysis and sorting
- **Interactive control** — Manual anchor editing alongside AI suggestions
- **Safety first** — Automatic backups before any destructive reorder operation
- **Fast for small playlists** — Seconds for ~12 tracks
- **Responsive for large playlists** — Minutes for 200+ tracks (dominated by distance matrix construction)