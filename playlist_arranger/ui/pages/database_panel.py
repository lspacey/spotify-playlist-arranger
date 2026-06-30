"""Database panel — track browser, detail view, maintenance."""

import asyncio
import json

from nicegui import ui

from playlist_arranger.config import _resolve_path, load_settings
from playlist_arranger.database import db as _db
from playlist_arranger.database.migrate import migrate_from_json


def build_database_dialog():
    """Build database panel as a dialog content."""
    container = ui.column().classes("w-full gap-4")

    with container:
        ui.label("Database Manager").classes("text-2xl font-bold mb-2")

        # Track count and maintenance
        with ui.row().classes("w-full gap-2 items-center"):
            count_label = ui.label(f"Tracks: {_db.track_count()}").classes("text-lg")
            ui.button("Refresh", on_click=lambda: (count_label.set_text(f"Tracks: {_db.track_count()}"), refresh_tracks())).classes("text-sm")

            async def do_vacuum():
                _db.vacuum()
                ui.notify("VACUUM complete", type="positive")
                count_label.set_text(f"Tracks: {_db.track_count()}")

            ui.button("VACUUM", on_click=do_vacuum, color="warning").classes("text-sm")

            async def do_analyze():
                _db.analyze()
                ui.notify("ANALYZE complete", type="positive")

            ui.button("ANALYZE", on_click=do_analyze, color="info").classes("text-sm")

        # Import from JSON
        json_path_input = ui.input(
            label="Import from .\\database\\tracks_db.json",
            value=".\\database\\tracks_db.json",
        ).classes("w-full max-w-md")

        async def do_import():
            path = json_path_input.value
            if not path:
                ui.notify("Enter a file path", type="warning")
                return
            jp = _resolve_path(path)
            if not jp.exists():
                ui.notify(f"File not found: {jp}", type="negative")
                return
            result = await asyncio.to_thread(migrate_from_json, jp)
            ui.notify(
                f"Imported {result['imported']} tracks, {result['skipped']} skipped, {result['errors']} errors",
                type="positive",
            )
            count_label.set_text(f"Tracks: {_db.track_count()}")
            refresh_tracks()

        ui.button("Import from JSON", on_click=do_import, color="secondary").classes("text-sm")

        # Track browser
        ui.separator()
        ui.label("Track Browser").classes("text-xl font-bold mb-2")

        search_input = ui.input(
            label="Search by name/artist/track ID", placeholder="Search...",
        ).classes("w-full max-w-md")

        track_container = ui.column().classes("w-full")

        def refresh_tracks():
            search = (search_input.value or "").lower().strip()
            all_data = _db.load_all()
            rows = []
            for tid, entry in all_data.items():
                name = (entry.get("name") or "").lower()
                artist = (entry.get("artist") or "").lower()
                track_id_lower = tid.lower() if tid else ""
                if search and search not in name and search not in artist and search not in track_id_lower:
                    continue
                emb_file = entry.get("embedding_file")
                # Check if .npy file actually exists in the configured embeds_dir
                s = load_settings()
                emb_path = s.embeds_dir / f"{tid}.npy"
                emb_exists = emb_path.exists()
                rows.append({
                    "id": tid,
                    "name": entry.get("name", "?")[:40],
                    "artist": entry.get("artist", "?")[:40],
                    "duration_ms": entry.get("duration_ms", 0),
                    "emb_exists": emb_exists,
                })

            track_container.clear()
            with track_container:
                ui.label(f"Showing {len(rows)} tracks").classes("text-sm text-gray-500 mb-2")
                cols = [
                    {"name": "name", "label": "Track", "field": "name", "sortable": True},
                    {"name": "artist", "label": "Artist", "field": "artist", "sortable": True},
                    {"name": "duration", "label": "Dur", "field": "duration"},
                    {"name": "embedding", "label": "MERT", "field": "embedding", "sortable": True},
                    {"name": "track_id", "label": "Track ID", "field": "track_id"},
                ]
                table_rows = []
                for r in rows:
                    dur_ms = r["duration_ms"]
                    dur_str = f"{dur_ms//60000}:{(dur_ms//1000)%60:02d}" if dur_ms else "?"
                    emb_str = "✓" if r["emb_exists"] else "✗"
                    table_rows.append({
                        "id": r["id"],
                        "name": r["name"],
                        "artist": r["artist"],
                        "duration": dur_str,
                        "embedding": emb_str,
                        "track_id": r["id"][:16] + "…" if len(r["id"]) > 16 else r["id"],
                    })

                def on_row_click(e):
                    # NiceGUI table rowClick: e.args[0] is the clicked row dict
                    row_data = e.args[0]
                    if isinstance(row_data, dict) and "id" in row_data:
                        show_track_detail(row_data["id"])

                track_table = ui.table(
                    columns=cols, rows=table_rows, row_key="name", pagination=50,
                ).classes("w-full")
                track_table.on("rowClick", on_row_click)

        # Dynamic search: debounced refresh on every keystroke
        search_input.on("keyup", lambda _: ui.timer(0.05, refresh_tracks, once=True))
        search_input.on("keydown.enter", lambda: refresh_tracks())

        # Track detail section
        detail_container = ui.column().classes("w-full mt-4")

        def show_track_detail(tid):
            detail_container.clear()
            entry = _db.get_track(tid)
            if not entry:
                with detail_container:
                    ui.label("Track not found").classes("text-red-500")
                return
            with detail_container:
                ui.label(f"Track: {entry.get('name', '?')}").classes("text-lg font-bold")
                ui.label(f"Artist: {entry.get('artist', '?')}")
                ui.label(f"Album: {entry.get('album', '?')}")

                # Embedding status
                emb_file = entry.get("embedding_file")
                if emb_file:
                    ui.label(f"Embedding: {emb_file}").classes("text-sm text-green-600 dark:text-green-400")
                else:
                    ui.label("Embedding: not available").classes("text-sm text-orange-500 dark:text-orange-400")

                # JSON view
                with ui.expansion("Full Data (JSON)"):
                    ui.code(json.dumps(entry, ensure_ascii=False, indent=2), language="json").classes("w-full max-h-64 overflow-y-auto")

                with ui.row().classes("gap-2 mt-2"):
                    async def delete_track():
                        _db.delete_track(tid)
                        ui.notify("Track deleted", type="warning")
                        detail_container.clear()
                        refresh_tracks()
                        count_label.set_text(f"Tracks: {_db.track_count()}")

                    ui.button("Delete Track", on_click=delete_track, color="red").classes("text-sm")

        # Show all tracks immediately on open
        refresh_tracks()

    return container
