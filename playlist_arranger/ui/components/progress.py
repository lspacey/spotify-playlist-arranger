"""Progress panel component: spinner + scrollable log output."""

from nicegui import ui


class ProgressPanel:
    """Shows a spinner and scrollable log output for long-running operations."""

    def __init__(self, title: str = "Processing..."):
        self.title = title
        self.container = None
        self.spinner = None
        self.log_area = None
        self._log_lines = []

    def render(self) -> ui.element:
        """Create and return the progress panel UI element."""
        with ui.card().classes("w-full") as self.container:
            ui.label(self.title).classes("text-lg font-bold mb-2")
            self.spinner = ui.spinner(size="lg")
            self.log_area = ui.log().classes("w-full h-48 overflow-y-auto")
        return self.container

    def log(self, msg: str) -> None:
        """Append a log message."""
        if self.log_area:
            self.log_area.push(msg)
        self._log_lines.append(msg)

    def done(self, msg: str = "Done!") -> None:
        """Mark as complete."""
        if self.spinner:
            self.spinner.set_visibility(False)
        if self.log_area:
            self.log_area.push(f"[OK] {msg}")

    def error(self, msg: str) -> None:
        """Mark as error."""
        if self.spinner:
            self.spinner.set_visibility(False)
        if self.log_area:
            self.log_area.push(f"[ERROR] {msg}")