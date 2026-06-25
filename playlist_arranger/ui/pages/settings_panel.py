"""Settings panel — paths, algorithm parameters, audio device, LLM config, env vars."""

import os

from nicegui import ui

from playlist_arranger.ui.state import get_settings
from playlist_arranger.config import (
    save_settings,
    Settings,
    DB_PATH_DEFAULT,
    EMBEDS_DIR_DEFAULT,
    CACHE_DIR_DEFAULT,
    LLM_BACKEND,
    OLLAMA_MODEL,
    DEEPSEEK_MODEL,
    MISTRAL_MODEL,
)
from playlist_arranger.audio.capture import list_loopback_devices
from playlist_arranger.database.db import reload_db as _reload_db


def _mask_value(val: str, show_len: int = 4) -> str:
    """Mask a secret value showing only first and last N chars."""
    if not val or val in ("...", "ollama"):
        return val if val else "(not set)"
    if len(val) <= show_len * 2:
        return val[:show_len] + "..."
    return val[:show_len] + "..." + val[-show_len:]


def build_settings_dialog():
    """Build settings panel as a dialog content."""
    s = get_settings()
    container = ui.column().classes("w-full gap-4")

    with container:
        ui.label("Settings").classes("text-2xl font-bold mb-2")

        # ─── Environment variables ──────────────────────────────────────────
        with ui.card().classes("w-full"):
            ui.label("Environment Variables (.env)").classes("text-lg font-bold mb-2")
            env_vars = [
                ("SPOTIPY_CLIENT_ID", os.getenv("SPOTIPY_CLIENT_ID", "")),
                ("SPOTIPY_CLIENT_SECRET", os.getenv("SPOTIPY_CLIENT_SECRET", "")),
                ("SPOTIPY_REDIRECT_URI", os.getenv("SPOTIPY_REDIRECT_URI", "")),
                ("DEEPSEEK_API_KEY", os.getenv("DEEPSEEK_API_KEY", "")),
                ("MISTRAL_API_KEY", os.getenv("MISTRAL_API_KEY", "")),
            ]
            for name, val in env_vars:
                display = val if name == "SPOTIPY_REDIRECT_URI" else _mask_value(val)
                with ui.row().classes("w-full gap-2 items-center"):
                    ui.label(name).classes("text-sm font-mono w-48")
                    ui.label(display).classes("text-sm font-mono text-gray-600 dark:text-gray-400")

        # ─── Path settings ──────────────────────────────────────────────────
        with ui.card().classes("w-full"):
            ui.label("Paths").classes("text-lg font-bold mb-2")
            db_input = ui.input(
                label="Database file path",
                value=str(s.db_path),
            ).classes("w-full max-w-md")
            embeds_input = ui.input(
                label="Embeddings folder",
                value=str(s.embeds_dir),
            ).classes("w-full max-w-md")
            cache_input = ui.input(
                label="Cache folder",
                value=str(s.cache_dir),
            ).classes("w-full max-w-md")
            ui.button("Reset to Defaults", on_click=lambda: (
                db_input.set_value(str(DB_PATH_DEFAULT)),
                embeds_input.set_value(str(EMBEDS_DIR_DEFAULT)),
                cache_input.set_value(str(CACHE_DIR_DEFAULT)),
            )).classes("text-sm")

        # ─── SA algorithm parameters ────────────────────────────────────────
        with ui.card().classes("w-full"):
            ui.label("SA Algorithm Parameters").classes("text-lg font-bold mb-2")
            with ui.row().classes("w-full gap-4"):
                sa_iter = ui.number(
                    label="Iterations multiplier",
                    value=s.sa_iterations_multiplier, min=100, max=2000,
                ).classes("w-32")
                sa_runs = ui.number(
                    label="N_RUNS", value=s.sa_n_runs, min=10, max=500,
                ).classes("w-32")
                sa_T_start = ui.number(
                    label="T_start", value=s.sa_T_start, min=0.1, max=10.0, step=0.1,
                ).classes("w-32")
                sa_T_end = ui.number(
                    label="T_end", value=s.sa_T_end, min=1e-8, max=1e-2, step=1e-4,
                    format="%.0e",
                ).classes("w-40")

            ui.label("Weights (auto-normalized to sum=1 on save)").classes("text-md font-bold mt-4 mb-2")
            with ui.row().classes("w-full gap-4"):
                w_mood = ui.number(
                    label="Mood", value=s.w_mood, min=0.0, max=1.0, step=0.05,
                ).classes("w-24")
                w_bpm = ui.number(
                    label="BPM", value=s.w_bpm, min=0.0, max=1.0, step=0.05,
                ).classes("w-24")
                w_transition = ui.number(
                    label="Transition", value=s.w_transition, min=0.0, max=1.0, step=0.05,
                ).classes("w-24")
                w_key = ui.number(
                    label="Key", value=s.w_key, min=0.0, max=1.0, step=0.05,
                ).classes("w-24")
                w_energy = ui.number(
                    label="Energy", value=s.w_energy, min=0.0, max=1.0, step=0.05,
                ).classes("w-24")

        # ─── Audio device ───────────────────────────────────────────────────
        with ui.card().classes("w-full"):
            ui.label("Audio Device").classes("text-lg font-bold mb-2")
            devices = list_loopback_devices()
            device_options = {}
            for d in devices:
                label = f"[{'LOOP' if d['loopback'] else 'IN'}] {d['name'][:50]} ({d['channels']}ch @ {d['sr']}Hz)"
                device_options[str(d["index"])] = label
            device_select = ui.select(
                label="Audio input device",
                options=device_options,
                value=str(s.selected_audio_device_index) if s.selected_audio_device_index is not None else None,
            ).classes("w-full max-w-md")

        # ─── LLM settings ───────────────────────────────────────────────────
        with ui.card().classes("w-full"):
            ui.label("LLM Settings").classes("text-lg font-bold mb-2")
            backend_options = {
                "ollama": "Ollama (local)",
                "deepseek": "DeepSeek API",
                "mistral": "Mistral API",
            }
            llm_backend_select = ui.select(
                label="LLM Backend",
                options=backend_options,
                value=s.llm_backend,
            ).classes("w-full max-w-md")
            ollama_model_input = ui.input(
                label="Ollama Model", value=s.ollama_model,
            ).classes("w-full max-w-md")
            deepseek_model_input = ui.input(
                label="DeepSeek Model", value=s.deepseek_model,
            ).classes("w-full max-w-md")
            mistral_model_input = ui.input(
                label="Mistral Model", value=s.mistral_model,
            ).classes("w-full max-w-md")

        # ─── Save button ────────────────────────────────────────────────────
        async def do_save():
            import pathlib
            s_new = Settings()
            s_new.db_path = pathlib.Path(db_input.value)
            s_new.embeds_dir = pathlib.Path(embeds_input.value)
            s_new.cache_dir = pathlib.Path(cache_input.value)
            s_new.sa_iterations_multiplier = int(sa_iter.value)
            s_new.sa_n_runs = int(sa_runs.value)
            s_new.sa_T_start = float(sa_T_start.value)
            s_new.sa_T_end = float(sa_T_end.value)

            # Normalize weights so they sum to 1
            raw = {
                "mood": float(w_mood.value),
                "bpm": float(w_bpm.value),
                "transition": float(w_transition.value),
                "key": float(w_key.value),
                "energy": float(w_energy.value),
            }
            total = sum(raw.values())
            if total > 0:
                for k in raw:
                    raw[k] = round(raw[k] / total, 4)
            # Assign after normalization
            s_new.w_mood = raw["mood"]
            s_new.w_bpm = raw["bpm"]
            s_new.w_transition = raw["transition"]
            s_new.w_key = raw["key"]
            s_new.w_energy = raw["energy"]

            s_new.llm_backend = llm_backend_select.value
            s_new.ollama_model = ollama_model_input.value
            s_new.deepseek_model = deepseek_model_input.value
            s_new.mistral_model = mistral_model_input.value
            if device_select.value:
                s_new.selected_audio_device_index = int(device_select.value)

            # Ensure dirs exist
            s_new.embeds_dir.mkdir(exist_ok=True)
            s_new.cache_dir.mkdir(exist_ok=True)
            s_new.db_path.parent.mkdir(parents=True, exist_ok=True)

            save_settings(s_new)

            # Update runtime
            from playlist_arranger.ui import state
            state.settings = s_new

            # Reload DB connection with new path
            _reload_db()

            # Update WEIGHTS in config
            import playlist_arranger.config as _cfg
            _cfg.WEIGHTS["mood"] = s_new.w_mood
            _cfg.WEIGHTS["bpm"] = s_new.w_bpm
            _cfg.WEIGHTS["transition"] = s_new.w_transition
            _cfg.WEIGHTS["key"] = s_new.w_key
            _cfg.WEIGHTS["energy"] = s_new.w_energy

            ui.notify("Settings saved! (weights normalized)", type="positive")

        ui.button("Save Settings", on_click=do_save, color="green").classes("mt-4")

    return container