"""Anchor plan loading, saving, AI generation, and merging."""

import datetime
import json
import re

from playlist_arranger.config import ANCHORS_DIR_DEFAULT, LLM_BACKEND, OLLAMA_MODEL, DEEPSEEK_MODEL, MISTRAL_MODEL
from playlist_arranger.llm.prompts import ANCHOR_SYSTEM_PROMPT, PLAYLIST_STRUCTURES
from playlist_arranger.llm.client import llm_chat, _init_llm_client
from playlist_arranger.cache.store import atomic_write_json


def _load_anchors_file(playlist_id: str) -> list | None:
    """Load anchors from anchors/anchors_<pl_id>.json. Returns plan list or None."""
    af = ANCHORS_DIR_DEFAULT / f"anchors_{playlist_id}.json"
    if not af.exists():
        return None
    try:
        return json.loads(af.read_text(encoding="utf-8")).get("plan", None)
    except Exception:
        return None


def _save_anchors_file(playlist_id: str, playlist_name: str, plan: list):
    """Save anchors plan to anchors/anchors_<pl_id>.json."""
    af = ANCHORS_DIR_DEFAULT / f"anchors_{playlist_id}.json"
    data = {
        "playlist_id": playlist_id,
        "playlist_name": playlist_name,
        "saved_at": datetime.datetime.now().isoformat(),
        "plan": plan,
    }
    atomic_write_json(af, data)


def _plan_from_track_list(track_list: list, descs: list) -> list:
    """Convert a list of {"track_id":...} dicts into an editor plan."""
    plan = []
    for t in track_list:
        plan.append({"type": "anchor", "track_id": t["track_id"]})
    return plan


def _build_descriptions_block(descs: list) -> str:
    """Build a compact block of track descriptions for the LLM prompt."""
    lines = []
    for d in descs:
        sid = d.get("track_id", "")
        name = d.get("name", "?")
        art = d.get("artist", "?")
        desc = d.get("description", "")[:150]
        bpm = d.get("bpm", 0)
        key = d.get("key", "")
        loud = d.get("loudness_db", 0)
        harm = d.get("harm_ratio", 0)
        dyn = d.get("dynamic_range", 0)
        onset = d.get("onset_str", 0)
        bass = d.get("bass_pct", 0)
        feat = f"BPM={bpm:.0f} Key={key} Loud={loud:.1f}dB Harm={harm:.2f} Dyn={dyn:.1f}dB Onset={onset:.2f} Bass={bass:.1f}%"
        lines.append(f'  "{name}" — {art}  [{feat}]\n    {desc}')
    return "\n\n".join(lines)


def _parse_llm_anchor_list(raw_text: str, descs: list, expected_n: int) -> list:
    """Parse LLM response like 'ANCHORS:\n1. Name — Artist\n...' into track dicts."""
    section = re.search(r"ANCHORS\s*:(.*)", raw_text, flags=re.DOTALL | re.IGNORECASE)
    block = section.group(1).strip() if section else raw_text

    parsed = []
    for line in block.splitlines():
        m = re.match(r"\s*\d+\.\s*(.+?)\s*[-\u2014\u2013]\s*(.+)", line)
        if m:
            parsed.append({
                "name": m.group(1).strip().strip("\"' '"),
                "artist": m.group(2).strip().strip("\"' '"),
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


def _run_ai_anchor_generation(descs: list, pl_name: str, pl_id: str, structure_idx: int, progress_cb=None) -> list:
    """Ask LLM to generate anchors. Returns plan list."""
    def _log(msg):
        if progress_cb:
            progress_cb(msg)

    _init_llm_client()

    chosen = PLAYLIST_STRUCTURES[structure_idx]

    # Compute adaptive anchor count from percentage of total tracks
    n_anchors = max(3, int(len(descs) * chosen.get("anchor_pct", 20) / 100))
    _log(f"Structure: {chosen['name']} — {chosen['desc']}")
    _log(f"Target anchors: {n_anchors}  ({chosen.get('anchor_pct',20)}% of {len(descs)} tracks)")

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

    _log("Asking LLM to choose anchors...")
    raw_resp = ""
    try:
        raw_resp = llm_chat(
            ANCHOR_SYSTEM_PROMPT, user_msg,
            temperature=0.4, max_tokens=6000,
        )
    except Exception as e:
        _log(f"LLM error: {e}")
        return []

    matched = _parse_llm_anchor_list(raw_resp, descs, n_anchors)
    if not matched:
        _log("Could not parse anchors from LLM response")
        return []

    _log(f"LLM selected {len(matched)} anchors")
    plan = _plan_from_track_list(matched, descs)
    _save_anchors_file(pl_id, pl_name, plan)
    return plan


def merge_adjacent_placeholders(plan: list) -> list:
    """Collapses two or more consecutive placeholder entries into a single one."""
    if not plan:
        return plan
    result = []
    prev_was_placeholder = False
    for e in plan:
        if e["type"] == "placeholder":
            if not prev_was_placeholder:
                result.append(e)
                prev_was_placeholder = True
        else:
            result.append(e)
            prev_was_placeholder = False
    return result