"""Generate LLM-powered track descriptions."""

import numpy as np

from playlist_arranger import config
from playlist_arranger.llm.client import llm_chat, _init_llm_client
from playlist_arranger.llm.prompts import DESCRIPTION_SYSTEM_PROMPT
from playlist_arranger.database import db as _db
from playlist_arranger.cache.store import (
    load_descriptions,
    save_descriptions,
)


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


def _emb_stats(emb: np.ndarray) -> str:
    """Return a textual summary of MERT embedding statistics for an LLM prompt."""
    v = (emb - emb.mean()) / (emb.std() + 1e-9)
    q = np.percentile(v, [10, 25, 50, 75, 90])
    return (
        f"MERT embedding (768-dim, normalized): "
        f"p10={q[0]:.2f}, p25={q[1]:.2f}, median={q[2]:.2f}, "
        f"p75={q[3]:.2f}, p90={q[4]:.2f}, raw_std={emb.std():.4f}"
    )


def generate_track_descriptions(tracks, pl_name, pl_id, progress_cb=None):
    """
    Generate text descriptions for all tracks in a playlist.
    Uses MERT embeddings + DB parameters + LLM.
    Saves intermediate results to cache.
    Returns the list of track description dicts.
    """
    def _log(msg):
        if progress_cb:
            progress_cb(msg)

    # Load existing descriptions
    descs = load_descriptions(pl_id) or []

    # Build set of spotify IDs already described
    existing_ids = set(
        d.get("track_id") or d.get("id") for d in descs if d
    )

    # Find tracks missing descriptions
    playlist_ids = set(t["id"] for t in tracks)
    missing_ids = playlist_ids - existing_ids

    if missing_ids:
        _log(f"Generating descriptions for {len(missing_ids)} new track(s)...")

        # Init LLM
        _init_llm_client()
        model_name = (
            config.OLLAMA_MODEL
            if config.LLM_BACKEND == "ollama"
            else config.DEEPSEEK_MODEL
            if config.LLM_BACKEND == "deepseek"
            else config.MISTRAL_MODEL
        )

        missing_list = [t for t in tracks if t["id"] in missing_ids]
        for idx, track in enumerate(missing_list, 1):
            tid = track["id"]
            _log(f"  [{idx}/{len(missing_list)}] {track['name']} — {track['artist']}")

            # Get DB entry
            db_entry = _db.get_track(tid)
            if not db_entry:
                descs.append(
                    {
                        "track_id": tid,
                        "name": track["name"],
                        "artist": track["artist"],
                        "album": track["album"],
                        "description": "(not in DB — analysis needed)",
                        "playlist": pl_name,
                    }
                )
                continue

            # Build feature summary
            feat_text = _feat_summary(db_entry)

            # Build MERT embedding hint
            emb_hint = ""
            ef = db_entry.get("embedding_file")
            if ef and (config.BASE_DIR / ef).exists():
                try:
                    emb = np.load(str(config.BASE_DIR / ef))
                    emb_hint = _emb_stats(emb)
                except Exception:
                    pass

            user_msg = (
                f'Track: "{track["name"]}"\n'
                f'Artist: {track["artist"]}\n'
                f'Album: {track["album"]}\n\n'
                f"Audio features:\n{feat_text}\n"
                + (f"\n{emb_hint}\n" if emb_hint else "")
                + "\nWrite a description of this track."
            )

            description = ""
            try:
                description = llm_chat(
                    DESCRIPTION_SYSTEM_PROMPT,
                    user_msg,
                    temperature=0.7,
                    max_tokens=2000,
                )
            except Exception as e:
                description = f"[Error: {e}]"

            descs.append(
                {
                    "track_id": tid,
                    "name": track["name"],
                    "artist": track["artist"],
                    "album": track["album"],
                    "description": description,
                    "playlist": pl_name,
                }
            )

        # Save after processing new tracks
        save_descriptions(pl_id, pl_name, descs, model_name)

    else:
        _log(
            f"All {len(tracks)} tracks already have descriptions"
        )

    # Verify all playlist tracks are in the list (reconcile)
    final_ids = set(
        d.get("track_id") or d.get("id") for d in descs if d
    )
    still_missing = playlist_ids - final_ids
    if still_missing:
        _log(
            f"{len(still_missing)} track(s) still missing descriptions "
            f"(likely not in DB). Run analysis first."
        )

    # ── Enrich each description entry with metrics from DB ──────
    enriched_any = False
    for d in descs:
        tid = d.get("track_id")
        if tid:
            entry = _db.get_track(tid)
            if entry:
                f = entry.get("features") or {}
                d["bpm"] = round(f.get("bpm", 0), 1)
                d["key"] = f"{f.get('chroma_key', '')} {f.get('mode', '')}".strip()
                d["camelot"] = f.get("camelot", "")
                d["loudness_db"] = round(f.get("rms_db", 0), 1)
                d["dynamic_range"] = round(f.get("dynamic_range", 0), 1)
                d["harm_ratio"] = round(f.get("harm_ratio", 0), 2)
                d["flatness"] = round(f.get("flatness", 0), 3)
                d["bass_pct"] = round(f.get("bass", 0) * 100, 1)
                d["mid_pct"] = round(f.get("mid", 0) * 100, 1)
                d["high_pct"] = round(f.get("high", 0) * 100, 1)
                d["onset_str"] = round(f.get("onset_str", 0), 2)
                d["duration_ms"] = entry.get("duration_ms", 0)
                enriched_any = True

    # Save after enrichment if any data changed
    if enriched_any and descs:
        model_name = (
            config.OLLAMA_MODEL
            if config.LLM_BACKEND == "ollama"
            else config.DEEPSEEK_MODEL
            if config.LLM_BACKEND == "deepseek"
            else config.MISTRAL_MODEL
        )
        save_descriptions(pl_id, pl_name, descs, model_name)

    return descs