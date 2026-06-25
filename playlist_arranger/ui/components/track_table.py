"""Reusable TrackTable component for displaying tracks."""

from nicegui import ui


class TrackTable:
    """Reusable NiceGUI track table component."""

    def __init__(
        self,
        tracks: list,
        show_add_anchor: bool = False,
        on_add_anchor=None,
    ):
        self.tracks = tracks
        self.show_add_anchor = show_add_anchor
        self.on_add_anchor = on_add_anchor
        self.container = None

    def render(self) -> ui.element:
        """Create and return the track table UI element."""
        with ui.column().classes("w-full") as self.container:
            columns = [
                {"name": "idx", "label": "#", "field": "idx", "sortable": True},
                {"name": "name", "label": "Track", "field": "name"},
                {"name": "artist", "label": "Artist", "field": "artist"},
                {"name": "album", "label": "Album", "field": "album"},
                {"name": "duration", "label": "Dur", "field": "duration"},
                {"name": "bpm", "label": "BPM", "field": "bpm"},
                {"name": "camelot", "label": "Key", "field": "camelot"},
                {"name": "status", "label": "Status", "field": "status"},
            ]
            if self.show_add_anchor:
                columns.append(
                    {"name": "action", "label": "", "field": "action"}
                )

            rows = []
            for i, t in enumerate(self.tracks, 1):
                dur_ms = t.get("duration_ms", 0)
                dur_str = (
                    f"{dur_ms // 60000}:{(dur_ms // 1000) % 60:02d}"
                    if dur_ms
                    else "?"
                )
                from playlist_arranger.ui.state import get_track_needs_analysis

                reason = get_track_needs_analysis(
                    t["id"], t.get("duration_ms")
                )
                if reason:
                    status = f"⚠ {reason}"
                else:
                    status = "✓ In DB"

                row = {
                    "idx": i,
                    "name": t["name"][:42],
                    "artist": t["artist"][:26],
                    "album": t.get("album", "")[:20],
                    "duration": dur_str,
                    "bpm": f"{t.get('bpm', 0):.0f}",
                    "camelot": t.get("camelot", "?"),
                    "status": status,
                }
                if self.show_add_anchor:
                    row["action"] = ""
                rows.append(row)

            table = ui.table(
                columns=columns,
                rows=rows,
                row_key="idx",
                pagination=50,
            ).classes("w-full")

            if self.show_add_anchor and self.on_add_anchor:
                def on_row_click(e):
                    row_idx = e.args.get("rowIndex", 0)
                    if row_idx < len(self.tracks):
                        self.on_add_anchor(row_idx)

                table.on("rowClick", on_row_click)

        return self.container