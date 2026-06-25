"""Welcome/landing page for Playlist Arranger."""

from nicegui import ui


def build_welcome() -> ui.column:
    """Render introductory panel when no playlist source is selected."""
    with ui.column().classes("w-full items-center") as container:
        ui.label("🎧 Playlist Arranger").classes("text-3xl font-bold mt-8 mb-4")
        ui.label(
            "Analyze your playlists, generate AI-powered track descriptions, "
            "and reorder tracks with a smart sorting algorithm to create "
            "the perfect listening journey."
        ).classes("text-lg text-center max-w-2xl mb-8")

        with ui.card().classes("w-full max-w-2xl p-6"):
            ui.label("Features").classes("text-xl font-bold mb-4")
            features = [
                "🎵 Spotify and local file playlist support",
                "🔊 Audio capture and MERT neural embedding analysis",
                "🤖 LLM-powered track descriptions (Ollama / DeepSeek / Mistral)",
                "📐 AI anchor selection with 10 playlist structure types",
                "🧮 Simulated Annealing smart sorting",
                "💾 Export back to Spotify or local M3U playlist",
            ]
            for feat in features:
                ui.label(feat).classes("text-base mb-1")

        ui.label("Get started by selecting a source from the sidebar →").classes(
            "text-base text-gray-500 mt-6"
        )
    return container