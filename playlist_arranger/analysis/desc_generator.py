"""Background description-generation queue + worker thread (LLM wired).

Thread-safe FIFO queue of Spotify track IDs with a daemon worker thread
that generates track descriptions via LLM (fallback chain across backends).

Uses:
  - playlist_arranger.llm.client: llm_chat / _init_llm_client
  - playlist_arranger.database.db: get_track / save_track (read-modify-write)
  - config.Settings for backend/model fallback list
"""

import queue
import re
import threading
import time
import logging
import pathlib
from datetime import datetime, timezone

import numpy as np

from playlist_arranger import config
from playlist_arranger.database import db as _db
from playlist_arranger.llm.client import llm_chat, _init_llm_client

# ── Feature summarization helper (inlined from deleted llm/descriptions.py) ────


def _feat_summary(t: dict) -> str:
    """Build a human-readable summary of audio features for an LLM prompt."""
    f = t.get("features") or {}
    ss = t.get("start_seg") or {}
    es = t.get("end_seg") or {}
    lines = []
    if f.get("bpm"):
        lines.append(f"BPM: {f['bpm']:.1f}")
    if f.get("chroma_key") and f.get("mode"):
        cam = f.get("camelot", "")
        lines.append(
            f"Key: {f['chroma_key']} {f['mode']}"
            + (f" (Camelot {cam})" if cam else "")
        )
    if "rms_db" in f:
        lines.append(f"Loudness: {f['rms_db']:.1f} dBFS")
    if "dynamic_range" in f:
        lines.append(f"Dynamic range: {f['dynamic_range']:.1f} dB")
    if "harm_ratio" in f:
        lines.append(
            f"Harmonic ratio: {f['harm_ratio']:.2f}  (1=fully tonal, 0=percussive)"
        )
    if "flatness" in f:
        lines.append(
            f"Spectral flatness: {f['flatness']:.3f}  (0=tonal, 1=noise-like)"
        )
    if "bass" in f:
        lines.append(
            f"Freq balance — bass: {f['bass']*100:.1f}%,  "
            f"mid: {f.get('mid',0)*100:.1f}%,  high: {f.get('high',0)*100:.1f}%"
        )
    if "centroid_hz" in f:
        lines.append(f"Spectral centroid: {f['centroid_hz']:.0f} Hz")
    if "beat_reg" in f:
        lines.append(
            f"Beat regularity: {f['beat_reg']:.1f}  (higher = more regular)"
        )
    if "onset_str" in f:
        lines.append(f"Onset strength: {f['onset_str']:.2f}")
    if "tempo_complexity" in f:
        lines.append(f"Tempo complexity: {f['tempo_complexity']:.3f}")
    if ss.get("bpm") and es.get("bpm"):
        lines.append(f"BPM drift: start {ss['bpm']:.1f} → end {es['bpm']:.1f}")
    if ss.get("rms_db") and es.get("rms_db"):
        lines.append(
            f"Energy drift: start {ss['rms_db']:.1f} dBFS → end {es['rms_db']:.1f} dBFS"
        )
    return "\n".join(lines) if lines else "(no numerical features available)"

logger = logging.getLogger(__name__)

# ── Thread-safe FIFO queue + dedupe set ──────────────────────────────────────

_desc_queue: queue.Queue = queue.Queue()
_desc_queue_set: set[str] = set()           # track_ids currently in the queue
_desc_queue_lock = threading.Lock()          # protects _desc_queue_set + size reads

# ── Currently-processing globals (updated by worker thread, read by UI) ──────

desc_generator_current_track_id: str | None = None
desc_generator_current_track_name: str | None = None

# ── Worker thread control ────────────────────────────────────────────────────

_desc_worker_thread: threading.Thread | None = None
_desc_worker_started = False
_desc_worker_stop = threading.Event()
_desc_worker_start_lock = threading.Lock()   # idempotent start guard

# ── Callback for UI refresh after description generated ─────────────────────

_on_desc_generated_cb = None  # callable(track_id: str) or None


def set_on_desc_generated(cb):
    """Inject a callback to refresh UI after a description is generated."""
    global _on_desc_generated_cb
    _on_desc_generated_cb = cb


# ── Valence/Arousal helpers ──────────────────────────────────────────────────

def compute_valence_arousal(track_entry: dict, emb: np.ndarray | None = None) -> tuple[float, float]:
    """Heuristic valence (pleasantness) and arousal (intensity) from audio features.

    Based on the reference implementation. Returns (valence, arousal) in [-1, 1].
    Falls back gracefully if features are missing.
    """
    f = track_entry.get("features") or {}

    bpm = float(f.get("bpm", 120))
    rms_db = float(f.get("rms_db", -12))
    onset = float(f.get("onset_str", 0.5))
    centroid = float(f.get("centroid_hz", 2000))
    beat_reg = float(f.get("beat_reg", 0.5))
    harm_ratio = float(f.get("harm_ratio", 0.5))
    flatness = float(f.get("flatness", 0.2))
    bass_pct = float(f.get("bass", 0.33))
    mode = str(f.get("mode", ""))
    dynamic_range = float(f.get("dynamic_range", 6))

    # ── Arousal (energy/intensity) ───────────────────────────────────────
    # BPM contribution: 0..1 centered at 120
    arousal_bpm = min(1.0, max(0.0, (bpm - 60) / 180))

    # RMS contribution: -30 dB (quiet) -> 0, -3 dB (loud) -> 1
    arousal_rms = min(1.0, max(0.0, (rms_db + 30) / 27))

    # Onset strength: higher -> more percussive/energetic
    arousal_onset = min(1.0, onset * 2)

    # Spectral centroid: higher -> brighter/more intense
    arousal_centroid = min(1.0, max(0.0, (centroid - 500) / 3500))

    # Beat regularity: regular beat -> higher energy (danceability proxy)
    arousal_beat = min(1.0, max(0.0, beat_reg))

    # Embedding L2-norm: activation energy proxy (optional)
    arousal_emb = 0.5
    if emb is not None and emb.size > 0:
        l2 = float(np.linalg.norm(emb))
        arousal_emb = min(1.0, max(0.0, (l2 - 50) / 150))

    arousal_raw = (
        arousal_bpm * 0.20
        + arousal_rms * 0.25
        + arousal_onset * 0.20
        + arousal_centroid * 0.15
        + arousal_beat * 0.10
        + arousal_emb * 0.10
    )
    arousal = max(-1.0, min(1.0, arousal_raw * 2 - 1))

    # ── Valence (pleasantness/happiness) ──────────────────────────────────
    # Harmonic ratio: tonal -> more pleasant
    valence_harm = min(1.0, max(0.0, harm_ratio))

    # Flatness: lower (more tonal) -> more pleasant
    valence_flat = min(1.0, max(0.0, 1 - flatness * 4))

    # Mode: major -> positive valence bump
    valence_mode = 0.7 if mode == "major" else 0.3

    # Bass percentage: moderate bass -> pleasant, extreme -> neutral
    valence_bass = 1.0 - abs(bass_pct - 0.35) * 2
    valence_bass = min(1.0, max(0.0, valence_bass))

    # Dynamic range: wider dynamics -> more expressive/pleasant
    valence_dynamic = min(1.0, max(0.0, dynamic_range / 20))

    # BPM: moderate tempos ~110-130 are "pleasant", extremes less so
    valence_bpm = 1.0 - abs(bpm - 120) / 120
    valence_bpm = min(1.0, max(0.0, valence_bpm))

    valence_raw = (
        valence_harm * 0.25
        + valence_flat * 0.15
        + valence_mode * 0.20
        + valence_bass * 0.10
        + valence_dynamic * 0.15
        + valence_bpm * 0.15
    )
    valence = max(-1.0, min(1.0, valence_raw * 2 - 1))

    return valence, arousal


def va_quadrant(valence: float, arousal: float) -> str:
    """Map valence/arousal to quadrant label."""
    if arousal >= 0 and valence >= 0:
        return "High Energy / Positive (energetic & happy)"
    elif arousal >= 0 and valence < 0:
        return "High Energy / Negative (tense & aggressive)"
    elif arousal < 0 and valence >= 0:
        return "Low Energy / Positive (calm & content)"
    else:
        return "Low Energy / Negative (melancholic & subdued)"


def _va_mood_tags(valence: float, arousal: float) -> list[str]:
    """Generate mood tags based on valence/arousal coordinates."""
    tags = []
    if arousal > 0.3:
        tags.append("energetic")
    elif arousal < -0.3:
        tags.append("calm")
    if valence > 0.3:
        tags.append("uplifting")
        tags.append("bright")
    elif valence < -0.3:
        tags.append("dark")
        tags.append("tense")
    if arousal > 0.5:
        tags.append("intense")
    if valence < -0.5:
        tags.append("brooding")
    if arousal < -0.5:
        tags.append("ambient")
    if abs(valence) < 0.2 and abs(arousal) < 0.2:
        tags.append("neutral")
    return tags


def va_intensity_label(valence: float, arousal: float) -> str:
    """Human-readable one-liner from valence/arousal."""
    tags = _va_mood_tags(valence, arousal)
    if not tags:
        return "Mood: neutral, moderate energy"
    return "Mood: " + ", ".join(tags)


# ── LLM fallback chain ───────────────────────────────────────────────────────

from playlist_arranger.llm.prompts import DESCRIPTION_SYSTEM_PROMPT  # canonical base prompt (single source of truth)


def _get_llm_candidates(s) -> list:
    """Ordered, deduplicated list of (backend, model) pairs to try.

    Priority order (fixed, regardless of llm_backend setting):
      1. ollama_model (local, fastest — try first)
      2. The primary backend from llm_backend (if not already ollama)
      3. mistral_model (if not already listed)
      4. deepseek_model (if not already listed)

    Each backend/model pair appears only once even if multiple settings
    point to the same combination.
    """
    candidates = []  # type: list[tuple[str, str]]
    seen = set()     # type: set[tuple[str, str]]

    def _add(backend: str, model: str):
        if backend and model:
            key = (backend, model)
            if key not in seen:
                candidates.append(key)
                seen.add(key)

    # 1. Ollama always first (fastest path — local inference)
    _add("ollama", s.ollama_model)

    # 2. Primary backend from settings (if not already added)
    primary_backend = s.llm_backend
    primary_model = {
        "ollama": s.ollama_model,
        "deepseek": s.deepseek_model,
        "mistral": s.mistral_model,
    }.get(primary_backend, s.ollama_model)
    _add(primary_backend, primary_model)

    # 3. Mistral
    _add("mistral", s.mistral_model)

    # 4. DeepSeek
    _add("deepseek", s.deepseek_model)

    return candidates


# Pre-compiled regex for stripping think blocks from LLM responses
_THINK_RE = re.compile(r"", re.DOTALL)
_THINK_INNER_RE = re.compile(r"", re.DOTALL)


# ── Tavily web search helpers ─────────────────────────────────────────────────

_TAVILY_DEBUG_PATH = config.CACHE_DIR_DEFAULT / "tavily_search_debug.txt"
_tavily_call_count_this_run: int = 0
_tavily_cap_logged: bool = False
TAVILY_MIN_RELEVANCE_SCORE = 0.3  # skip results below this score threshold


def _artist_mentioned_in(artist: str, *texts: str) -> bool:
    """Check whether *artist* appears as a substring (case-insensitive) in any of *texts*.

    Normalises both sides by lowercasing and stripping to guard against
    whitespace / capitalisation differences.  Handles multi-word artist
    names (e.g. "Porcelain Toy") via straightforward substring match —
    fuzzy/stemmed matching is deliberately NOT used to avoid false
    positives.
    """
    needle = artist.strip().lower()
    if not needle:
        return False
    return any(needle in (t or "").strip().lower() for t in texts)


def _tavily_cap_reached() -> bool:
    """Check whether the per-run Tavily call cap has been hit.

    Returns False if TAVILY_MAX_CALLS_PER_RUN <= 0 (unlimited mode).
    Otherwise returns True once the counter reaches the configured cap.
    """
    cap = config.TAVILY_MAX_CALLS_PER_RUN
    if cap <= 0:
        return False  # 0 or negative = unlimited
    return _tavily_call_count_this_run >= cap


def _reset_tavily_call_counter() -> None:
    """Reset the per-run Tavily call counter.  Called once per description
    batch (i.e. each time the user clicks a bulk-generate button)."""
    global _tavily_call_count_this_run, _tavily_cap_logged
    _tavily_call_count_this_run = 0
    _tavily_cap_logged = False


def _search_track_context(track_name: str, artist: str) -> str | None:
    """Search Tavily for web context about a track (reviews, meaning, reception).

    Returns a concatenated string of relevant text snippets (up to ~800 chars),
    or None if no key is configured, search fails, or no results found.
    This is a nice-to-have enrichment — failures must never block description
    generation.
    """
    api_key = config.TAVILY_API_KEY
    if not api_key:
        return None

    # ── Per-run cap check (soft degradation, not a hard stop) ───────────
    global _tavily_call_count_this_run, _tavily_cap_logged
    if _tavily_cap_reached():
        if not _tavily_cap_logged:
            logger.info(
                "Tavily call cap (%d) reached — remaining tracks will use "
                "audio-only descriptions", config.TAVILY_MAX_CALLS_PER_RUN,
            )
            _tavily_cap_logged = True
        return None

    _tavily_call_count_this_run += 1

    query = f'"{artist}" "{track_name}" song meaning reception review'

    try:
        from tavily import TavilyClient  # noqa: F811 — optional dependency

        client = TavilyClient(api_key=api_key)
        response = client.search(
            query=query,
            search_depth="basic",
            max_results=5,
            include_answer=True,  # Union[bool, Literal['basic', 'advanced']] — True requests the synthesized answer
        )

        # Debug dump (best-effort, don't block on failure)
        _write_tavily_debug(track_name, artist, query, response)

        results = response.get("results") or []

        # ── Gather all result texts for artist-mention validation ────────
        result_texts: list[str] = []
        for r in results:
            for field in ("title", "content"):
                val = (r.get(field) or "").strip()
                if val:
                    result_texts.append(val)

        # ── Prefer the synthesized answer if available and non-empty ─────
        answer = (response.get("answer") or "").strip()
        if answer:
            # Layer 2: validate that the artist is mentioned SOMEWHERE in
            # the response (answer + all result texts).  If the artist is
            # absent, the answer is likely about a DIFFERENT song with the
            # same title — discard it.
            if not _artist_mentioned_in(artist, answer, *result_texts):
                logger.info(
                    "Tavily result for '%s' by %s does not mention the "
                    "artist — likely a title collision with an unrelated "
                    "song, discarding web context",
                    track_name, artist,
                )
                return None

            # Truncate synthesized answer to ~500 chars to keep prompt lean
            if len(answer) > 500:
                answer = answer[:497] + "..."
            logger.info(
                "Enriched description for %s with Tavily web context (%d chars)",
                track_name, len(answer),
            )
            return answer

        # ── Fall back to concatenating raw snippets ──────────────────────
        if not results:
            logger.debug("Tavily search for '%s' returned empty results", track_name)
            return None

        parts: list[str] = []
        total = 0
        MAX_TOTAL = 800
        filtered_out = 0
        for r in results:
            # Layer 3: skip low-relevance results
            score = float(r.get("score", 0))
            if score < TAVILY_MIN_RELEVANCE_SCORE:
                logger.debug(
                    "Skipping Tavily result (score %.2f < %.2f) for '%s'",
                    score, TAVILY_MIN_RELEVANCE_SCORE, track_name,
                )
                filtered_out += 1
                continue

            # Layer 2: also skip results that never mention the artist
            title = (r.get("title") or "").strip()
            content = (r.get("content") or "").strip()
            if not _artist_mentioned_in(artist, title, content):
                logger.debug(
                    "Skipping Tavily result (artist not mentioned) for '%s'",
                    track_name,
                )
                filtered_out += 1
                continue

            snippet = content
            if not snippet:
                continue
            if total + len(snippet) > MAX_TOTAL:
                remaining = MAX_TOTAL - total
                if remaining > 40:
                    snippet = snippet[:remaining] + "..."
                else:
                    break
            parts.append(snippet)
            total += len(snippet)

        if not parts:
            if filtered_out > 0:
                logger.info(
                    "All %d Tavily results filtered out (score < %.2f or "
                    "artist not mentioned) for '%s' — no usable web context",
                    filtered_out, TAVILY_MIN_RELEVANCE_SCORE, track_name,
                )
            else:
                logger.debug("Tavily search for '%s' returned no content text", track_name)
            return None

        combined = " | ".join(parts)
        logger.info(
            "Enriched description for %s with Tavily web context (%d chars)",
            track_name, len(combined),
        )
        return combined

    except Exception:
        logger.warning(
            "Tavily search failed for '%s' — continuing without web context",
            track_name, exc_info=True,
        )
        return None


def _write_tavily_debug(track_name: str, artist: str, query: str, response: dict) -> None:
    """Append raw Tavily response to debug file (best-effort, never raises)."""
    try:
        import json

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        header = f"{'='*70}\n{ts}  |  {track_name} — {artist}\nQuery: {query}\n"
        body = json.dumps(response, indent=2, ensure_ascii=False, default=str)
        entry = f"{header}\n{body}\n\n"

        _TAVILY_DEBUG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_TAVILY_DEBUG_PATH, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception:
        pass  # debug file is best-effort only


def _try_generate_description(track_name: str, artist: str, album: str,
                               feat_text: str, va_text: str,
                               web_context: str | None = None) -> str | None:
    """Try all LLM backend candidates in order. Returns description or None if all fail."""
    s = config.load_settings()
    candidates = _get_llm_candidates(s)

    if not candidates:
        logger.warning("No LLM backends configured — cannot generate description")
        return None

    for backend, model in candidates:
        logger.info("Trying LLM backend: %s / %s for track '%s'",
                     backend, model, track_name[:40])
        try:
            # Initialize client for this specific backend
            _init_llm_client(backend=backend, model_override=model)

            user_msg = (
                'Track: "' + track_name + '"\n'
                'Artist: ' + artist + '\n'
                'Album: ' + album + '\n\n'
                "Audio features:\n" + feat_text + "\n\n"
                + va_text
            )

            if web_context:
                user_msg += (
                    f"\n\nWeb-search context (verify before using):\n{web_context}\n\n"
                    "Use this ONLY for Part 2 (emotional/cultural significance). "
                    "If it doesn't clearly discuss this specific track, ignore it."
                )

            user_msg += "\n\nWrite a description of this track."

            raw = llm_chat(DESCRIPTION_SYSTEM_PROMPT, user_msg, temperature=0.7, max_tokens=2000)

            # Strip blocks if present
            cleaned = _THINK_RE.sub("", raw).strip()

            if not cleaned:
                # If think tag consumed everything, try extracting inner content
                m = _THINK_INNER_RE.search(raw)
                cleaned = m.group(1).strip() if m else raw

            if cleaned:
                logger.info("Generated description via %s/%s for '%s' (%d chars)",
                            backend, model, track_name[:40], len(cleaned))
                return cleaned
            else:
                logger.warning("Empty response from %s/%s — trying next candidate",
                               backend, model)
        except Exception as e:
            logger.exception("LLM call failed for %s/%s: %s", backend, model, e)

    return None


# ── Worker thread loop ───────────────────────────────────────────────────────

def _desc_worker_loop():
    """Worker thread loop: pulls one track ID, generates description, writes to DB."""
    global desc_generator_current_track_id, desc_generator_current_track_name

    logger.info("Description generator worker thread started")
    while not _desc_worker_stop.is_set():
        try:
            track_id = _desc_queue.get(timeout=1.0)
        except queue.Empty:
            continue

        # Remove from the dedupe set (it's been pulled from the queue)
        with _desc_queue_lock:
            _desc_queue_set.discard(track_id)

        # ── Look up track info ───────────────────────────────────────────
        artist = ""
        name = ""
        album = ""
        entry = None
        try:
            entry = _db.get_track(track_id)
            if isinstance(entry, dict):
                artist = entry.get("artist", "") or ""
                name = entry.get("name", "") or ""
                album = entry.get("album", "") or ""
        except Exception:
            logger.exception("DB lookup failed for track_id=%s", track_id[:8] if track_id else "?")

        # Also check state.current_tracks / state.analysis_queue as fallback
        if not name:
            try:
                from playlist_arranger.ui import state as _state
                for t in _state.current_tracks:
                    if t.get("id") == track_id:
                        name = t.get("name", "") or ""
                        artist = t.get("artist", "") or ""
                        album = t.get("album", "") or ""
                        break
                if not name:
                    for t in _state.analysis_queue:
                        if t.get("id") == track_id:
                            name = t.get("name", "") or ""
                            artist = t.get("artist", "") or ""
                            album = t.get("album", "") or ""
                            break
            except Exception:
                pass

        # Build display name
        if artist and name:
            display_name = f"{artist} - {name}"
        elif name:
            display_name = name
        else:
            display_name = track_id[:12] if track_id else "?"

        desc_generator_current_track_id = track_id
        desc_generator_current_track_name = display_name

        # ── Validation: track must be in DB ──────────────────────────────
        if entry is None:
            logger.warning(
                "Cannot generate description: track %s (%s) not in DB — skipping",
                track_id[:8] if track_id else "?", display_name[:40],
            )
            desc_generator_current_track_id = None
            desc_generator_current_track_name = None
            continue

        # ── Extract features ─────────────────────────────────────────────
        # (features dict used within compute_valence_arousal / _feat_summary)

        # ── Load MERT embedding (optional) ───────────────────────────────
        emb = None
        emb_file = entry.get("embedding_file")
        if emb_file:
            try:
                s = config.load_settings()
                emb_path = pathlib.Path(s.embeds_dir) / f"{track_id}.npy"
                if emb_path.exists():
                    emb = np.load(str(emb_path))
            except Exception:
                logger.debug("Could not load embedding for %s", track_id[:8] if track_id else "?")

        # ── Compute VA ──────────────────────────────────────────────────
        valence, arousal = compute_valence_arousal(entry, emb)
        quadrant = va_quadrant(valence, arousal)
        intensity = va_intensity_label(valence, arousal)
        va_text = (
            "Valence (pleasantness): " + f"{valence:+.2f}" + "  "
            "Arousal (intensity): " + f"{arousal:+.2f}" + "\n"
            "Quadrant: " + quadrant + "\n"
            + intensity
        )

        # ── Build feature summary ────────────────────────────────────────
        feat_text = _feat_summary(entry)

        # ── Query Tavily for web context (best-effort, never blocks) ────
        web_context = _search_track_context(name, artist)

        # ── Generate description (fallback chain + retry loop) ───────────
        description = None
        while not _desc_worker_stop.is_set():
            description = _try_generate_description(
                name, artist, album, feat_text, va_text,
                web_context=web_context,
            )
            if description is not None:
                break
            # All candidates failed — sleep 300s and retry
            logger.warning(
                "All LLM backends failed for '%s' — retrying in 300s",
                display_name[:40],
            )
            # Sleep with periodic checks: global stop signal AND
            # whether THIS track was removed from the queue (e.g. user
            # cleared only this one track, not a full queue clear).
            sleep_deadline = time.time() + 300
            while time.time() < sleep_deadline:
                if _desc_worker_stop.is_set():
                    break
                with _desc_queue_lock:
                    still_queued = track_id in _desc_queue_set
                if not still_queued:
                    logger.debug("Track %s removed from queue during retry sleep — aborting",
                                 track_id[:8] if track_id else "?")
                    break
                time.sleep(1.0)
            if _desc_worker_stop.is_set():
                break
            # Re-verify: was the track removed from the queue while we
            # were sleeping? If so, abort this item entirely.
            with _desc_queue_lock:
                still_queued = track_id in _desc_queue_set
            if not still_queued:
                logger.info("Track %s no longer in queue after retry sleep — discarding",
                            track_id[:8] if track_id else "?")
                description = None  # force discard below
                break

        if description is None:
            logger.warning("Worker stopped while processing '%s' — discarding", display_name[:40])
            desc_generator_current_track_id = None
            desc_generator_current_track_name = None
            continue

        # ── Write to DB (read-modify-write) ─────────────────────────────
        try:
            # Re-read entry to avoid race conditions
            fresh_entry = _db.get_track(track_id)
            if fresh_entry is None:
                logger.warning("Track %s disappeared from DB during generation — discarding", track_id[:8] if track_id else "?")
                desc_generator_current_track_id = None
                desc_generator_current_track_name = None
                continue

            from datetime import datetime, timezone
            fresh_entry["desc_text"] = description
            fresh_entry["desc_generated_at"] = datetime.now(timezone.utc).isoformat()
            _db.save_track(track_id, fresh_entry)
            logger.info("Saved description for '%s' (%d chars)", display_name[:40], len(description))
        except Exception:
            logger.exception("Failed to write description to DB for '%s'", display_name[:40])

        # ── Trigger UI refresh ──────────────────────────────────────────
        cb = _on_desc_generated_cb
        if cb:
            try:
                cb(track_id)
            except Exception:
                logger.exception("_on_desc_generated_cb failed for %s", track_id[:8] if track_id else "?")

        # ── Move to next item ────────────────────────────────────────────
        desc_generator_current_track_id = None
        desc_generator_current_track_name = None

    logger.info("Description generator worker thread stopped")


# ── Public API ───────────────────────────────────────────────────────────────

def desc_queue_add(track_id: str) -> bool:
    """Add a track ID to the description queue if not already present.

    Returns True if added, False if already queued (dedupe).
    Thread-safe under concurrent add/pop.
    """
    with _desc_queue_lock:
        if track_id in _desc_queue_set:
            return False
        _desc_queue_set.add(track_id)
        _desc_queue.put(track_id)
        return True


def desc_queue_add_many(track_ids: list) -> int:
    """Add multiple track IDs, skipping duplicates. Returns count actually added.

    Also resets the per-run Tavily call counter — every UI button click that
    feeds tracks into this function begins a fresh batch with a full quota.
    """
    _reset_tavily_call_counter()
    added = 0
    with _desc_queue_lock:
        for tid in track_ids:
            if tid and tid not in _desc_queue_set:
                _desc_queue_set.add(tid)
                _desc_queue.put(tid)
                added += 1
    return added


def desc_queue_clear():
    """Empty the queue and signal current in-progress item to stop cleanly.

    Drains all pending items.  The worker thread will finish its current
    item naturally (it was already dequeued) then stop processing new items.
    Does NOT forcefully kill the thread.
    """
    global desc_generator_current_track_id, desc_generator_current_track_name
    with _desc_queue_lock:
        _desc_queue_set.clear()
        while True:
            try:
                _desc_queue.get_nowait()
            except queue.Empty:
                break
    desc_generator_current_track_id = None
    desc_generator_current_track_name = None
    logger.info("Description queue cleared")


def desc_queue_size() -> int:
    """Current queue length (thread-safe read)."""
    with _desc_queue_lock:
        return len(_desc_queue_set)


def start_desc_generator():
    """Start the description generator worker thread. Idempotent."""
    global _desc_worker_thread, _desc_worker_started, _desc_worker_stop
    with _desc_worker_start_lock:
        if _desc_worker_started:
            return
        _desc_worker_started = True
        _desc_worker_stop.clear()
        _desc_worker_thread = threading.Thread(target=_desc_worker_loop, daemon=True)
        _desc_worker_thread.start()
        logger.info("Description generator worker started (idempotent)")


def stop_desc_generator():
    """Signal the worker thread to stop. Idempotent."""
    global _desc_worker_started
    _desc_worker_stop.set()
    _desc_worker_started = False
    logger.info("Description generator worker stop signalled")


__all__ = [
    "desc_queue_add",
    "desc_queue_add_many",
    "desc_queue_clear",
    "desc_queue_size",
    "desc_generator_current_track_id",
    "desc_generator_current_track_name",
    "start_desc_generator",
    "stop_desc_generator",
    "set_on_desc_generated",
    "compute_valence_arousal",
    "va_quadrant",
    "va_intensity_label",
    "_search_track_context",
    "_write_tavily_debug",
    "_try_generate_description",
    "_reset_tavily_call_counter",
]
