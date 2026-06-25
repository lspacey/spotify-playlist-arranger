"""Database panel — track browser, detail view, maintenance."""

import json
import pathlib

from nicegui import ui

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
            ui.button("Refresh", on_click=lambda: count_label.set_text(f"Tracks: {_db.track_count()}")).classes("text-sm")

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
            label="Import from tracks_db.json",
            placeholder="path/to/tracks_db.json",
        ).classes("w-full max-w-md")

        async def do_import():
            path = json_path_input.value
            if not path:
                ui.notify("Enter a file path", type="warning")
                return
            jp = pathlib.Path(path)
            if not jp.exists():
                ui.notify("File not found", type="negative")
                return
            result = await ui.run.io_bound(migrate_from_json, jp)
            ui.notify(
                f"Imported {result['imported']} tracks, {result['skipped']} skipped, {result['errors']} errors",
                type="positive",
            )
            count_label.set_text(f"Tracks: {_db.track_count()}")

        ui.button("Import from JSON", on_click=do_import, color="secondary").classes("text-sm")

        # Track browser
        ui.separator()
        ui.label("Track Browser").classes("text-xl font-bold mb-2")

        search_input = ui.input(
            label="Search by name/artist", placeholder="Search...",
        ).classes("w-full max-w-md")

        track_container = ui.column().classes("w-full")

        def refresh_tracks():
            search = (search_input.value or "").lower().strip()
            all_data = _db.load_all()
            rows = []
            for tid, entry in all_data.items():
                name = (entry.get("name") or "").lower()
                artist = (entry.get("artist") or "").lower()
                if search and search not in name and search not in artist:
                    continue
                rows.append({
                    "id": tid,
                    "name": entry.get("name", "?")[:40],
                    "artist": entry.get("artist", "?")[:24],
                    "duration_ms": entry.get("duration_ms", 0),
                })

            track_container.clear()
            with track_container:
                ui.label(f"Showing {len(rows)} tracks").classes("text-sm text-gray-500 mb-2")
                cols = [
                    {"name": "name", "label": "Track", "field": "name"},
                    {"name": "artist", "label": "Artist", "field": "artist"},
                    {"name": "duration", "label": "Dur", "field": "duration"},
                ]
                table_rows = []
                for r in rows:
                    dur_ms = r["duration_ms"]
                    dur_str = f"{dur_ms//60000}:{(dur_ms//1000)%60:02d}" if dur_ms else "?"
                    table_rows.append({
                        "name": r["name"],
                        "artist": r["artist"],
                        "duration": dur_str,
                    })

                def on_row_click(e):
                    row_idx = e.args.get("rowIndex", 0)
                    if row_idx < len(rows):
                        show_track_detail(rows[row_idx]["id"])

                track_table = ui.table(
                    columns=cols, rows=table_rows, row_key="name", pagination=50,
                ).classes("w-full")
                track_table.on("rowClick", on_row_click)

        search_input.on("keydown.enter", lambda: refresh_tracks())
        ui.button("Search", on_click=refresh_tracks).classes("text-sm")

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

    return container