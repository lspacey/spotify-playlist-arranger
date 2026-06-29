"""Anchor editor page — manage anchor plan interactively."""

import threading

from nicegui import ui

from playlist_arranger.ui.state import (
    current_descs,
    current_anchor_plan,
    current_playlist_id,
    current_playlist_name,
)
from playlist_arranger.llm.prompts import PLAYLIST_STRUCTURES
from playlist_arranger.sorting.anchors import (
    _load_anchors_file,
    _save_anchors_file,
    _run_ai_anchor_generation,
    merge_adjacent_placeholders,
)


def build_anchor_editor():
    """Build anchor editor page."""
    # Load existing plan
    global current_anchor_plan
    if current_playlist_id and not current_anchor_plan:
        current_anchor_plan = _load_anchors_file(current_playlist_id) or []

    # Validate: keep only anchors in current playlist
    desc_ids = set(d["track_id"] for d in current_descs)
    valid_plan = []
    removed = 0
    for e in current_anchor_plan:
        if e["type"] != "anchor":
            valid_plan.append(e)
        elif e.get("track_id", "") in desc_ids:
            valid_plan.append(e)
        else:
            removed += 1
    current_anchor_plan[:] = valid_plan
    if removed:
        ui.notify(f"{removed} anchor(s) removed — no longer in playlist", type="warning")

    # Ensure plan is never fully empty
    if not current_anchor_plan:
        current_anchor_plan.append({"type": "placeholder"})

    container = ui.column().classes("w-full gap-4")

    with container:
        ui.label("Anchor Editor").classes("text-2xl font-bold mb-2")

        # Track list
        with ui.expansion("All Tracks (click to add)").classes("w-full"):
            desc_by_id = {d["track_id"]: d for d in current_descs}
            cols = [
                {"name": "idx", "label": "#", "field": "idx", "sortable": True},
                {"name": "name", "label": "Track", "field": "name"},
                {"name": "artist", "label": "Artist", "field": "artist"},
                {"name": "bpm", "label": "BPM", "field": "bpm"},
                {"name": "camelot", "label": "Key", "field": "camelot"},
                {"name": "desc", "label": "Description", "field": "desc"},
            ]
            rows = []
            for i, d in enumerate(current_descs, 1):
                rows.append({
                    "idx": i,
                    "name": d["name"][:30],
                    "artist": d["artist"][:16],
                    "bpm": f"{d.get('bpm', 0):.0f}",
                    "camelot": d.get("camelot", "?"),
                    "desc": d.get("description", "")[:58],
                })

            def on_track_select(e):
                rows_selected = e.args.get("rows", [{}])
                if rows_selected:
                    idx = rows_selected[0].get("idx", 0) - 1
                    if 0 <= idx < len(current_descs):
                        sid = current_descs[idx]["track_id"]
                        current_anchor_plan.append({"type": "anchor", "track_id": sid})
                        _save_anchors_file(current_playlist_id, current_playlist_name, current_anchor_plan)
                        ui.notify(f"Added: {current_descs[idx]['name']}", type="positive")
                        ui.run_javascript("location.reload()")

            track_table = ui.table(
                columns=cols, rows=rows, row_key="idx", pagination=25,
            ).classes("w-full")
            track_table.on("rowClick", on_track_select)

        # Current anchor plan
        with ui.card().classes("w-full"):
            ui.label("Current Anchor Plan").classes("text-lg font-bold mb-2")

            plan_container = ui.column().classes("w-full gap-1")
            refresh_plan = lambda: refresh_plan_view(plan_container)

            def refresh_plan_view(container):
                container.clear()
                desc_by_id = {d["track_id"]: d for d in current_descs}
                with container:
                    for i, e in enumerate(current_anchor_plan):
                        with ui.row().classes("w-full items-center gap-2 py-1 border-b"):
                            ui.label(f"{i+1}.").classes("font-bold w-8")
                            if e["type"] == "anchor":
                                sid = e.get("track_id", "")
                                d = desc_by_id.get(sid, {})
                                ui.label(f"⚓ {d.get('name','?')[:30]}").classes("flex-1")
                                ui.label(f"{d.get('artist','?')[:16]}").classes("text-sm text-gray-500")
                                ui.label(f"{d.get('bpm',0):.0f} BPM").classes("text-xs text-green-600")
                                ui.label(f"{d.get('camelot','?')}").classes("text-xs text-yellow-600")
                            else:
                                ui.label("── [ Placeholder ] ──").classes("flex-1 text-gray-400 italic")

                            # Action buttons
                            ui.button("×", on_click=lambda _, pos=i: remove_item(pos)).classes("text-xs px-1")
                            ui.button("↑", on_click=lambda _, pos=i: move_up(pos)).classes("text-xs px-1").bind_enabled_from(
                                globals(), "i", backward=lambda v: v > 0
                            )
                            ui.button("↓", on_click=lambda _, pos=i: move_down(pos)).classes("text-xs px-1").bind_enabled_from(
                                globals(), "i", backward=lambda v: v < len(current_anchor_plan) - 1
                            )

            refresh_plan()

        def remove_item(pos):
            del current_anchor_plan[pos]
            if not current_anchor_plan:
                current_anchor_plan.append({"type": "placeholder"})
            _save_anchors_file(current_playlist_id, current_playlist_name, current_anchor_plan)
            refresh_plan()

        def move_up(pos):
            if 1 <= pos < len(current_anchor_plan):
                current_anchor_plan[pos - 1], current_anchor_plan[pos] = (
                    current_anchor_plan[pos],
                    current_anchor_plan[pos - 1],
                )
                _save_anchors_file(current_playlist_id, current_playlist_name, current_anchor_plan)
                refresh_plan()

        def move_down(pos):
            if 0 <= pos < len(current_anchor_plan) - 1:
                current_anchor_plan[pos], current_anchor_plan[pos + 1] = (
                    current_anchor_plan[pos + 1],
                    current_anchor_plan[pos],
                )
                _save_anchors_file(current_playlist_id, current_playlist_name, current_anchor_plan)
                refresh_plan()

        # Controls row
        with ui.row().classes("w-full gap-2 mt-4"):
            ui.button(
                "+ Add Placeholder",
                on_click=lambda: add_placeholder(),
            ).classes("text-sm")

            # Structure type selector
            structure_options = {
                str(i): s["name"] for i, s in enumerate(PLAYLIST_STRUCTURES)
            }
            struct_select = ui.select(
                label="Structure", options=structure_options, value="0",
            ).classes("w-40")

            def generate_ai():
                struct_idx = int(struct_select.value or 0)
                run_ai_anchors(struct_idx)

            ui.button("Generate with AI", on_click=generate_ai, color="blue").classes("text-sm")

            ui.button(
                "Save Plan",
                on_click=save_plan,
                color="green",
            ).classes("text-sm")

        def add_placeholder():
            current_anchor_plan.append({"type": "placeholder"})
            _save_anchors_file(current_playlist_id, current_playlist_name, current_anchor_plan)
            refresh_plan()

        def save_plan():
            plan = merge_adjacent_placeholders(current_anchor_plan)
            current_anchor_plan[:] = plan
            _save_anchors_file(current_playlist_id, current_playlist_name, plan)
            ui.notify("Anchor plan saved!", type="positive")
            refresh_plan()

        async def run_ai_anchors(struct_idx):
            import asyncio
            progress = ui.column().classes("w-full mt-2")
            with progress:
                log = ui.log().classes("w-full h-32")
            try:
                plan = await asyncio.to_thread(
                    _run_ai_anchor_generation,
                    current_descs,
                    current_playlist_name,
                    current_playlist_id,
                    struct_idx,
                    progress_cb=lambda msg: log.push(msg),
                )
                if plan:
                    current_anchor_plan[:] = plan
                    _save_anchors_file(current_playlist_id, current_playlist_name, plan)
                    ui.notify("AI anchors generated!", type="positive")
                    ui.run_javascript("location.reload()")
                else:
                    ui.notify("AI generation returned no results", type="warning")
            except Exception as e:
                ui.notify(f"AI anchor generation failed: {e}", type="negative")

    return container