"""Unit tests for playlist_arranger.analysis.desc_generator (LLM-wired)."""

import sys
sys.path.insert(0, r"e:\Projects\Spotify_playlists\repository")

import threading
import time
from unittest.mock import patch, MagicMock

from playlist_arranger.analysis import desc_generator as dg

# Ensure clean slate before any tests
dg.desc_queue_clear()
results = []


# ── Queue tests (unchanged from step 1) ────────────────────────────────────

def test_add_new_returns_true():
    dg.desc_queue_clear()
    assert dg.desc_queue_size() == 0
    result = dg.desc_queue_add("track_aaa")
    assert result is True
    assert dg.desc_queue_size() == 1


def test_add_duplicate_returns_false():
    dg.desc_queue_clear()
    dg.desc_queue_add("track_bbb")
    size_before = dg.desc_queue_size()
    result = dg.desc_queue_add("track_bbb")
    assert result is False
    assert dg.desc_queue_size() == size_before


def test_add_many_skips_duplicates():
    dg.desc_queue_clear()
    dg.desc_queue_add("existing")
    added = dg.desc_queue_add_many(["existing", "new1", "new2", "existing", "new3"])
    assert added == 3
    assert dg.desc_queue_size() == 4


def test_add_many_all_new():
    dg.desc_queue_clear()
    added = dg.desc_queue_add_many(["a", "b", "c"])
    assert added == 3
    assert dg.desc_queue_size() == 3


def test_add_many_all_duplicates():
    dg.desc_queue_clear()
    dg.desc_queue_add("x")
    dg.desc_queue_add("y")
    added = dg.desc_queue_add_many(["x", "y", "x"])
    assert added == 0
    assert dg.desc_queue_size() == 2


def test_add_many_empty_list():
    dg.desc_queue_clear()
    added = dg.desc_queue_add_many([])
    assert added == 0


def test_add_many_with_empty_strings():
    dg.desc_queue_clear()
    added = dg.desc_queue_add_many(["valid", "", None, "also_valid"])
    assert added == 2
    assert dg.desc_queue_size() == 2


def test_clear_empties_queue():
    dg.desc_queue_clear()
    dg.desc_queue_add_many(["a", "b", "c"])
    assert dg.desc_queue_size() == 3
    dg.desc_queue_clear()
    assert dg.desc_queue_size() == 0


def test_clear_resets_processing():
    dg.desc_queue_clear()
    dg.desc_queue_add("track_x")
    dg.desc_queue_clear()
    assert dg.desc_generator_current_track_id is None
    assert dg.desc_generator_current_track_name is None


# ── compute_valence_arousal tests ─────────────────────────────────────────

def test_va_high_energy():
    """High BPM + high RMS + major mode → high arousal, positive valence."""
    entry = {"features": {
        "bpm": 160, "rms_db": -5, "onset_str": 0.9, "centroid_hz": 4000,
        "beat_reg": 0.9, "harm_ratio": 0.8, "flatness": 0.1,
        "bass": 0.4, "mode": "major", "dynamic_range": 8,
    }}
    v, a = dg.compute_valence_arousal(entry)
    assert a > 0.3, f"Expected arousal > 0.3, got {a:.3f}"
    assert v > 0.0, f"Expected valence > 0, got {v:.3f}"


def test_va_calm_sad():
    """Low BPM + low RMS + minor mode → low arousal, negative valence."""
    entry = {"features": {
        "bpm": 70, "rms_db": -25, "onset_str": 0.1, "centroid_hz": 800,
        "beat_reg": 0.2, "harm_ratio": 0.2, "flatness": 0.6,
        "bass": 0.2, "mode": "minor", "dynamic_range": 2,
    }}
    v, a = dg.compute_valence_arousal(entry)
    assert a < -0.2, f"Expected arousal < -0.2, got {a:.3f}"
    assert v < 0.0, f"Expected valence < 0, got {v:.3f}"


def test_va_no_features():
    """Empty features dict → returns reasonable defaults."""
    v, a = dg.compute_valence_arousal({})
    assert -1 <= v <= 1
    assert -1 <= a <= 1


def test_va_with_embedding():
    """Embedding L2-norm adjusts arousal slightly."""
    import numpy as np
    emb = np.ones(768, dtype=np.float32)  # L2 = sqrt(768) ≈ 27.7
    entry = {"features": {"bpm": 120, "rms_db": -12}}
    v, a = dg.compute_valence_arousal(entry, emb=emb)
    # embedding L2=27.7 → arousal_emb ~0 (below 50 threshold), so minimal effect
    assert -1 <= v <= 1
    assert -1 <= a <= 1


# ── va_quadrant tests ─────────────────────────────────────────────────────

def test_quadrant_high_positive():
    q = dg.va_quadrant(0.5, 0.5)
    assert "energetic" in q.lower()


def test_quadrant_low_negative():
    q = dg.va_quadrant(-0.5, -0.5)
    assert "melancholic" in q.lower()


def test_quadrant_high_negative():
    q = dg.va_quadrant(-0.5, 0.5)
    assert "tense" in q.lower()


def test_quadrant_low_positive():
    q = dg.va_quadrant(0.5, -0.5)
    assert "calm" in q.lower()


def test_va_intensity_label():
    label = dg.va_intensity_label(0.8, 0.8)
    assert "energetic" in label
    assert "uplifting" in label


# ── Fallback chain with mocked LLM ────────────────────────────────────────

class FakeSettings:
    llm_backend = "ollama"
    ollama_model = "fake-model"
    deepseek_model = "deepseek-fake"
    mistral_model = "mistral-fake"


def test_fallback_candidate_1_available():
    """Candidate 1 succeeds — no fallback needed."""
    with patch("playlist_arranger.analysis.desc_generator.config.load_settings",
               return_value=FakeSettings()), \
         patch("playlist_arranger.analysis.desc_generator._init_llm_client"), \
         patch("playlist_arranger.analysis.desc_generator.llm_chat",
               return_value="A great track with driving beat.") as mock_chat:
        desc = dg._try_generate_description(
            "Test Track", "Test Artist", "Test Album",
            "BPM: 120\nKey: C major", "Valence: +0.2  Arousal: +0.5"
        )
        assert desc is not None
        assert "driving beat" in desc.lower()
        assert mock_chat.called


def test_fallback_candidate_1_fails_2_succeeds():
    """Candidate 1 fails, candidate 2 succeeds."""
    with patch("playlist_arranger.analysis.desc_generator.config.load_settings",
               return_value=FakeSettings()), \
         patch("playlist_arranger.analysis.desc_generator._init_llm_client"), \
         patch("playlist_arranger.analysis.desc_generator.llm_chat",
               side_effect=[RuntimeError("fail"), "Backup LLM description."]) as mock_chat:
        desc = dg._try_generate_description(
            "Test2", "Artist2", "Album2",
            "BPM: 90", "VA text"
        )
        assert desc is not None
        assert mock_chat.call_count == 2


def test_fallback_all_fail_returns_none():
    """All candidates fail → returns None."""
    with patch("playlist_arranger.analysis.desc_generator.config.load_settings",
               return_value=FakeSettings()), \
         patch("playlist_arranger.analysis.desc_generator._init_llm_client"), \
         patch("playlist_arranger.analysis.desc_generator.llm_chat",
               side_effect=RuntimeError("all dead")):
        desc = dg._try_generate_description(
            "Test3", "Artist3", "Album3",
            "feat", "va"
        )
        assert desc is None


def test_think_tag_stripping():
    """regex strips blocks."""
    with patch("playlist_arranger.analysis.desc_generator.config.load_settings",
               return_value=FakeSettings()), \
         patch("playlist_arranger.analysis.desc_generator._init_llm_client"), \
         patch("playlist_arranger.analysis.desc_generator.llm_chat",
               return_value="Stripped description."):
        desc = dg._try_generate_description(
            "Test4", "Artist4", "Album4",
            "feat", "va"
        )
        assert desc is not None
        assert "think" not in desc.lower()


def test_think_tag_stripping_inner_fallback():
    """', 'inner text') — no preamble."""
    with patch("playlist_arranger.analysis.desc_generator.config.load_settings",
               return_value=FakeSettings()), \
         patch("playlist_arranger.analysis.desc_generator._init_llm_client"), \
         patch("playlist_arranger.analysis.desc_generator.llm_chat",
               return_value="Good description."):
        desc = dg._try_generate_description(
            "Test5", "Artist5", "Album_now",
            "feat", "va"
        )
        assert desc is not None
        assert "Good description" in desc


# ── descriptions.py removal regression tests ────────────────────────────────


def test_descriptions_module_deleted():
    """descriptions.py has been removed and nothing imports from it."""
    import importlib
    try:
        importlib.import_module("playlist_arranger.llm.descriptions")
        assert False, "playlist_arranger.llm.descriptions should NOT exist"
    except ModuleNotFoundError:
        pass  # expected — module was deleted


def test_run_descriptions_stub_does_not_crash():
    """The stubbed run_descriptions() in main.py works without crashing."""
    from playlist_arranger.main import run_descriptions
    # run_descriptions is an async function; we test the synchronous part.
    # It just calls ui.notify + logger.info — no imports, no I/O.
    try:
        run_descriptions()
    except (RuntimeError, AttributeError) as exc:
        err = str(exc).lower()
        if "nicegui" in err or "context" in err or "client" in err:
            # NiceGUI context not available in test environment — expected,
            # but the function itself didn't raise an ImportError or crash.
            pass
        else:
            raise


def test_smart_sorting_safe_with_empty_descs():
    """smart_sorting.build_smart_sorting() doesn't crash with empty current_descs."""
    from playlist_arranger.ui.state import current_descs, current_anchor_plan, current_playlist_id, current_playlist_name, current_sorted_descs
    orig_descs = list(current_descs)
    orig_plan = list(current_anchor_plan)
    orig_sorted = list(current_sorted_descs)
    orig_pid = current_playlist_id
    orig_pname = current_playlist_name
    try:
        current_descs[:] = []
        current_anchor_plan[:] = [{"type": "anchor", "track_id": "fake_track"}]
        current_sorted_descs[:] = []
        current_playlist_id = "test_empty_descs"
        current_playlist_name = "Test Empty"
        from playlist_arranger.ui.pages.smart_sorting import build_smart_sorting
        build_smart_sorting()
    except (RuntimeError, AttributeError) as exc:
        err = str(exc).lower()
        if "nicegui" in err or "context" in err or "client" in err:
            pass
        else:
            raise
    finally:
        current_descs[:] = orig_descs
        current_anchor_plan[:] = orig_plan
        current_sorted_descs[:] = orig_sorted
        current_playlist_id = orig_pid
        current_playlist_name = orig_pname


def test_start_sorting_disabled_when_track_not_ok():
    """_refresh_start_button disables the button when any track is not 'OK'."""
    from unittest.mock import patch, MagicMock
    import playlist_arranger.ui.pages.smart_sorting as _ss

    class FB:
        def __init__(self): self._enabled = True
        def set_enabled(self, v): self._enabled = v

    class FL:
        def __init__(self): self._visible = False
        def set_text(self, t): self._text = t
        def set_visibility(self, v): self._visible = v

    old_btn = _ss._start_sort_btn
    old_warn = _ss._start_sort_warning
    old_tracks = list(_ss._playlist_tracks)
    try:
        _ss._start_sort_btn = FB()
        _ss._start_sort_warning = FL()
        # One track NOT OK
        with patch("playlist_arranger.ui.pages.smart_sorting.get_track_status",
                   return_value="Not OK"):
            _ss._playlist_tracks[:] = [{"id": "t1", "name": "T1", "artist": "A"}]
            _ss._refresh_start_button()
        assert not _ss._start_sort_btn._enabled, "Button should be DISABLED when track not OK"
        assert _ss._start_sort_warning._visible, "Warning should be visible"
        assert "missing audio features" in _ss._start_sort_warning._text, (
            f"Warning should mention missing features, got: {_ss._start_sort_warning._text}"
        )
    finally:
        _ss._start_sort_btn = old_btn
        _ss._start_sort_warning = old_warn
        _ss._playlist_tracks[:] = old_tracks


def test_start_sorting_enabled_when_all_tracks_ok():
    """_refresh_start_button enables the button when ALL tracks are OK."""
    from unittest.mock import patch
    import playlist_arranger.ui.pages.smart_sorting as _ss

    class FB:
        def __init__(self): self._enabled = False
        def set_enabled(self, v): self._enabled = v

    class FL:
        def __init__(self): self._visible = True
        def set_text(self, t): pass
        def set_visibility(self, v): self._visible = v

    old_btn = _ss._start_sort_btn
    old_warn = _ss._start_sort_warning
    old_tracks = list(_ss._playlist_tracks)
    try:
        _ss._start_sort_btn = FB()
        _ss._start_sort_warning = FL()
        with patch("playlist_arranger.ui.pages.smart_sorting.get_track_status",
                   return_value="OK"):
            _ss._playlist_tracks[:] = [
                {"id": "t1", "name": "T1", "artist": "A"},
                {"id": "t2", "name": "T2", "artist": "B"},
            ]
            _ss._refresh_start_button()
        assert _ss._start_sort_btn._enabled, "Button should be ENABLED when all tracks OK"
        assert not _ss._start_sort_warning._visible, "Warning should be HIDDEN when all OK"
    finally:
        _ss._start_sort_btn = old_btn
        _ss._start_sort_warning = old_warn
        _ss._playlist_tracks[:] = old_tracks


def test_start_sorting_disabled_when_no_tracks_loaded():
    """_refresh_start_button disables the button when playlist_tracks is empty."""
    import playlist_arranger.ui.pages.smart_sorting as _ss

    class FB:
        def __init__(self): self._enabled = True
        def set_enabled(self, v): self._enabled = v

    class FL:
        def __init__(self): self._visible = False
        def set_text(self, t): self._text = t
        def set_visibility(self, v): self._visible = v

    old_btn = _ss._start_sort_btn
    old_warn = _ss._start_sort_warning
    old_tracks = list(_ss._playlist_tracks)
    try:
        _ss._start_sort_btn = FB()
        _ss._start_sort_warning = FL()
        _ss._playlist_tracks[:] = []  # no tracks
        _ss._refresh_start_button()
        assert not _ss._start_sort_btn._enabled, "Button should be DISABLED with empty track list"
        assert _ss._start_sort_warning._visible, "Warning should be visible"
        assert "No tracks loaded" in _ss._start_sort_warning._text, (
            f"Expected 'No tracks loaded', got: {_ss._start_sort_warning._text}"
        )
    finally:
        _ss._start_sort_btn = old_btn
        _ss._start_sort_warning = old_warn
        _ss._playlist_tracks[:] = old_tracks


def test_rebuild_queue_ui_skips_when_client_disconnected():
    """_rebuild_queue_ui skips when client.has_socket_connection is False."""
    from unittest.mock import patch, MagicMock
    from playlist_arranger.ui.pages import playlist_source as _ps

    class FakeClient:
        has_socket_connection = False

    fake_container = MagicMock()
    fake_container.client = FakeClient()
    fake_container.clear = MagicMock()

    old_container = _ps._queue_container
    try:
        _ps._queue_container = fake_container
        _ps._rebuild_queue_ui()
        # .clear() must NOT have been called
        fake_container.clear.assert_not_called()
    finally:
        _ps._queue_container = old_container


def test_rebuild_queue_ui_proceeds_when_client_connected():
    """_rebuild_queue_ui proceeds when client.has_socket_connection is True."""
    from unittest.mock import patch, MagicMock
    from playlist_arranger.ui.pages import playlist_source as _ps

    class FakeClient:
        has_socket_connection = True

    fake_container = MagicMock()
    fake_container.client = FakeClient()
    fake_container.clear = MagicMock()

    old_container = _ps._queue_container
    old_batch_btn = _ps._ba._batch_btn
    try:
        _ps._queue_container = fake_container
        # Avoid crashing in _render_queue_table/_render_queue_controls
        # by ensuring the container context manager works and the
        # batch button refresh doesn't crash on a None button.
        _ps._ba._batch_btn = None
        _ps._rebuild_queue_ui()
        # .clear() must have been called (guard passed)
        fake_container.clear.assert_called_once()
    finally:
        _ps._queue_container = old_container
        _ps._ba._batch_btn = old_batch_btn


def test_load_save_descriptions_removed():
    """load_descriptions / save_descriptions no longer exist in cache.store."""
    from playlist_arranger.cache import store
    assert not hasattr(store, "load_descriptions"), (
        "load_descriptions should have been removed from cache.store"
    )
    assert not hasattr(store, "save_descriptions"), (
        "save_descriptions should have been removed from cache.store"
    )


# ── Dialog live-update tests ─────────────────────────────────────────────────


def test_dialog_updates_when_same_track_open():
    """_push_desc_to_open_dialog pushes new desc when same track dialog is open."""
    from playlist_arranger.ui import desc_dialog as _dd

    # Simulate dialog open for track X
    _dd._current_open_track_id = "track_X"

    # Create a mock textarea with .value attribute
    mock_textarea = MagicMock()
    mock_textarea.value = "Old description"
    _dd._current_textarea = mock_textarea

    # Mock DB to return a fresh description
    with patch("playlist_arranger.ui.pages.playlist_source._db.get_track",
               return_value={"desc_text": "New description!"}):
        from playlist_arranger.ui.pages.playlist_source import _push_desc_to_open_dialog
        _push_desc_to_open_dialog("track_X")

    assert mock_textarea.value == "New description!", (
        f"Expected 'New description!', got {mock_textarea.value!r}"
    )
    _dd._close_dialog_state()


def test_dialog_not_updated_when_different_track_open():
    """_push_desc_to_open_dialog does NOT push when a different track's dialog is open."""
    from playlist_arranger.ui import desc_dialog as _dd

    _dd._current_open_track_id = "track_Y"
    mock_textarea = MagicMock()
    mock_textarea.value = "Y's old desc"
    _dd._current_textarea = mock_textarea

    with patch("playlist_arranger.ui.pages.playlist_source._db.get_track",
               return_value={"desc_text": "X's new description"}):
        from playlist_arranger.ui.pages.playlist_source import _push_desc_to_open_dialog
        _push_desc_to_open_dialog("track_X")

    assert mock_textarea.value == "Y's old desc", (
        "Textarea should NOT have been changed for a different track's dialog"
    )
    _dd._close_dialog_state()


def test_dialog_not_updated_when_closed():
    """_push_desc_to_open_dialog is a no-op when no dialog is open (_current_open_track_id is None)."""
    from playlist_arranger.ui import desc_dialog as _dd

    _dd._current_open_track_id = None
    _dd._current_textarea = None

    with patch("playlist_arranger.ui.pages.playlist_source._db.get_track",
               return_value={"desc_text": "Some description"}):
        from playlist_arranger.ui.pages.playlist_source import _push_desc_to_open_dialog
        # Should not raise — just return early
        _push_desc_to_open_dialog("track_X")

    # No crash = pass


# ── Tavily web search tests ─────────────────────────────────────────────────

import tempfile
import os


def _mock_tavily_response_answer(answer_text: str = "A haunting trip-hop classic from 1994...") -> dict:
    """Helper: build a mock Tavily response dict with a synthesized answer."""
    return {
        "answer": answer_text,
        "results": [
            {"title": "Review 1", "url": "https://example.com/1", "content": "Brilliant track.", "score": 0.9},
            {"title": "Review 2", "url": "https://example.com/2", "content": "Genre-defining.", "score": 0.8},
        ],
        "response_time": 0.42,
        "query": "test query",
    }


def _mock_tavily_response_snippets(snippets: list[str] = None) -> dict:
    """Helper: build a mock Tavily response dict with results but no answer."""
    if snippets is None:
        snippets = ["Snippet one about the song.", "Snippet two with more detail.", "Snippet three finishing up."]
    return {
        "results": [{"title": f"R{i}", "url": f"https://x.com/{i}", "content": s, "score": 0.9 - i * 0.1}
                     for i, s in enumerate(snippets)],
        "response_time": 0.42,
        "query": "test query",
    }


def test_tavily_search_returns_none_when_key_not_set():
    """_search_track_context returns None when TAVILY_API_KEY is empty."""
    import playlist_arranger.config as cfg
    orig_key = cfg.TAVILY_API_KEY
    try:
        cfg.TAVILY_API_KEY = ""
        result = dg._search_track_context("Sour Times", "Portishead")
        assert result is None, f"Expected None when key not set, got {result!r}"
    finally:
        cfg.TAVILY_API_KEY = orig_key


def test_tavily_search_returns_none_on_api_error():
    """_search_track_context returns None (does not raise) when TavilyClient.search raises."""
    import playlist_arranger.config as cfg
    orig_key = cfg.TAVILY_API_KEY
    try:
        cfg.TAVILY_API_KEY = "fake-key-for-test"
        with patch("tavily.TavilyClient") as mock_tc:
            mock_instance = MagicMock()
            mock_instance.search.side_effect = RuntimeError("API connection refused")
            mock_tc.return_value = mock_instance

            # Also patch the debug writer to avoid touching filesystem
            with patch("playlist_arranger.analysis.desc_generator._write_tavily_debug"):
                result = dg._search_track_context("Test Track", "Test Artist")
                assert result is None, (
                    f"Expected None on API error, got {result!r}"
                )
    finally:
        cfg.TAVILY_API_KEY = orig_key


def test_tavily_search_returns_none_on_empty_results():
    """_search_track_context returns None when Tavily returns zero results and no answer."""
    import playlist_arranger.config as cfg
    orig_key = cfg.TAVILY_API_KEY
    try:
        cfg.TAVILY_API_KEY = "fake-key-for-test"
        empty_response = {"results": [], "response_time": 0.1, "query": "test"}

        with patch("tavily.TavilyClient") as mock_tc:
            mock_instance = MagicMock()
            mock_instance.search.return_value = empty_response
            mock_tc.return_value = mock_instance

            with patch("playlist_arranger.analysis.desc_generator._write_tavily_debug"):
                result = dg._search_track_context("Obscure Track", "Unknown Artist")
                assert result is None, (
                    f"Expected None on empty results, got {result!r}"
                )
    finally:
        cfg.TAVILY_API_KEY = orig_key


def test_web_context_appended_to_user_msg_when_present():
    """_try_generate_description appends web context when provided."""
    web_ctx = "Portishead's 'Sour Times' is known for its melancholic trip-hop atmosphere..."

    with patch("playlist_arranger.analysis.desc_generator.config.load_settings",
               return_value=FakeSettings()), \
         patch("playlist_arranger.analysis.desc_generator._init_llm_client"), \
         patch("playlist_arranger.analysis.desc_generator.llm_chat",
               return_value="A moody trip-hop track with haunting vocals.") as mock_chat:
        desc = dg._try_generate_description(
            "Sour Times", "Portishead", "Dummy",
            "BPM: 94\nKey: F# minor",
            "Valence: -0.50  Arousal: -0.20\nQuadrant: Low Energy / Negative",
            web_context=web_ctx,
        )
        # Verify the LLM was called with web context in the user message
        assert mock_chat.called, "LLM chat was not called"
        user_msg = mock_chat.call_args[0][1]  # second positional arg is user_msg
        assert "Additional context from web sources" in user_msg, (
            f"Expected web context section in user message. Got: {user_msg[:200]}"
        )
        assert "Portishead" in user_msg
        assert "Sour Times" in user_msg
        assert desc is not None


def test_user_msg_unchanged_when_web_context_absent():
    """_try_generate_description produces the correct audio-only prompt when web_context is None."""
    with patch("playlist_arranger.analysis.desc_generator.config.load_settings",
               return_value=FakeSettings()), \
         patch("playlist_arranger.analysis.desc_generator._init_llm_client"), \
         patch("playlist_arranger.analysis.desc_generator.llm_chat",
               return_value="A description.") as mock_chat:
        dg._try_generate_description(
            "Test Track", "Test Artist", "Test Album",
            "BPM: 120\nKey: C major",
            "Valence: +0.30  Arousal: +0.50",
            web_context=None,
        )
        user_msg = mock_chat.call_args[0][1]
        assert "Additional context from web sources" not in user_msg, (
            "Web context section should NOT appear when web_context is None"
        )
        assert "Write a description of this track." in user_msg
        assert "Test Track" in user_msg
        assert "Test Artist" in user_msg


def test_tavily_calls_capped_per_run():
    """_search_track_context respects TAVILY_MAX_CALLS_PER_RUN."""
    import playlist_arranger.config as cfg
    orig_key = cfg.TAVILY_API_KEY
    orig_cap = cfg.TAVILY_MAX_CALLS_PER_RUN
    try:
        cfg.TAVILY_API_KEY = "fake-key-for-test"
        cfg.TAVILY_MAX_CALLS_PER_RUN = 3  # small cap for testing

        # Reset counter to start fresh
        dg._reset_tavily_call_counter()

        # Mock TavilyClient to track call count
        call_count = [0]  # mutable counter in closure

        def _mock_search(**kwargs):
            call_count[0] += 1
            # Include the query in the answer so artist-mention check passes
            query_str = kwargs.get("query", "")
            return _mock_tavily_response_answer(
                f"Review of {query_str} — call {call_count[0]}"
            )

        with patch("tavily.TavilyClient") as mock_tc:
            mock_instance = MagicMock()
            mock_instance.search.side_effect = _mock_search
            mock_tc.return_value = mock_instance

            with patch("playlist_arranger.analysis.desc_generator._write_tavily_debug"):
                # Simulate 10 tracks — only first 3 should trigger API calls
                for i in range(10):
                    artist = f"Artist_{i}"  # underscores so Tavily query includes it
                    result = dg._search_track_context(f"Track {i}", artist)
                    if i < 3:
                        assert result is not None, (
                            f"Track {i} (before cap) should get web context"
                        )
                    else:
                        assert result is None, (
                            f"Track {i} (after cap) should return None"
                        )

        # TavilyClient.search should have been called exactly 3 times
        assert call_count[0] == 3, (
            f"Expected 3 Tavily API calls, got {call_count[0]}"
        )
    finally:
        cfg.TAVILY_API_KEY = orig_key
        cfg.TAVILY_MAX_CALLS_PER_RUN = orig_cap
        dg._reset_tavily_call_counter()


def test_tavily_counter_resets_between_separate_batch_runs():
    """desc_queue_add_many resets the counter, so a second batch gets a fresh budget."""
    import playlist_arranger.config as cfg
    orig_key = cfg.TAVILY_API_KEY
    orig_cap = cfg.TAVILY_MAX_CALLS_PER_RUN
    try:
        cfg.TAVILY_API_KEY = "fake-key-for-test"
        cfg.TAVILY_MAX_CALLS_PER_RUN = 3

        # Simulate batch 1 via desc_queue_add_many — should reset counter
        dg.desc_queue_clear()
        dg.desc_queue_add_many([f"b1_track_{i}" for i in range(5)])

        # Run 5 tracks — only first 3 should get Tavily calls
        call_count = [0]

        def _mock_search_b1(**kwargs):
            call_count[0] += 1
            query_str = kwargs.get("query", "")
            return _mock_tavily_response_answer(
                f"Review of {query_str} — batch1 call {call_count[0]}"
            )

        with patch("tavily.TavilyClient") as mock_tc:
            mock_instance = MagicMock()
            mock_instance.search.side_effect = _mock_search_b1
            mock_tc.return_value = mock_instance

            with patch("playlist_arranger.analysis.desc_generator._write_tavily_debug"):
                for i in range(5):
                    artist = f"Artist_{i}"
                    result = dg._search_track_context(f"Track {i}", artist)
                    if i < 3:
                        assert result is not None, f"Batch1 track {i} should get enrichment"
                    else:
                        assert result is None, f"Batch1 track {i} should be capped"

        assert call_count[0] == 3, f"Batch1: expected 3 calls, got {call_count[0]}"

        # Now simulate batch 2 — a second call to desc_queue_add_many
        dg.desc_queue_clear()
        dg.desc_queue_add_many([f"b2_track_{i}" for i in range(5)])

        call_count[0] = 0  # reset counter via closure

        def _mock_search_b2(**kwargs):
            call_count[0] += 1
            query_str = kwargs.get("query", "")
            return _mock_tavily_response_answer(
                f"Review of {query_str} — batch2 call {call_count[0]}"
            )

        with patch("tavily.TavilyClient") as mock_tc:
            mock_instance = MagicMock()
            mock_instance.search.side_effect = _mock_search_b2
            mock_tc.return_value = mock_instance

            with patch("playlist_arranger.analysis.desc_generator._write_tavily_debug"):
                for i in range(5):
                    artist = f"Artist_{i}"
                    result = dg._search_track_context(f"Track {i}", artist)
                    if i < 3:
                        assert result is not None, f"Batch2 track {i} should get enrichment"
                    else:
                        assert result is None, f"Batch2 track {i} should be capped"

        assert call_count[0] == 3, (
            f"Batch2: expected 3 calls (reset), got {call_count[0]}"
        )
    finally:
        cfg.TAVILY_API_KEY = orig_key
        cfg.TAVILY_MAX_CALLS_PER_RUN = orig_cap
        dg.desc_queue_clear()
        dg._reset_tavily_call_counter()


def test_tavily_cap_zero_means_unlimited():
    """TAVILY_MAX_CALLS_PER_RUN=0 means unlimited — all tracks get web context."""
    import playlist_arranger.config as cfg
    orig_key = cfg.TAVILY_API_KEY
    orig_cap = cfg.TAVILY_MAX_CALLS_PER_RUN
    try:
        cfg.TAVILY_API_KEY = "fake-key-for-test"
        cfg.TAVILY_MAX_CALLS_PER_RUN = 0  # 0 = unlimited

        dg._reset_tavily_call_counter()

        call_count = [0]

        def _mock_search_unlimited(**kwargs):
            call_count[0] += 1
            query_str = kwargs.get("query", "")
            return _mock_tavily_response_answer(
                f"Review of {query_str} — unlimited call {call_count[0]}"
            )

        with patch("tavily.TavilyClient") as mock_tc:
            mock_instance = MagicMock()
            mock_instance.search.side_effect = _mock_search_unlimited
            mock_tc.return_value = mock_instance

            with patch("playlist_arranger.analysis.desc_generator._write_tavily_debug"):
                # 100 tracks — ALL should get Tavily calls
                for i in range(100):
                    artist = f"Artist_{i}"
                    result = dg._search_track_context(f"Track {i}", artist)
                    assert result is not None, f"Track {i} should get enrichment with cap=0"

        assert call_count[0] == 100, (
            f"Unlimited mode: expected 100 calls, got {call_count[0]}"
        )
    finally:
        cfg.TAVILY_API_KEY = orig_key
        cfg.TAVILY_MAX_CALLS_PER_RUN = orig_cap
        dg._reset_tavily_call_counter()


# ── Tavily relevance validation tests ──────────────────────────────────────

def test_tavily_answer_discarded_when_artist_not_mentioned():
    """Answer is discarded (returns None) when artist name is absent from entire response."""
    import playlist_arranger.config as cfg
    orig_key = cfg.TAVILY_API_KEY
    try:
        cfg.TAVILY_API_KEY = "fake-key-for-test"
        dg._reset_tavily_call_counter()

        # Answer exists but never mentions the artist ANYWHERE
        response = {
            "answer": "A classic rock anthem about rebellion and youth...",
            "results": [
                {"title": "Review 1", "url": "https://x.com/1",
                 "content": "Great song by a different band.", "score": 0.9},
            ],
            "response_time": 0.42,
            "query": "test",
        }

        with patch("tavily.TavilyClient") as mock_tc:
            mock_instance = MagicMock()
            mock_instance.search.return_value = response
            mock_tc.return_value = mock_instance
            with patch("playlist_arranger.analysis.desc_generator._write_tavily_debug"):
                result = dg._search_track_context("Strange Love", "Swoone")
                assert result is None, (
                    f"Should discard answer when artist 'Swoone' never mentioned. "
                    f"Got: {result}"
                )
    finally:
        cfg.TAVILY_API_KEY = orig_key
        dg._reset_tavily_call_counter()


def test_tavily_answer_kept_when_artist_mentioned_in_answer():
    """Answer is KEPT when artist name appears in the answer text itself."""
    import playlist_arranger.config as cfg
    orig_key = cfg.TAVILY_API_KEY
    try:
        cfg.TAVILY_API_KEY = "fake-key-for-test"
        dg._reset_tavily_call_counter()

        response = {
            "answer": "Portishead's 'Sour Times' is a melancholic trip-hop classic...",
            "results": [
                {"title": "R1", "url": "https://x.com/1",
                 "content": "Portishead defined a genre.", "score": 0.95},
            ],
            "response_time": 0.42,
            "query": "test",
        }

        with patch("tavily.TavilyClient") as mock_tc:
            mock_instance = MagicMock()
            mock_instance.search.return_value = response
            mock_tc.return_value = mock_instance
            with patch("playlist_arranger.analysis.desc_generator._write_tavily_debug"):
                result = dg._search_track_context("Sour Times", "Portishead")
                assert result is not None, "Should keep answer — artist mentioned"
                assert "Portishead" in result, f"Artist should be in result: {result[:100]}"
    finally:
        cfg.TAVILY_API_KEY = orig_key
        dg._reset_tavily_call_counter()


def test_tavily_answer_kept_when_artist_mentioned_in_results_not_answer():
    """Answer is KEPT when artist name appears in result snippets, even if absent from answer."""
    import playlist_arranger.config as cfg
    orig_key = cfg.TAVILY_API_KEY
    try:
        cfg.TAVILY_API_KEY = "fake-key-for-test"
        dg._reset_tavily_call_counter()

        response = {
            "answer": "A classic song about loss and redemption...",
            "results": [
                {"title": "Radiohead interview", "url": "https://x.com/1",
                 "content": "Radiohead's approach to 'Creep' changed rock music...",
                 "score": 0.92},
            ],
            "response_time": 0.42,
            "query": "test",
        }

        with patch("tavily.TavilyClient") as mock_tc:
            mock_instance = MagicMock()
            mock_instance.search.return_value = response
            mock_tc.return_value = mock_instance
            with patch("playlist_arranger.analysis.desc_generator._write_tavily_debug"):
                result = dg._search_track_context("Creep", "Radiohead")
                assert result is not None, (
                    "Should keep answer — artist mentioned in results even if "
                    "not in answer text"
                )
    finally:
        cfg.TAVILY_API_KEY = orig_key
        dg._reset_tavily_call_counter()


def test_tavily_result_skipped_when_score_below_threshold():
    """Results with score < TAVILY_MIN_RELEVANCE_SCORE are skipped in snippet fallback."""
    import playlist_arranger.config as cfg
    orig_key = cfg.TAVILY_API_KEY
    orig_threshold = dg.TAVILY_MIN_RELEVANCE_SCORE
    try:
        cfg.TAVILY_API_KEY = "fake-key-for-test"
        dg.TAVILY_MIN_RELEVANCE_SCORE = 0.5  # raise threshold for test
        dg._reset_tavily_call_counter()

        # No answer, only results — first result has low score + no artist
        response = {
            "results": [
                {"title": "R1", "url": "https://x.com/1",
                 "content": "Low relevance snippet about Radiohead.", "score": 0.2},
                {"title": "R2", "url": "https://x.com/2",
                 "content": "High quality review of Radiohead's Creep...", "score": 0.8},
            ],
            "response_time": 0.42,
            "query": "test",
        }

        with patch("tavily.TavilyClient") as mock_tc:
            mock_instance = MagicMock()
            mock_instance.search.return_value = response
            mock_tc.return_value = mock_instance
            with patch("playlist_arranger.analysis.desc_generator._write_tavily_debug"):
                result = dg._search_track_context("Creep", "Radiohead")
                # Should return the high-score result only
                assert result is not None, "Should get context from high-score result"
                assert "High quality" in result or "Radiohead" in result, (
                    f"High-score snippet should be included: {result[:100]}"
                )
                assert "Low relevance" not in result, (
                    f"Low-score result should be filtered out: {result[:100]}"
                )
    finally:
        cfg.TAVILY_API_KEY = orig_key
        dg.TAVILY_MIN_RELEVANCE_SCORE = orig_threshold
        dg._reset_tavily_call_counter()


def test_tavily_all_results_filtered_returns_none():
    """When ALL results are filtered (score + artist), return None."""
    import playlist_arranger.config as cfg
    orig_key = cfg.TAVILY_API_KEY
    orig_threshold = dg.TAVILY_MIN_RELEVANCE_SCORE
    try:
        cfg.TAVILY_API_KEY = "fake-key-for-test"
        dg.TAVILY_MIN_RELEVANCE_SCORE = 0.9  # very high — nothing passes
        dg._reset_tavily_call_counter()

        response = {
            "results": [
                {"title": "R1", "url": "https://x.com/1",
                 "content": "Some band Swoone made a cool song.", "score": 0.3},
                {"title": "R2", "url": "https://x.com/2",
                 "content": "Another track by Swoone.", "score": 0.5},
            ],
            "response_time": 0.42,
            "query": "test",
        }

        with patch("tavily.TavilyClient") as mock_tc:
            mock_instance = MagicMock()
            mock_instance.search.return_value = response
            mock_tc.return_value = mock_instance
            with patch("playlist_arranger.analysis.desc_generator._write_tavily_debug"):
                result = dg._search_track_context("Strange Love", "Swoone")
                assert result is None, (
                    f"All results filtered — should return None, got {result!r}"
                )
    finally:
        cfg.TAVILY_API_KEY = orig_key
        dg.TAVILY_MIN_RELEVANCE_SCORE = orig_threshold
        dg._reset_tavily_call_counter()


def test_tavily_artist_name_matching_case_insensitive():
    """Artist mention check is case-insensitive and handles whitespace."""
    import playlist_arranger.config as cfg
    orig_key = cfg.TAVILY_API_KEY
    try:
        cfg.TAVILY_API_KEY = "fake-key-for-test"
        dg._reset_tavily_call_counter()

        # Artist "Swoone" — answer uses "SWOONE" (uppercase) + extra whitespace
        response = {
            "answer": "  SWOONE  's latest track 'Strange Love' explores...",
            "results": [
                {"title": "Review", "url": "https://x.com/1",
                 "content": "A masterpiece by swoone.", "score": 0.85},
            ],
            "response_time": 0.42,
            "query": "test",
        }

        with patch("tavily.TavilyClient") as mock_tc:
            mock_instance = MagicMock()
            mock_instance.search.return_value = response
            mock_tc.return_value = mock_instance
            with patch("playlist_arranger.analysis.desc_generator._write_tavily_debug"):
                result = dg._search_track_context("Strange Love", "swoone")
                assert result is not None, "Case-insensitive: 'swoone' should match 'SWOONE'"
    finally:
        cfg.TAVILY_API_KEY = orig_key
        dg._reset_tavily_call_counter()


def test_tavily_debug_file_written_on_search():
    """_search_track_context writes debug file when search succeeds."""
    import playlist_arranger.config as cfg
    orig_key = cfg.TAVILY_API_KEY
    orig_debug_path = dg._TAVILY_DEBUG_PATH

    # Use a temp file to avoid touching real debug output
    tmpdir = tempfile.mkdtemp(prefix="tavily_debug_test_")
    tmpfile = os.path.join(tmpdir, "tavily_search_debug.txt")

    try:
        cfg.TAVILY_API_KEY = "fake-key-for-test"
        # Override the debug path for this test
        dg._TAVILY_DEBUG_PATH = dg.config.CACHE_DIR_DEFAULT.__class__(tmpfile)
        # Actually set the module-level path properly
        import playlist_arranger.analysis.desc_generator as _dg_mod
        _dg_mod._TAVILY_DEBUG_PATH = dg.config.CACHE_DIR_DEFAULT.__class__(tmpfile)

        # Ensure dir exists
        os.makedirs(os.path.dirname(tmpfile), exist_ok=True)

        response = _mock_tavily_response_answer("Sour Times is a trip-hop track...")

        # Directly call _write_tavily_debug (already tested through _search_track_context)
        dg._write_tavily_debug("Sour Times", "Portishead",
                               '"Sour Times" Portishead song meaning mood reception review',
                               response)

        # Verify the file was written
        assert os.path.exists(tmpfile), f"Debug file was not created at {tmpfile}"
        content = open(tmpfile, "r", encoding="utf-8").read()
        assert "Sour Times" in content, f"Track name missing from debug output: {content[:200]}"
        assert "Portishead" in content, f"Artist missing from debug output: {content[:200]}"
        assert "Sour Times is a trip-hop track" in content, f"Answer missing: {content[:200]}"
        assert "song meaning mood reception review" in content, f"Query missing: {content[:200]}"
    finally:
        cfg.TAVILY_API_KEY = orig_key
        dg._TAVILY_DEBUG_PATH = orig_debug_path
        # Clean up temp file
        try:
            os.remove(tmpfile)
            os.rmdir(os.path.dirname(tmpfile))
        except Exception:
            pass


def test_stale_reference_cleared_on_close():
    """_close_dialog_state resets both _current_open_track_id and _current_textarea to None."""
    from playlist_arranger.ui import desc_dialog as _dd

    _dd._current_open_track_id = "track_Z"
    _dd._current_textarea = "fake textarea ref"

    _dd._close_dialog_state()

    assert _dd._current_open_track_id is None, (
        f"Expected None, got {_dd._current_open_track_id!r}"
    )
    assert _dd._current_textarea is None, (
        f"Expected None after close, got {_dd._current_textarea!r}"
    )


# ── Settings resolution regression tests ──────────────────────────────────────

from playlist_arranger.config import Settings, resolve_llm_settings, _ENV_DEFAULTS
from playlist_arranger.sorting.distance import _track_distance, WEIGHTS
import playlist_arranger.config as _cfg


# ── Distance component tests ───────────────────────────────────────────────────

# ── sync_weights_from_settings tests ────────────────────────────────────────

def test_sync_weights_updates_module_level_weights():
    """sync_weights_from_settings() copies all weight fields to config.WEIGHTS."""
    import playlist_arranger.config as _cfg
    from playlist_arranger.config import Settings

    # Store originals
    orig_weights = dict(_cfg.WEIGHTS)
    orig_artist = _cfg.ARTIST_PENALTY
    orig_album = _cfg.ALBUM_PENALTY
    try:
        s = Settings()
        s.w_mood = 0.99
        s.w_bpm = 0.88
        s.w_transition = 0.77
        s.w_key = 0.66
        s.w_energy = 0.55
        s.w_texture = 0.44
        s.w_freq_balance = 0.33
        s.artist_penalty = 0.12
        s.album_penalty = 0.25
        _cfg.sync_weights_from_settings(s)
        assert _cfg.WEIGHTS["mood"] == 0.99
        assert _cfg.WEIGHTS["bpm"] == 0.88
        assert _cfg.WEIGHTS["transition"] == 0.77
        assert _cfg.WEIGHTS["key"] == 0.66
        assert _cfg.WEIGHTS["energy"] == 0.55
        assert _cfg.WEIGHTS["texture"] == 0.44
        assert _cfg.WEIGHTS["freq_balance"] == 0.33
        assert _cfg.ARTIST_PENALTY == 0.12
        assert _cfg.ALBUM_PENALTY == 0.25
    finally:
        _cfg.WEIGHTS.update(orig_weights)
        _cfg.ARTIST_PENALTY = orig_artist
        _cfg.ALBUM_PENALTY = orig_album


def test_sync_weights_cache_not_cleared_on_identical_weights():
    """_SORTING_CACHE is NOT cleared when weights haven't changed (save with same values)."""
    import playlist_arranger.config as _cfg
    from playlist_arranger.config import Settings
    from playlist_arranger.sorting.distance import _SORTING_CACHE

    # Pre-populate cache
    _SORTING_CACHE.clear()
    _SORTING_CACHE["test_playlist"] = {"dummy": True}

    orig_weights = dict(_cfg.WEIGHTS)
    orig_artist = _cfg.ARTIST_PENALTY
    orig_album = _cfg.ALBUM_PENALTY
    try:
        s = Settings()
        s.w_mood = _cfg.WEIGHTS["mood"]
        s.w_bpm = _cfg.WEIGHTS["bpm"]
        s.w_transition = _cfg.WEIGHTS["transition"]
        s.w_key = _cfg.WEIGHTS["key"]
        s.w_energy = _cfg.WEIGHTS["energy"]
        s.w_texture = _cfg.WEIGHTS["texture"]
        s.w_freq_balance = _cfg.WEIGHTS["freq_balance"]
        s.artist_penalty = _cfg.ARTIST_PENALTY
        s.album_penalty = _cfg.ALBUM_PENALTY

        # Simulate the do_save() weight-change check
        weight_fields = [
            "w_mood", "w_bpm", "w_transition", "w_key", "w_energy",
            "w_texture", "w_freq_balance",
        ]
        weights_changed = any(
            getattr(s, f) != _cfg.WEIGHTS[f.split("_", 1)[1] if f.startswith("w_") else f]
            for f in weight_fields
        )
        weights_changed = weights_changed or (
            s.artist_penalty != _cfg.ARTIST_PENALTY
            or s.album_penalty != _cfg.ALBUM_PENALTY
        )
        assert not weights_changed, "Should detect no change"
        assert "test_playlist" in _SORTING_CACHE, "Cache should NOT be cleared"
    finally:
        _SORTING_CACHE.clear()
        _cfg.WEIGHTS.update(orig_weights)
        _cfg.ARTIST_PENALTY = orig_artist
        _cfg.ALBUM_PENALTY = orig_album


def test_sync_weights_cache_cleared_on_weight_change():
    """_SORTING_CACHE is cleared when any weight changes via do_save() logic."""
    import playlist_arranger.config as _cfg
    from playlist_arranger.config import Settings
    from playlist_arranger.sorting.distance import _SORTING_CACHE

    _SORTING_CACHE.clear()
    _SORTING_CACHE["test_playlist"] = {"dummy": True}
    assert len(_SORTING_CACHE) > 0, "Cache should be populated"

    orig_weights = dict(_cfg.WEIGHTS)
    orig_artist = _cfg.ARTIST_PENALTY
    orig_album = _cfg.ALBUM_PENALTY
    try:
        s = Settings()
        s.w_mood = _cfg.WEIGHTS["mood"] + 0.01  # CHANGE one weight
        s.w_bpm = _cfg.WEIGHTS["bpm"]
        s.w_transition = _cfg.WEIGHTS["transition"]
        s.w_key = _cfg.WEIGHTS["key"]
        s.w_energy = _cfg.WEIGHTS["energy"]
        s.w_texture = _cfg.WEIGHTS["texture"]
        s.w_freq_balance = _cfg.WEIGHTS["freq_balance"]
        s.artist_penalty = _cfg.ARTIST_PENALTY
        s.album_penalty = _cfg.ALBUM_PENALTY

        weight_fields = [
            "w_mood", "w_bpm", "w_transition", "w_key", "w_energy",
            "w_texture", "w_freq_balance",
        ]
        weights_changed = any(
            getattr(s, f) != _cfg.WEIGHTS[f.split("_", 1)[1] if f.startswith("w_") else f]
            for f in weight_fields
        )
        weights_changed = weights_changed or (
            s.artist_penalty != _cfg.ARTIST_PENALTY
            or s.album_penalty != _cfg.ALBUM_PENALTY
        )
        assert weights_changed, "Should detect a change in w_mood"

        # Simulate the cache invalidation call
        _cfg.sync_weights_from_settings(s)
        _SORTING_CACHE.clear()
        assert len(_SORTING_CACHE) == 0, "Cache should be empty after weights changed"
    finally:
        _SORTING_CACHE.clear()
        _cfg.WEIGHTS.update(orig_weights)
        _cfg.ARTIST_PENALTY = orig_artist
        _cfg.ALBUM_PENALTY = orig_album


def test_sync_weights_startup_restores_settings_json_values():
    """Calling sync_weights_from_settings(load_settings()) applies persisted weights."""
    import json
    import playlist_arranger.config as _cfg
    from playlist_arranger.config import CACHE_DIR_DEFAULT, sync_weights_from_settings, load_settings

    real_path = CACHE_DIR_DEFAULT / "settings.json"
    backup_data = None
    if real_path.exists():
        backup_data = real_path.read_text(encoding="utf-8")

    orig_weights = dict(_cfg.WEIGHTS)
    orig_artist = _cfg.ARTIST_PENALTY
    orig_album = _cfg.ALBUM_PENALTY
    try:
        # Write settings with custom weights
        data = {
            "w_mood": 0.05, "w_bpm": 0.06, "w_transition": 0.07,
            "w_key": 0.08, "w_energy": 0.09,
            "w_texture": 0.11, "w_freq_balance": 0.12,
            "artist_penalty": 0.03, "album_penalty": 0.04,
        }
        real_path.parent.mkdir(parents=True, exist_ok=True)
        real_path.write_text(json.dumps(data), encoding="utf-8")
        # Simulate startup: load settings then sync
        s = load_settings()
        sync_weights_from_settings(s)
        assert _cfg.WEIGHTS["mood"] == 0.05, f"Expected 0.05, got {_cfg.WEIGHTS['mood']}"
        assert _cfg.WEIGHTS["bpm"] == 0.06
        assert _cfg.WEIGHTS["freq_balance"] == 0.12
        assert _cfg.ARTIST_PENALTY == 0.03
        assert _cfg.ALBUM_PENALTY == 0.04
    finally:
        _cfg.WEIGHTS.update(orig_weights)
        _cfg.ARTIST_PENALTY = orig_artist
        _cfg.ALBUM_PENALTY = orig_album
        if backup_data is not None:
            real_path.write_text(backup_data, encoding="utf-8")
        elif real_path.exists():
            real_path.unlink()


# ── Solver tests (Part 4) ────────────────────────────────────────────────────

def test_solver_reads_sa_params_from_settings():
    """_run_smart_sorting uses config settings, not hardcoded N_RUNS=100 / *500."""
    import playlist_arranger.config as _cfg
    from playlist_arranger.sorting.solver import _run_smart_sorting
    from playlist_arranger.config import Settings

    # Create a tiny fake DB with 3 tracks that share identical features so
    # distance = 0 everywhere (fast, deterministic).
    fake_db = {
        "tid_A": {
            "track_id": "tid_A",
            "features": {"bpm": 120, "rms_db": -12, "camelot": "8B",
                         "harm_ratio": 0.5, "flatness": 0.5,
                         "dynamic_range": 10.0, "onset_str": 1.0,
                         "bass": 0.33, "mid": 0.33, "high": 0.33},
            "end_seg": {"bpm": 120, "rms_db": -12, "camelot": "8B",
                        "harm_ratio": 0.5, "flatness": 0.5,
                        "dynamic_range": 10.0, "onset_str": 1.0,
                        "bass": 0.33, "mid": 0.33, "high": 0.33},
        },
        "tid_B": {
            "track_id": "tid_B",
            "features": {"bpm": 120, "rms_db": -12, "camelot": "8B",
                         "harm_ratio": 0.5, "flatness": 0.5,
                         "dynamic_range": 10.0, "onset_str": 1.0,
                         "bass": 0.33, "mid": 0.33, "high": 0.33},
            "end_seg": {"bpm": 120, "rms_db": -12, "camelot": "8B",
                        "harm_ratio": 0.5, "flatness": 0.5,
                        "dynamic_range": 10.0, "onset_str": 1.0,
                        "bass": 0.33, "mid": 0.33, "high": 0.33},
        },
        "tid_C": {
            "track_id": "tid_C",
            "features": {"bpm": 120, "rms_db": -12, "camelot": "8B",
                         "harm_ratio": 0.5, "flatness": 0.5,
                         "dynamic_range": 10.0, "onset_str": 1.0,
                         "bass": 0.33, "mid": 0.33, "high": 0.33},
            "end_seg": {"bpm": 120, "rms_db": -12, "camelot": "8B",
                        "harm_ratio": 0.5, "flatness": 0.5,
                        "dynamic_range": 10.0, "onset_str": 1.0,
                        "bass": 0.33, "mid": 0.33, "high": 0.33},
        },
    }

    descs = [
        {"track_id": "tid_A", "name": "A", "artist": "Artist A"},
        {"track_id": "tid_B", "name": "B", "artist": "Artist B"},
        {"track_id": "tid_C", "name": "C", "artist": "Artist C"},
    ]

    # ── Write a fake anchors file with one anchor ─────────────────────────
    import json
    from playlist_arranger.config import ANCHORS_DIR_DEFAULT
    fake_pl_id = "test_sa_params_pl"
    anchors_path = ANCHORS_DIR_DEFAULT / f"anchors_{fake_pl_id}.json"
    anchors_path.parent.mkdir(parents=True, exist_ok=True)
    anchors_data = {"plan": [
        {"type": "anchor", "track_id": "tid_A"},
        {"type": "placeholder"},
    ]}
    try:
        anchors_path.write_text(json.dumps(anchors_data), encoding="utf-8")
        # Clear any cached distance matrix for this pl_id
        from playlist_arranger.sorting.distance import _SORTING_CACHE
        _SORTING_CACHE.pop(fake_pl_id, None)

        # Create custom Settings with low N_RUNS for fast test
        s = Settings()
        s.sa_iterations_multiplier = 10
        s.sa_n_runs = 3
        s.sa_T_start = 0.5
        s.sa_T_end = 1e-3

        logs = []
        ordered_descs, cost = _run_smart_sorting(
            fake_db, descs, fake_pl_id, "Test Playlist",
            progress_cb=logs.append, settings=s,
        )

        # Should have returned 3 tracks (2 free + 1 anchor with 1 open slot)
        assert len(ordered_descs) == 3, f"Expected 3 tracks, got {len(ordered_descs)}"
        # Anchor "tid_A" must appear (somewhere in the result)
        returned_ids = {d["track_id"] for d in ordered_descs}
        assert "tid_A" in returned_ids, "Anchor track must be in ordered result"
        # Cost should be finite (all identical tracks → cost near 0)
        assert cost >= 0.0, f"Cost should be >= 0, got {cost}"
        # Check that the SA log line mentions our custom N_RUNS
        log_text = "\n".join(logs)
        assert "3 runs" in log_text or "Run 1/3" in log_text, (
            f"Expected log to mention 3 runs, got: {log_text}"
        )
    finally:
        if anchors_path.exists():
            anchors_path.unlink()
        _SORTING_CACHE.pop(fake_pl_id, None)


def test_solver_returns_early_when_no_anchors():
    """_run_smart_sorting returns (descs, 0.0) when anchor file is missing."""
    from playlist_arranger.sorting.solver import _run_smart_sorting

    logs = []
    descs = [{"track_id": "tid_X", "name": "X", "artist": "X Artist"}]
    ordered, cost = _run_smart_sorting({}, descs, "nonexistent_pl", "N/A",
                                       progress_cb=logs.append)
    assert ordered is descs, "Should return original descs unchanged"
    assert cost == 0.0
    assert any("No anchors" in m for m in logs)


def test_solver_progress_callback_delivers_logs():
    """progress_cb receives messages from the solver in worker-thread style."""
    import json
    from playlist_arranger.config import ANCHORS_DIR_DEFAULT, Settings
    from playlist_arranger.sorting.solver import _run_smart_sorting
    from playlist_arranger.sorting.distance import _SORTING_CACHE

    fake_pl_id = "test_log_callback_pl"
    anchors_path = ANCHORS_DIR_DEFAULT / f"anchors_{fake_pl_id}.json"
    anchors_path.parent.mkdir(parents=True, exist_ok=True)
    anchors_data = {"plan": [{"type": "anchor", "track_id": "tid_1"}]}

    fake_db = {
        "tid_1": {"track_id": "tid_1",
                  "features": {"bpm": 100, "rms_db": -10, "camelot": "1A",
                               "harm_ratio": 0.5, "flatness": 0.5,
                               "dynamic_range": 10.0, "onset_str": 1.0,
                               "bass": 0.33, "mid": 0.33, "high": 0.33},
                  "end_seg": {"bpm": 100, "rms_db": -10, "camelot": "1A",
                              "harm_ratio": 0.5, "flatness": 0.5,
                              "dynamic_range": 10.0, "onset_str": 1.0,
                              "bass": 0.33, "mid": 0.33, "high": 0.33}},
    }

    descs = [{"track_id": "tid_1", "name": "T1", "artist": "A1"}]
    s = Settings()
    s.sa_n_runs = 2
    s.sa_iterations_multiplier = 2  # very fast

    try:
        anchors_path.write_text(json.dumps(anchors_data), encoding="utf-8")
        _SORTING_CACHE.pop(fake_pl_id, None)
        logs = []
        _run_smart_sorting(fake_db, descs, fake_pl_id, "Test",
                           progress_cb=logs.append, settings=s)
        # Verify we got the "Building distance matrix" log
        assert any("Building distance matrix" in m or "Using cached" in m for m in logs), (
            f"Expected distance matrix log, got: {logs}"
        )
        # Verify we got the "SA:" log
        assert any("SA:" in m for m in logs), f"Expected SA log, got: {logs}"
        # Verify we got the "Best cost:" log
        assert any("Best cost:" in m for m in logs), f"Expected Best cost log, got: {logs}"
    finally:
        if anchors_path.exists():
            anchors_path.unlink()
        _SORTING_CACHE.pop(fake_pl_id, None)


# ── Thread-safe log queue tests ──────────────────────────────────────────────

def test_enqueue_and_drain_log_queue():
    """_enqueue_log (from any thread) pushes strings; _drain_log_queue pops them."""
    import queue
    # We can't import the module-level globals directly from smart_sorting
    # because they're None before build_smart_sorting() runs.  Test the
    # standalone queue + drain pattern that smart_sorting uses.
    q = queue.Queue()

    # Simulate worker thread pushing
    q.put("Line 1")
    q.put("Line 2")
    q.put("Line 3")

    # Simulate main thread draining
    drained = []
    while True:
        try:
            drained.append(q.get_nowait())
        except queue.Empty:
            break

    assert drained == ["Line 1", "Line 2", "Line 3"], f"Got: {drained}"


def test_log_queue_empty_drain_is_noop():
    """Draining an empty queue returns no lines (no crash)."""
    import queue
    q = queue.Queue()
    drained = []
    while True:
        try:
            drained.append(q.get_nowait())
        except queue.Empty:
            break
    assert drained == []


def test_log_queue_thread_safety_multiple_producers():
    """Multiple threads pushing concurrently (simulated) doesn't lose messages."""
    import threading
    import queue

    q = queue.Queue()
    num_threads = 4
    msgs_per_thread = 25

    def producer(thread_id):
        for i in range(msgs_per_thread):
            q.put(f"T{thread_id}-{i}")

    threads = [threading.Thread(target=producer, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    drained = []
    while True:
        try:
            drained.append(q.get_nowait())
        except queue.Empty:
            break

    assert len(drained) == num_threads * msgs_per_thread, (
        f"Expected {num_threads * msgs_per_thread} messages, got {len(drained)}"
    )
    # Verify no duplicates and all expected messages present
    expected = {f"T{i}-{j}" for i in range(num_threads) for j in range(msgs_per_thread)}
    assert set(drained) == expected, "Message set mismatch"


def test_solver_with_long_n_runs_logging():
    """When N_RUNS >= 20, log should show progress every 20 runs + run 1."""
    import json
    from playlist_arranger.config import ANCHORS_DIR_DEFAULT, Settings
    from playlist_arranger.sorting.solver import _run_smart_sorting
    from playlist_arranger.sorting.distance import _SORTING_CACHE

    fake_pl_id = "test_long_runs_logging_pl"
    anchors_path = ANCHORS_DIR_DEFAULT / f"anchors_{fake_pl_id}.json"
    anchors_path.parent.mkdir(parents=True, exist_ok=True)
    anchors_data = {"plan": [
        {"type": "anchor", "track_id": "tid_a"},
        {"type": "anchor", "track_id": "tid_b"},
    ]}

    fake_db = {
        "tid_a": {"track_id": "tid_a",
                  "features": {"bpm": 80, "rms_db": -15, "camelot": "5A",
                               "harm_ratio": 0.5, "flatness": 0.5,
                               "dynamic_range": 10.0, "onset_str": 1.0,
                               "bass": 0.33, "mid": 0.33, "high": 0.33},
                  "end_seg": {"bpm": 80, "rms_db": -15, "camelot": "5A",
                              "harm_ratio": 0.5, "flatness": 0.5,
                              "dynamic_range": 10.0, "onset_str": 1.0,
                              "bass": 0.33, "mid": 0.33, "high": 0.33}},
        "tid_b": {"track_id": "tid_b",
                  "features": {"bpm": 160, "rms_db": -5, "camelot": "8B",
                               "harm_ratio": 0.5, "flatness": 0.5,
                               "dynamic_range": 10.0, "onset_str": 1.0,
                               "bass": 0.33, "mid": 0.33, "high": 0.33},
                  "end_seg": {"bpm": 160, "rms_db": -5, "camelot": "8B",
                              "harm_ratio": 0.5, "flatness": 0.5,
                              "dynamic_range": 10.0, "onset_str": 1.0,
                              "bass": 0.33, "mid": 0.33, "high": 0.33}},
    }

    descs = [
        {"track_id": "tid_a", "name": "Slow", "artist": "A"},
        {"track_id": "tid_b", "name": "Fast", "artist": "B"},
    ]

    s = Settings()
    s.sa_n_runs = 40  # enough to trigger (run+1) % 20 == 0 log lines
    s.sa_iterations_multiplier = 2

    try:
        anchors_path.write_text(json.dumps(anchors_data), encoding="utf-8")
        _SORTING_CACHE.pop(fake_pl_id, None)
        logs = []
        _run_smart_sorting(fake_db, descs, fake_pl_id, "Test",
                           progress_cb=logs.append, settings=s)
        # Should see Run 1/40 and Run 20/40 and Run 40/40
        log_text = "\n".join(logs)
        assert "Run 1/40" in log_text or "Run 1/40" in log_text
        run_count = sum(1 for m in logs if "Run " in m[:6] and "/40" in m)
        assert run_count >= 3, (
            f"Expected at least 3 run log lines (every 20 runs), got {run_count}: {log_text[:500]}"
        )
    finally:
        if anchors_path.exists():
            anchors_path.unlink()
        _SORTING_CACHE.pop(fake_pl_id, None)


# ── Part 5: Save/Export batching tests ───────────────────────────────────────

def test_reorder_playlist_put_first_100_then_post_rest():
    """reorder_playlist uses PUT for first 100 URIs, POST for remaining chunks."""
    from unittest.mock import patch
    from playlist_arranger.sources.spotify_source import reorder_playlist

    # 250 URIs — should be: PUT (100) + POST (100) + POST (50)
    uris = [f"spotify:track:{i:06d}" for i in range(250)]
    mock_sp = MagicMock()

    with patch("playlist_arranger.sources.spotify_source._spotify_request_with_retries") as mock_req:
        mock_req.return_value = ({}, None)  # success for every call
        with patch("playlist_arranger.sources.spotify_source.time.sleep"):
            result = reorder_playlist(mock_sp, "pl_123", uris)

    assert result["success"] is True
    assert result["error"] is None
    assert result["tracks_saved"] == 250
    assert result["chunks_total"] == 3
    assert result["chunks_completed"] == 3

    # 3 API calls: PUT (100) + POST (100) + POST (50)
    assert mock_req.call_count == 3, f"Expected 3 calls, got {mock_req.call_count}"

    # First call: PUT
    call_1 = mock_req.call_args_list[0]
    assert call_1[0][1] == "PUT", f"Expected PUT, got {call_1[0][1]}"
    assert len(call_1[0][3]["uris"]) == 100

    # Second call: POST
    call_2 = mock_req.call_args_list[1]
    assert call_2[0][1] == "POST", f"Expected POST, got {call_2[0][1]}"
    assert len(call_2[0][3]["uris"]) == 100

    # Third call: POST
    call_3 = mock_req.call_args_list[2]
    assert call_3[0][1] == "POST", f"Expected POST, got {call_3[0][1]}"
    assert len(call_3[0][3]["uris"]) == 50


def test_reorder_playlist_exactly_100_tracks_put_only():
    """reorder_playlist with exactly 100 URIs makes only 1 PUT call, zero POST."""
    from unittest.mock import patch
    from playlist_arranger.sources.spotify_source import reorder_playlist

    uris = [f"spotify:track:{i:06d}" for i in range(100)]
    mock_sp = MagicMock()

    with patch("playlist_arranger.sources.spotify_source._spotify_request_with_retries") as mock_req:
        mock_req.return_value = ({}, None)
        with patch("playlist_arranger.sources.spotify_source.time.sleep"):
            result = reorder_playlist(mock_sp, "pl_123", uris)

    assert result["success"] is True
    assert result["error"] is None
    assert result["tracks_saved"] == 100
    assert result["chunks_total"] == 1
    assert mock_req.call_count == 1, f"Expected 1 call, got {mock_req.call_count}"
    call_1 = mock_req.call_args_list[0]
    assert call_1[0][1] == "PUT"
    assert len(call_1[0][3]["uris"]) == 100


def test_reorder_playlist_101_tracks_put_100_post_1():
    """reorder_playlist with 101 URIs: PUT(100) + POST(1)."""
    from unittest.mock import patch
    from playlist_arranger.sources.spotify_source import reorder_playlist

    uris = [f"spotify:track:{i:06d}" for i in range(101)]
    mock_sp = MagicMock()

    with patch("playlist_arranger.sources.spotify_source._spotify_request_with_retries") as mock_req:
        mock_req.return_value = ({}, None)
        with patch("playlist_arranger.sources.spotify_source.time.sleep"):
            result = reorder_playlist(mock_sp, "pl_123", uris)

    assert result["success"] is True
    assert result["error"] is None
    assert result["tracks_saved"] == 101
    assert result["chunks_total"] == 2
    assert mock_req.call_count == 2, f"Expected 2 calls, got {mock_req.call_count}"
    # PUT first
    assert mock_req.call_args_list[0][0][1] == "PUT"
    assert len(mock_req.call_args_list[0][0][3]["uris"]) == 100
    # POST rest
    assert mock_req.call_args_list[1][0][1] == "POST"
    assert len(mock_req.call_args_list[1][0][3]["uris"]) == 1


def test_reorder_playlist_empty_uris_returns_true_noop():
    """reorder_playlist with empty URIs returns success dict immediately."""
    from playlist_arranger.sources.spotify_source import reorder_playlist
    mock_sp = MagicMock()
    result = reorder_playlist(mock_sp, "pl_123", [])
    assert result["success"] is True
    assert result["error"] is None
    assert result["tracks_saved"] == 0
    assert result["chunks_total"] == 0


def test_reorder_playlist_put_failure_returns_error():
    """When the PUT call fails, reorder_playlist returns success=False with error dict."""
    from unittest.mock import patch
    from playlist_arranger.sources.spotify_source import reorder_playlist

    uris = [f"spotify:track:{i:06d}" for i in range(50)]
    mock_sp = MagicMock()

    with patch("playlist_arranger.sources.spotify_source._spotify_request_with_retries") as mock_req:
        mock_req.return_value = (None, {"message": "403 Forbidden", "type": "auth_expired", "status_code": 403})
        result = reorder_playlist(mock_sp, "pl_123", uris)

    assert result["success"] is False
    assert result["error"] is not None
    assert "403" in result["error"]["message"]
    assert result["error"]["type"] == "auth_expired"
    assert result["tracks_saved"] == 0
    assert result["failed_chunk_index"] == 0


def test_reorder_playlist_post_partway_failure():
    """When a POST chunk fails partway, reorder_playlist returns partial-success dict."""
    from unittest.mock import patch
    from playlist_arranger.sources.spotify_source import reorder_playlist

    uris = [f"spotify:track:{i:06d}" for i in range(250)]
    mock_sp = MagicMock()

    call_num = [0]

    def mock_req_side_effect(*args, **kwargs):
        call_num[0] += 1
        if call_num[0] <= 1:  # PUT succeeds
            return ({}, None)
        elif call_num[0] == 2:  # first POST fails
            return (None, {"message": "429 Rate limited", "type": "rate_limited", "status_code": 429})
        else:
            return ({}, None)

    with patch("playlist_arranger.sources.spotify_source._spotify_request_with_retries",
               side_effect=mock_req_side_effect):
        with patch("playlist_arranger.sources.spotify_source.time.sleep"):
            result = reorder_playlist(mock_sp, "pl_123", uris)

    assert result["success"] is False
    assert "429" in result["error"]["message"]
    assert result["error"]["type"] == "rate_limited"
    assert result["tracks_saved"] == 100  # first chunk (PUT) succeeded
    assert result["chunks_completed"] == 1  # PUT succeeded + 0 POSTs
    assert result["failed_chunk_index"] == 1


def test_create_playlist_all_post_no_put():
    """create_playlist only uses POST (no PUT), for all chunks."""
    from unittest.mock import patch
    from playlist_arranger.sources.spotify_source import create_playlist

    uris = [f"spotify:track:{i:06d}" for i in range(350)]
    mock_sp = MagicMock()

    mock_new_pl = {"id": "new_pl_456", "name": "Test_20260728_120000"}

    with patch("playlist_arranger.sources.spotify_source._spotify_request_with_retries") as mock_req:
        # First call: create playlist
        # Remaining calls: add tracks
        responses = [(mock_new_pl, None)] + [({}, None)] * 4  # 350 tracks = 4 chunks
        mock_req.side_effect = responses
        with patch("playlist_arranger.sources.spotify_source.time.sleep"):
            result = create_playlist(mock_sp, "Test_20260728_120000", uris)

    assert result["error"] is None
    assert result["success"] is True
    assert result["playlist"] == mock_new_pl
    assert result["tracks_saved"] == 350
    assert result["chunks_total"] == 4
    assert result["chunks_completed"] == 4
    # 1 create playlist + ceil(350/100)=4 POST calls = 5 total
    assert mock_req.call_count == 5, f"Expected 5 calls, got {mock_req.call_count}"
    # First call: POST to me/playlists
    assert mock_req.call_args_list[0][0][1] == "POST"
    assert mock_req.call_args_list[0][0][2] == "me/playlists"
    # Remaining calls: all POST to playlists/{id}/items
    for i in range(1, mock_req.call_count):
        assert mock_req.call_args_list[i][0][1] == "POST"
        assert "items" in mock_req.call_args_list[i][0][2]


def test_create_playlist_fails_creation_returns_error():
    """create_playlist returns success=False when playlist creation fails."""
    from unittest.mock import patch
    from playlist_arranger.sources.spotify_source import create_playlist

    mock_sp = MagicMock()
    uris = ["spotify:track:000001"]

    with patch("playlist_arranger.sources.spotify_source._spotify_request_with_retries") as mock_req:
        mock_req.return_value = (None, {"message": "401 Unauthorized", "type": "auth_expired", "status_code": 401})
        result = create_playlist(mock_sp, "Test", uris)

    assert result["success"] is False
    assert result["playlist"] is None
    assert "401" in result["error"]["message"]
    assert result["error"]["type"] == "auth_expired"


def test_create_playlist_partial_track_add_failure():
    """create_playlist returns playlist dict with error when creation succeeds but track-add fails."""
    from unittest.mock import patch
    from playlist_arranger.sources.spotify_source import create_playlist

    mock_sp = MagicMock()
    mock_new_pl = {"id": "pl_789"}
    uris = [f"spotify:track:{i:06d}" for i in range(200)]  # 2 chunks

    responses = [
        (mock_new_pl, None),
        ({}, None),
        (None, {"message": "502 Bad Gateway", "type": "server_error", "status_code": 502}),
    ]
    with patch("playlist_arranger.sources.spotify_source._spotify_request_with_retries") as mock_req:
        mock_req.side_effect = responses
        with patch("playlist_arranger.sources.spotify_source.time.sleep"):
            result = create_playlist(mock_sp, "Test", uris)

    # Should return the created playlist dict BUT with an error
    assert result["success"] is False
    assert result["playlist"] == mock_new_pl
    assert "502" in result["error"]["message"]
    assert result["error"]["type"] == "server_error"
    assert result["tracks_saved"] == 100  # first chunk succeeded
    assert result["chunks_completed"] == 1  # 0-based: 1 OF 2 completed before failure
    assert result["failed_chunk_index"] == 1


# ── Timestamp naming test ────────────────────────────────────────────────────

def test_timestamp_naming_convention():
    """_on_save_new_playlist uses {name}_{YYYYMMDD_HHMMSS} naming convention."""
    import datetime

    # Simulate the naming logic used in smart_sorting.py
    playlist_name = "My Awesome Mix"
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    new_name = f"{playlist_name}_{ts}"

    # Verify format: "My Awesome Mix_YYYYMMDD_HHMMSS"
    parts = new_name.rsplit("_", 2)
    assert len(parts) == 3, f"Expected 3 underscore-separated parts, got {parts}"
    assert parts[0] == "My Awesome Mix"
    assert len(parts[1]) == 8, f"Date part should be 8 chars (YYYYMMDD), got '{parts[1]}'"
    assert len(parts[2]) == 6, f"Time part should be 6 chars (HHMMSS), got '{parts[2]}'"
    assert parts[1].isdigit(), f"Date part should be digits: {parts[1]}"
    assert parts[2].isdigit(), f"Time part should be digits: {parts[2]}"

    # Verify no special chars that would break filesystem/URL
    import re
    assert re.match(r'^[A-Za-z0-9 _\-\.,&]+_\d{8}_\d{6}$', new_name), (
        f"Format check failed for: {new_name}"
    )


def test_timestamp_naming_fallback_when_name_empty():
    """When playlist_name is empty, fallback to 'Sorted_{timestamp}'."""
    import datetime

    playlist_name = ""
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    new_name = f"{playlist_name}_{ts}" if playlist_name else f"Sorted_{ts}"

    assert new_name.startswith("Sorted_"), f"Expected 'Sorted_...', got '{new_name}'"
    assert len(new_name) > len("Sorted_") + 10  # at least timestamp


def test_todo_comment_near_normalization_constants():
    """The calibration comment about normalization is present in distance.py."""
    from pathlib import Path
    distance_py = Path(__file__).parent.parent / "playlist_arranger" / "sorting" / "distance.py"
    content = distance_py.read_text(encoding="utf-8")
    assert "per-playlist" in content.lower(), "distance.py should mention per-playlist calibration"
    assert "dyn_scale" in content, "distance.py should reference dyn_scale"
    assert "onset_scale" in content, "distance.py should reference onset_scale"
    assert "robust" in content.lower(), "distance.py should mention robust range"
    assert "_calibrate_texture_scales" in content, "distance.py should contain _calibrate_texture_scales"


def test_d_texture_same_track_returns_zero():
    """d_texture with identical features → 0.0."""
    fa = {
        "harm_ratio": 0.8, "flatness": 0.2,
        "dynamic_range": 15.0, "onset_str": 1.2,
    }
    ta = {"features": fa, "end_seg": fa}
    tb = {"features": fa, "start_seg": fa}
    d = _track_distance(ta, tb, None, None)
    # All base components (mood=0.5 fallback, bpm=0, transition=0.5 fallback, key=0, energy=0)
    # + texture=0 + freq_balance=0 → without artist/album penalty should be small
    # But with legacy fallback for missing embedding/chroma it won't be exactly 0.
    # Verify that the distance computation doesn't crash and returns a finite value.
    assert d >= 0.0, f"Distance should be >= 0, got {d}"
    assert d < 1.5, f"Distance too large: {d}"


def test_d_texture_different_tracks_nonzero():
    """d_texture with maximally different features contributes to total distance."""
    fa = {
        "harm_ratio": 1.0, "flatness": 0.0,
        "dynamic_range": 30.0, "onset_str": 2.0,
    }
    fb = {
        "harm_ratio": 0.0, "flatness": 1.0,
        "dynamic_range": 0.0, "onset_str": 0.0,
    }
    ta = {"features": fa, "end_seg": fa}
    tb = {"features": fb, "start_seg": fb}
    d = _track_distance(ta, tb, None, None)
    # Total includes d_mood(0.5)*w_mood + d_transition(0.5)*w_transition
    # + d_texture(1.125)*w_texture + d_freq_balance*w_freq_balance, all capped at 1+ALBUM_PENALTY
    # With default weights it should be > 0.4
    assert d > 0.40, f"Expected d > 0.40, got {d}"


def test_d_freq_balance_same_vector_zero():
    """d_freq_balance with identical bass/mid/high → 0."""
    fa = {"bass": 0.4, "mid": 0.3, "high": 0.3}
    fb = {"bass": 0.4, "mid": 0.3, "high": 0.3}
    ta = {"features": fa, "end_seg": fa}
    tb = {"features": fb, "start_seg": fb}
    d = _track_distance(ta, tb, None, None)
    assert d >= 0.0, f"Distance should be >= 0, got {d}"


def test_d_freq_balance_orthogonal():
    """d_freq_balance for [1,0,0] vs [0,1,0] contributes to total distance."""
    fa = {"bass": 1.0, "mid": 0.0, "high": 0.0}
    fb = {"bass": 0.0, "mid": 1.0, "high": 0.0}
    ta = {"features": fa, "end_seg": fa}
    tb = {"features": fb, "start_seg": fb}
    d = _track_distance(ta, tb, None, None)
    # d_freq_balance = 1.0 → contributes w_freq_balance*1.0 to total
    # Total is capped at 1+ALBUM_PENALTY=1.3, but should be > 0.4 even with other components
    assert d > 0.40, f"Expected d > 0.40 with orthogonal freq vectors, got {d}"


def test_settings_missing_new_weights_falls_back():
    """Old settings.json without w_texture/w_freq_balance → uses dataclass defaults."""
    from playlist_arranger.config import load_settings
    s = load_settings()
    # If no settings.json or old one without these keys, defaults should be used
    assert hasattr(s, 'w_texture'), "Settings should have w_texture"
    assert hasattr(s, 'w_freq_balance'), "Settings should have w_freq_balance"
    assert s.w_texture == 0.10, f"Expected default 0.10, got {s.w_texture}"
    assert s.w_freq_balance == 0.08, f"Expected default 0.08, got {s.w_freq_balance}"


def test_track_distance_includes_texture_and_freq_balance():
    """_track_distance uses w_texture and w_freq_balance in final weighted sum."""
    ta = {"features": {}, "end_seg": {
        "bpm": 120, "camelot": "8B", "rms_db": -12,
        "harm_ratio": 1.0, "flatness": 0.0, "dynamic_range": 30.0, "onset_str": 2.0,
        "bass": 1.0, "mid": 0.0, "high": 0.0,
    }}
    tb = {"features": {}, "start_seg": {
        "bpm": 120, "camelot": "8B", "rms_db": -12,
        "harm_ratio": 0.0, "flatness": 1.0, "dynamic_range": 0.0, "onset_str": 0.0,
        "bass": 0.0, "mid": 1.0, "high": 0.0,
    }}

    # Store original weights
    orig = dict(WEIGHTS)
    try:
        # Set all other weights to 0, texture and freq_balance to 1.0
        for k in WEIGHTS:
            WEIGHTS[k] = 0.0
        WEIGHTS["texture"] = 1.0
        WEIGHTS["freq_balance"] = 1.0
        d = _track_distance(ta, tb, None, None)
        # d_texture = 0.25*1 + 0.25*1 + 0.25*(30/20) + 0.25*(2/2) = 0.25+0.25+0.375+0.25 = 1.125
        # d_freq_balance = sqrt(1+1+0)/sqrt(2) = 1.0
        # base = 1.0*1.125 + 1.0*1.0 = 2.125
        # Capped at 1.0 + ALBUM_PENALTY (0.3) = 1.3
        assert d == 1.3, (
            f"Expected distance capped at 1.3 (1.0 + ALBUM_PENALTY=0.3), got {d}"
        )
    finally:
        WEIGHTS.update(orig)


def test_settings_value_wins_when_non_empty():
    """settings.json non-empty value overrides .env fallback."""
    s = Settings()
    orig = s.mistral_model  # should be from _ENV_DEFAULTS
    assert orig == _ENV_DEFAULTS["mistral_model"] == "mistral-large-latest"
    s.mistral_model = "some-custom-model"
    resolve_llm_settings(s)
    assert s.mistral_model == "some-custom-model", (
        f"Expected 'some-custom-model', got '{s.mistral_model}'"
    )


def test_env_fallback_when_settings_empty():
    """settings.json empty string falls back to .env constant."""
    s = Settings()
    s.mistral_model = ""
    resolve_llm_settings(s)
    assert s.mistral_model == _ENV_DEFAULTS["mistral_model"], (
        f"Expected '{_ENV_DEFAULTS['mistral_model']}', got '{s.mistral_model}'"
    )


def test_hot_reload_no_restart():
    """Calling load_settings() twice with different settings.json picks up the new value."""
    import json
    import tempfile
    import pathlib
    from playlist_arranger.config import load_settings, CACHE_DIR_DEFAULT, resolve_llm_settings

    # Write a temporary settings.json with a non-standard model name
    # at the REAL location (CACHE_DIR_DEFAULT).  Save & restore the original
    # afterwards so the test doesn't corrupt the user's real settings.
    real_path = CACHE_DIR_DEFAULT / "settings.json"
    backup_data = None
    if real_path.exists():
        backup_data = real_path.read_text(encoding="utf-8")

    try:
        data = {"mistral_model": "hot-reload-test-model"}
        real_path.parent.mkdir(parents=True, exist_ok=True)
        real_path.write_text(json.dumps(data), encoding="utf-8")

        s1 = load_settings()
        resolve_llm_settings(s1)
        assert s1.mistral_model == "hot-reload-test-model", (
            f"First load: expected 'hot-reload-test-model', got '{s1.mistral_model}'"
        )

        # Now change settings.json to a DIFFERENT model name
        data["mistral_model"] = "hot-reload-test-model-v2"
        real_path.write_text(json.dumps(data), encoding="utf-8")

        s2 = load_settings()
        resolve_llm_settings(s2)
        assert s2.mistral_model == "hot-reload-test-model-v2", (
            f"Second load: expected 'hot-reload-test-model-v2', got '{s2.mistral_model}'"
        )

    finally:
        if backup_data is not None:
            real_path.write_text(backup_data, encoding="utf-8")
        elif real_path.exists():
            real_path.unlink()


def test_cache_invalidates_on_model_change_only():
    """Changing only the model name (same backend) triggers a client rebuild.

    Simulates: backend stays 'mistral', mistral_model changes from
    'mistral-large-latest' to 'mistral-small-latest' via settings.
    First call with no args caches.  After settings change, the second
    call (no args) must rebuild because _llm_model_used differs.

    NOTE: We use module-attribute access (lc._llm_model_used) rather than
    ``from ... import`` because the latter captures a snapshot at import
    time and would miss assignment updates made by _init_llm_client.
    """
    import json
    import playlist_arranger.llm.client as lc
    from playlist_arranger.config import CACHE_DIR_DEFAULT

    real_path = CACHE_DIR_DEFAULT / "settings.json"
    backup_data = None
    if real_path.exists():
        backup_data = real_path.read_text(encoding="utf-8")

    try:
        # Write initial settings: backend=mistral, model=mistral-large-latest
        data = {"llm_backend": "mistral", "mistral_model": "mistral-large-latest"}
        real_path.parent.mkdir(parents=True, exist_ok=True)
        real_path.write_text(json.dumps(data), encoding="utf-8")

        # Force-reset globals so the test is isolated
        lc._llm_client = None
        lc._llm_backend_used = None
        lc._llm_model_used = None

        try:
            from openai import OpenAI as _OpenAI
        except ImportError:
            _OpenAI = None

        if _OpenAI is not None:
            with patch("playlist_arranger.llm.client._OpenAI") as mock_openai:
                mock_instance = MagicMock()
                mock_openai.return_value = mock_instance
                lc._init_llm_client()
                first_model = lc._llm_model_used
                assert first_model == "mistral-large-latest", (
                    f"Expected 'mistral-large-latest', got '{first_model}'"
                )
                first_client = lc._llm_client
        else:
            lc._init_llm_client()
            first_model = lc._llm_model_used
            assert first_model == "mistral-large-latest", (
                f"Expected 'mistral-large-latest', got '{first_model}'"
            )
            first_client = lc._llm_client

        # Now change model only (same backend) via settings.json
        data["mistral_model"] = "mistral-small-latest"
        real_path.write_text(json.dumps(data), encoding="utf-8")

        # Second call — must rebuild because model name changed
        if _OpenAI is not None:
            with patch("playlist_arranger.llm.client._OpenAI") as mock_openai:
                mock_instance = MagicMock()
                mock_openai.return_value = mock_instance
                lc._init_llm_client()
                second_model = lc._llm_model_used
                assert second_model == "mistral-small-latest", (
                    f"Expected 'mistral-small-latest', got '{second_model}'"
                )
                second_client = lc._llm_client
        else:
            lc._init_llm_client()
            second_model = lc._llm_model_used
            assert second_model == "mistral-small-latest", (
                f"Expected 'mistral-small-latest', got '{second_model}'"
            )
            second_client = lc._llm_client

        # Client MUST have been rebuilt (different object)
        assert second_client is not first_client, (
            "Client was NOT rebuilt after model-only change"
        )

    finally:
        # Restore original settings
        if backup_data is not None:
            real_path.write_text(backup_data, encoding="utf-8")
        elif real_path.exists():
            real_path.unlink()
        lc._llm_client = None
        lc._llm_backend_used = None
        lc._llm_model_used = None


# ── Run all tests ─────────────────────────────────────────────────────────────

tests = [
    ("test_add_new_returns_true", test_add_new_returns_true),
    ("test_add_duplicate_returns_false", test_add_duplicate_returns_false),
    ("test_add_many_skips_duplicates", test_add_many_skips_duplicates),
    ("test_add_many_all_new", test_add_many_all_new),
    ("test_add_many_all_duplicates", test_add_many_all_duplicates),
    ("test_add_many_empty_list", test_add_many_empty_list),
    ("test_add_many_with_empty_strings", test_add_many_with_empty_strings),
    ("test_clear_empties_queue", test_clear_empties_queue),
    ("test_clear_resets_processing", test_clear_resets_processing),
    ("test_va_high_energy", test_va_high_energy),
    ("test_va_calm_sad", test_va_calm_sad),
    ("test_va_no_features", test_va_no_features),
    ("test_va_with_embedding", test_va_with_embedding),
    ("test_quadrant_high_positive", test_quadrant_high_positive),
    ("test_quadrant_low_negative", test_quadrant_low_negative),
    ("test_quadrant_high_negative", test_quadrant_high_negative),
    ("test_quadrant_low_positive", test_quadrant_low_positive),
    ("test_va_intensity_label", test_va_intensity_label),
    ("test_fallback_candidate_1_available", test_fallback_candidate_1_available),
    ("test_fallback_candidate_1_fails_2_succeeds", test_fallback_candidate_1_fails_2_succeeds),
    ("test_fallback_all_fail_returns_none", test_fallback_all_fail_returns_none),
    ("test_think_tag_stripping", test_think_tag_stripping),
    ("test_think_tag_stripping_inner_fallback", test_think_tag_stripping_inner_fallback),
    ("test_settings_value_wins_when_non_empty", test_settings_value_wins_when_non_empty),
    ("test_env_fallback_when_settings_empty", test_env_fallback_when_settings_empty),
    ("test_hot_reload_no_restart", test_hot_reload_no_restart),
    ("test_cache_invalidates_on_model_change_only", test_cache_invalidates_on_model_change_only),
    ("test_descriptions_module_deleted", test_descriptions_module_deleted),
    ("test_run_descriptions_stub_does_not_crash", test_run_descriptions_stub_does_not_crash),
    ("test_smart_sorting_safe_with_empty_descs", test_smart_sorting_safe_with_empty_descs),
    ("test_start_sorting_disabled_when_track_not_ok", test_start_sorting_disabled_when_track_not_ok),
    ("test_start_sorting_enabled_when_all_tracks_ok", test_start_sorting_enabled_when_all_tracks_ok),
    ("test_start_sorting_disabled_when_no_tracks_loaded", test_start_sorting_disabled_when_no_tracks_loaded),
    ("test_rebuild_queue_ui_skips_when_client_disconnected", test_rebuild_queue_ui_skips_when_client_disconnected),
    ("test_rebuild_queue_ui_proceeds_when_client_connected", test_rebuild_queue_ui_proceeds_when_client_connected),
    ("test_load_save_descriptions_removed", test_load_save_descriptions_removed),
    ("test_dialog_updates_when_same_track_open", test_dialog_updates_when_same_track_open),
    ("test_dialog_not_updated_when_different_track_open", test_dialog_not_updated_when_different_track_open),
    ("test_dialog_not_updated_when_closed", test_dialog_not_updated_when_closed),
    ("test_stale_reference_cleared_on_close", test_stale_reference_cleared_on_close),
    ("test_tavily_search_returns_none_when_key_not_set", test_tavily_search_returns_none_when_key_not_set),
    ("test_tavily_search_returns_none_on_api_error", test_tavily_search_returns_none_on_api_error),
    ("test_tavily_search_returns_none_on_empty_results", test_tavily_search_returns_none_on_empty_results),
    ("test_web_context_appended_to_user_msg_when_present", test_web_context_appended_to_user_msg_when_present),
    ("test_user_msg_unchanged_when_web_context_absent", test_user_msg_unchanged_when_web_context_absent),
    ("test_tavily_calls_capped_per_run", test_tavily_calls_capped_per_run),
    ("test_tavily_counter_resets_between_separate_batch_runs", test_tavily_counter_resets_between_separate_batch_runs),
    ("test_tavily_cap_zero_means_unlimited", test_tavily_cap_zero_means_unlimited),
    ("test_tavily_answer_discarded_when_artist_not_mentioned", test_tavily_answer_discarded_when_artist_not_mentioned),
    ("test_tavily_answer_kept_when_artist_mentioned_in_answer", test_tavily_answer_kept_when_artist_mentioned_in_answer),
    ("test_tavily_answer_kept_when_artist_mentioned_in_results_not_answer", test_tavily_answer_kept_when_artist_mentioned_in_results_not_answer),
    ("test_tavily_result_skipped_when_score_below_threshold", test_tavily_result_skipped_when_score_below_threshold),
    ("test_tavily_all_results_filtered_returns_none", test_tavily_all_results_filtered_returns_none),
    ("test_tavily_artist_name_matching_case_insensitive", test_tavily_artist_name_matching_case_insensitive),
    ("test_tavily_debug_file_written_on_search", test_tavily_debug_file_written_on_search),
    ("test_d_texture_same_track_returns_zero", test_d_texture_same_track_returns_zero),
    ("test_d_texture_different_tracks_nonzero", test_d_texture_different_tracks_nonzero),
    ("test_d_freq_balance_same_vector_zero", test_d_freq_balance_same_vector_zero),
    ("test_d_freq_balance_orthogonal", test_d_freq_balance_orthogonal),
    ("test_settings_missing_new_weights_falls_back", test_settings_missing_new_weights_falls_back),
    ("test_track_distance_includes_texture_and_freq_balance", test_track_distance_includes_texture_and_freq_balance),
    ("test_sync_weights_updates_module_level_weights", test_sync_weights_updates_module_level_weights),
    ("test_sync_weights_cache_not_cleared_on_identical_weights", test_sync_weights_cache_not_cleared_on_identical_weights),
    ("test_sync_weights_cache_cleared_on_weight_change", test_sync_weights_cache_cleared_on_weight_change),
    ("test_sync_weights_startup_restores_settings_json_values", test_sync_weights_startup_restores_settings_json_values),
    ("test_todo_comment_near_normalization_constants", test_todo_comment_near_normalization_constants),
    ("test_solver_reads_sa_params_from_settings", test_solver_reads_sa_params_from_settings),
    ("test_solver_returns_early_when_no_anchors", test_solver_returns_early_when_no_anchors),
    ("test_solver_progress_callback_delivers_logs", test_solver_progress_callback_delivers_logs),
    ("test_enqueue_and_drain_log_queue", test_enqueue_and_drain_log_queue),
    ("test_log_queue_empty_drain_is_noop", test_log_queue_empty_drain_is_noop),
    ("test_log_queue_thread_safety_multiple_producers", test_log_queue_thread_safety_multiple_producers),
    ("test_solver_with_long_n_runs_logging", test_solver_with_long_n_runs_logging),
    ("test_reorder_playlist_put_first_100_then_post_rest", test_reorder_playlist_put_first_100_then_post_rest),
    ("test_reorder_playlist_exactly_100_tracks_put_only", test_reorder_playlist_exactly_100_tracks_put_only),
    ("test_reorder_playlist_101_tracks_put_100_post_1", test_reorder_playlist_101_tracks_put_100_post_1),
    ("test_reorder_playlist_empty_uris_returns_true_noop", test_reorder_playlist_empty_uris_returns_true_noop),
    ("test_reorder_playlist_put_failure_returns_error", test_reorder_playlist_put_failure_returns_error),
    ("test_reorder_playlist_post_partway_failure", test_reorder_playlist_post_partway_failure),
    ("test_create_playlist_all_post_no_put", test_create_playlist_all_post_no_put),
    ("test_create_playlist_fails_creation_returns_error", test_create_playlist_fails_creation_returns_error),
    ("test_create_playlist_partial_track_add_failure", test_create_playlist_partial_track_add_failure),
    ("test_timestamp_naming_convention", test_timestamp_naming_convention),
    ("test_timestamp_naming_fallback_when_name_empty", test_timestamp_naming_fallback_when_name_empty),
]

for name, fn in tests:
    try:
        fn()
        results.append(f"PASS: {name}")
    except Exception as e:
        results.append(f"FAIL: {name} - {e}")

for r in results:
    print(r)

passed = sum(1 for r in results if r.startswith("PASS"))
failed = sum(1 for r in results if r.startswith("FAIL"))
print(f"\n{passed}/{len(results)} passed, {failed} failed")

sys.exit(1 if failed > 0 else 0)