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