"""Smart sorting page — SA parameters, run button, results display."""

import threading
import datetime

from nicegui import ui

from playlist_arranger.ui.state import (
    current_descs,
    current_anchor_plan,
    current_sorted_descs,
    current_playlist_id,
    current_playlist_name,
    current_playlist_source,
    sp,
)
from playlist_arranger.sorting.solver import _run_smart_sorting
from playlist_arranger.cache.store import save_result


def build_smart_sorting():
    """Build smart sorting page."""
    container = ui.column().classes("w-full gap-4")

    with container:
        ui.label("Smart Sorting").classes("text-2xl font-bold mb-2")

        # SA parameters
        with ui.card().classes("w-full"):
            ui.label("SA Parameters").classes("text-lg font-bold mb-2")
            with ui.row().classes("w-full gap-4"):
                iterations_input = ui.number(
                    label="Iterations multiplier",
                    value=500,
                    min=100,
                    max=2000,
                ).classes("w-32")
                n_runs_input = ui.number(
                    label="N_RUNS", value=100, min=10, max=500
                ).classes("w-32")
                T_start_input = ui.number(
                    label="T_start", value=1.0, min=0.1, max=10.0, step=0.1,
                ).classes("w-32")
                T_end_input = ui.number(
                    label="T_end", value=1e-4, min=1e-8, max=1e-2, step=1e-4,
                    format="%.0e",
                ).classes("w-40")

        # Progress area
        progress_container = ui.column().classes("w-full")

        # Run button
        def run_sorting():
            progress_container.clear()
            with progress_container:
                log = ui.log().classes("w-full h-48")

            def bg_task():
                try:
                    from playlist_arranger.database import db as _db
                    db_dict = _db.load_all()

                    def progress_cb(msg):
                        log.push(msg)

                    ordered, cost = _run_smart_sorting(
                        db_dict, current_descs,
                        current_playlist_id, current_playlist_name,
                        progress_cb=progress_cb,
                    )
                    current_sorted_descs[:] = ordered

                    # Save result
                    save_result(
                        current_playlist_id, current_playlist_name,
                        ordered, cost,
                    )
                    ui.timer(0.1, lambda: ui.notify(
                        f"Sorting complete! Cost: {cost:.4f}",
                        type="positive",
                    ), once=True)
                    ui.timer(0.2, lambda: ui.run_javascript("location.reload()"), once=True)
                except Exception as e:
                    ui.timer(0.1, lambda: ui.notify(f"Sorting failed: {e}", type="negative"), once=True)

            threading.Thread(target=bg_task, daemon=True).start()

        ui.button("Run Sorting", on_click=run_sorting, color="green").classes("mb-4")

        # Result display
        if current_sorted_descs:
            with ui.card().classes("w-full"):
                ui.label("Sorted Playlist").classes("text-lg font-bold mb-2")

                cols = [
                    {"name": "idx", "label": "#", "field": "idx", "sortable": True},
                    {"name": "name", "label": "Track", "field": "name"},
                    {"name": "artist", "label": "Artist", "field": "artist"},
                    {"name": "bpm", "label": "BPM", "field": "bpm"},
                    {"name": "camelot", "label": "Key", "field": "camelot"},
                    {"name": "action", "label": "", "field": "action"},
                ]
                rows = []
                for i, d in enumerate(current_sorted_descs, 1):
                    rows.append({
                        "idx": i,
                        "name": d["name"][:32],
                        "artist": d.get("artist", "")[:16],
                        "bpm": f"{d.get('bpm', 0):.0f}",
                        "camelot": d.get("camelot", "?"),
                        "action": "",
                    })

                sorted_table = ui.table(
                    columns=cols, rows=rows, row_key="idx", pagination=50,
                ).classes("w-full")

            # Save/Export section
            with ui.row().classes("w-full gap-2 mt-4"):
                if current_playlist_source == "spotify" and sp:
                    async def save_new_playlist():
                        from playlist_arranger.sources.spotify_source import create_playlist
                        uris = [
                            f"spotify:track:{d['track_id']}"
                            for d in current_sorted_descs
                            if d.get("track_id")
                        ]
                        ts_tag = datetime.datetime.now().strftime("%Y%m%d%H%M")
                        new_name = f"{current_playlist_name} {ts_tag}"
                        try:
                            new_pl, err = create_playlist(sp, new_name, uris)
                            if err:
                                ui.notify(f"Error: {err}", type="negative")
                            else:
                                url = (new_pl.get("external_urls") or {}).get("spotify", "")
                                ui.notify(f"Created: {new_name}", type="positive")
                                if url:
                                    ui.link(url, url).classes("text-sm")
                        except Exception as e:
                            ui.notify(f"Error: {e}", type="negative")

                    ui.button("Save as New Spotify Playlist", on_click=save_new_playlist, color="green")

                    async def update_playlist():
                        from playlist_arranger.sources.spotify_source import (
                            reorder_playlist,
                            get_playlist_tracks,
                        )
                        from playlist_arranger.cache.store import atomic_write_json
                        from playlist_arranger.config import ANCHORS_DIR_DEFAULT

                        # Backup existing
                        try:
                            existing = get_playlist_tracks(sp, current_playlist_id)
                        except Exception:
                            existing = []
                        bk_file = ANCHORS_DIR_DEFAULT / f"backup_{current_playlist_id}.json"
                        atomic_write_json(bk_file, {
                            "playlist_id": current_playlist_id,
                            "playlist_name": current_playlist_name,
                            "saved_at": datetime.datetime.now().isoformat(),
                            "tracks": existing,
                        })

                        uris = [
                            f"spotify:track:{d['track_id']}"
                            for d in current_sorted_descs
                            if d.get("track_id")
                        ]
                        ok, err = reorder_playlist(sp, current_playlist_id, uris)
                        if ok:
                            ui.notify("Playlist updated!", type="positive")
                        else:
                            ui.notify(f"Error: {err}", type="negative")

                    ui.button(
                        f"Update '{current_playlist_name}'",
                        on_click=update_playlist,
                        color="orange",
                    )

                elif current_playlist_source == "local":
                    m3u_input = ui.input(
                        label="Save as M3U path",
                        placeholder="playlist.m3u8",
                    ).classes("w-64")

                    async def save_m3u():
                        from playlist_arranger.sources.local_source import save_m3u as _save_m3u
                        import pathlib
                        paths = [
                            pathlib.Path(d.get("file_path", ""))
                            for d in current_sorted_descs
                            if d.get("file_path")
                        ]
                        m3u_path = pathlib.Path(m3u_input.value or "playlist.m3u8")
                        _save_m3u(m3u_path, paths)
                        ui.notify(f"Saved M3U: {m3u_path}", type="positive")

                    ui.button("Save M3U", on_click=save_m3u, color="blue")

            ui.button("Run Again", on_click=run_sorting, color="secondary").classes("mt-2")

    return container