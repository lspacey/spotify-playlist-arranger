"""Regression test: ``show_track_compact_table`` must cancel previous timers
before creating new ones for the same ``pl_id``.

NiceGUI ``ui.timer()`` is an Element whose ``_handle_delete()`` does NOT
cancel the backing asyncio coroutine — so when a parent container is
``.clear()``'d, the timer's DOM element is removed but the background
loop keeps ticking on stale closures.  Repeated expand/collapse cycles
would accumulate orphan timers without explicit cancellation.

Tests cover both:
  * **Source inspection** — dicts exist, are stored per pl_id, and cancel
    calls appear near each store site.
  * **Behavioral** — three consecutive calls to
    ``show_track_compact_table()`` for the same ``pl_id`` with a mocked
    ``ui.timer``; the test verifies that exactly the first two handles
    are cancelled and only the last one remains active in the
    tracking dict.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from unittest.mock import MagicMock, patch

import pytest

_SRC_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "playlist_arranger"
    / "ui"
    / "track_table.py"
)


def _src_lines():
    return _SRC_PATH.read_text(encoding="utf-8").splitlines()


# ══════════════════════════════════════════════════════════════════════════════
#  Source-inspection tests (unchanged from step 1)
# ══════════════════════════════════════════════════════════════════════════════

class TestTimerCancellationBeforeRebuild:
    """Each of the three timer creation sites must cancel previous timers."""

    DICT_NAMES = [
        "_status_refresh_timers",
        "_add_queue_btn_timers",
        "_desc_sel_btn_timers",
    ]

    @pytest.mark.parametrize("dict_name", DICT_NAMES)
    def test_dict_is_defined_before_use(self, dict_name):
        lines = _src_lines()
        defn = [l for l in lines if l.strip().startswith(f"{dict_name}")]
        assert defn, f"{dict_name} not defined in track_table.py"
        defn_idx = lines.index(defn[0])
        assert defn_idx < 150, (
            f"{dict_name} defined too late (line {defn_idx + 1}) — "
            f"should be a module-level near the top"
        )

    @pytest.mark.parametrize("dict_name", DICT_NAMES)
    def test_timer_stored_in_dict_by_pl_id_key(self, dict_name):
        lines = _src_lines()
        func_start = None
        for i, line in enumerate(lines):
            if "def show_track_compact_table" in line:
                func_start = i
                break
        assert func_start is not None, "show_track_compact_table not found"
        store_patterns = [
            f"{dict_name}[pl_id] = ui.timer",
            f"{dict_name}[pl_id]=",
        ]
        found = False
        for i in range(func_start, len(lines)):
            if any(pat in lines[i] for pat in store_patterns):
                found = True
                break
        assert found, (
            f"No line found assigning to {dict_name}[pl_id] inside "
            f"show_track_compact_table().  Ensure the timer is stored "
            f"so it can be cancelled on next rebuild."
        )

    @pytest.mark.parametrize("dict_name", DICT_NAMES)
    def test_previous_timer_is_cancelled_before_storing_new_one(self, dict_name):
        lines = _src_lines()
        func_start = None
        for i, line in enumerate(lines):
            if "def show_track_compact_table" in line:
                func_start = i
                break
        assert func_start is not None

        store_line_idx = None
        for i in range(func_start, len(lines)):
            if f"{dict_name}[pl_id]" in lines[i]:
                store_line_idx = i
                break
        assert store_line_idx is not None, (
            f"No store line found for {dict_name}[pl_id]"
        )

        cancel_found = False
        for i in range(store_line_idx - 1, func_start, -1):
            if ".cancel()" in lines[i]:
                window = lines[max(func_start, i - 4):i + 1]
                if any(dict_name in w for w in window):
                    cancel_found = True
                    break
        assert cancel_found, (
            f"No .cancel() call guarded by {dict_name} found between "
            f"show_track_compact_table start (line {func_start + 1}) and "
            f"the store line (line {store_line_idx + 1}).  "
            f"Previous timer must be cancelled before creating a new one."
        )


class TestTimerDictExportedForTestability:
    """Dicts must be module-level, not inside ``show_track_compact_table``."""

    @pytest.mark.parametrize("dict_name", TestTimerCancellationBeforeRebuild.DICT_NAMES)
    def test_dict_not_defined_inside_show_track_compact_table(self, dict_name):
        lines = _src_lines()
        func_start = None
        for i, line in enumerate(lines):
            if "def show_track_compact_table" in line:
                func_start = i
                break
        assert func_start is not None
        base_indent = len(lines[func_start]) - len(lines[func_start].lstrip())
        for i in range(func_start + 1, len(lines)):
            if not lines[i].strip():
                continue
            line_indent = len(lines[i]) - len(lines[i].lstrip())
            if line_indent <= base_indent:
                break
            if dict_name in lines[i] and "=" in lines[i] and "{" in lines[i]:
                raise AssertionError(
                    f"{dict_name} appears to be defined inside "
                    f"show_track_compact_table at line {i + 1} — "
                    f"must be module-level so timers survive across rebuilds"
                )


# ══════════════════════════════════════════════════════════════════════════════
#  Behavioral test: 3 rebuild cycles → only 1 active timer per category
# ══════════════════════════════════════════════════════════════════════════════

# ── Helpers to build a fake track list ─────────────────────────────────────────

def _make_tracks(count=3):
    """Return a minimal track list suitable for show_track_compact_table."""
    return [
        {
            "id": f"track_{i:04d}",
            "name": f"Track {i}",
            "artist": f"Artist {i}",
            "duration_ms": 180000 + i * 1000,
        }
        for i in range(count)
    ]


# ── Dict keys used by the three timer categories ───────────────────────────────

_CAT_DICT_ATTRS = [
    "_add_queue_btn_timers",    # created 1st per call (inside _build_add_to_queue_btn)
    "_desc_sel_btn_timers",     # created 2nd per call (description gen button)
    "_status_refresh_timers",   # created 3rd per call (status cell refresh)
]


class TestRepeatedRebuildLeavesOneActiveTimerPerPlaylist:
    """Three consecutive ``show_track_compact_table()`` calls for the SAME
    ``pl_id`` must cancel the first two timer handles and leave only the
    third one alive in the tracking dict."""

    PL_ID = "playlist_abc"

    @pytest.fixture(autouse=True)
    def _setup_module(self, monkeypatch):
        """Inject mocked dependencies into ``playlist_arranger.ui.track_table``
        so ``show_track_compact_table`` can be called without a real NiceGUI
        process.  We also reset the three timer-tracking dicts before each
        test."""
        import playlist_arranger.ui.track_table as tt
        import playlist_arranger.ui.state as state_mod
        import playlist_arranger.ui.playlist_highlight as ph_mod
        import playlist_arranger.analysis.desc_generator as desc_gen_mod
        import playlist_arranger.ui.table_pagination as tp_mod

        # ── Fake ui ────────────────────────────────────────────────────────
        mock_ui = MagicMock()
        # ui.timer — each call returns a fresh distinct MagicMock
        fake_timers = []  # mutable list so closures see updates
        def _fake_timer(*a, **kw):
            h = MagicMock()
            fake_timers.append(h)
            return h
        mock_ui.timer = _fake_timer
        mock_ui.label = MagicMock(return_value=MagicMock())
        mock_ui.row = MagicMock()
        mock_ui.column = MagicMock(return_value=MagicMock())
        mock_ui.table = MagicMock()
        mock_ui.button = MagicMock(return_value=MagicMock())
        mock_ui.notify = MagicMock()
        mock_ui.add_head_html = MagicMock()
        mock_ui.context = MagicMock(client=MagicMock())

        monkeypatch.setattr(tt, "ui", mock_ui)

        # ── State ──────────────────────────────────────────────────────────
        state_mod.STATUS_OK = "✓ OK"
        def _fake_status(track):
            return "✓ OK"
        state_mod.get_track_status = _fake_status
        def _fake_status_desc(track):
            return {
                "status": "✓ OK",
                "desc": "✓",
                "desc_icon": "auto_stories",
                "desc_color": "green",
                "desc_caption": "Sample",
            }
        state_mod.get_track_status_with_desc = _fake_status_desc
        tt._state = state_mod
        tt._get_track_status = _fake_status

        # ── Playlist highlight ─────────────────────────────────────────────
        ph_mod._playlist_tables = {}
        ph_mod._playlist_rows_cache = {}
        ph_mod._now_playing_row_keys = {}
        ph_mod._sync_now_playing_row_highlight = MagicMock()
        tt._ph = ph_mod

        # ── Desc generator ─────────────────────────────────────────────────
        desc_gen_mod.desc_queue_add_many = MagicMock(return_value=1)
        tt._desc_gen = desc_gen_mod

        # ── Other injected fns ─────────────────────────────────────────────
        tt._add_selected_to_queue_fn = MagicMock()
        tt._persist_queue_fn = MagicMock()
        tt._update_queue_label_fn = MagicMock()
        tt._rebuild_queue_ui_fn = MagicMock()
        tt._recover_from_backup_fn = MagicMock()
        tt._backup_exists_fn = MagicMock(return_value=False)
        tt._show_desc_dialog_fn = MagicMock()
        tt._play_track = MagicMock()
        tt._db = MagicMock()
        tt._get_desc_age_info = MagicMock()

        # ── Table pagination ───────────────────────────────────────────────
        tp_mod.get_default_pagination = MagicMock(
            return_value={"rowsPerPage": 50, "page": 1}
        )
        tp_mod.on_pagination_change_handler = MagicMock(return_value=MagicMock())
        tp_mod.apply_pagination = MagicMock()

        # ── Reset the three timer-tracking dicts ───────────────────────────
        tt._status_refresh_timers.clear()
        tt._add_queue_btn_timers.clear()
        tt._desc_sel_btn_timers.clear()

        # Store for test assertions
        self._tt = tt
        self._fake_timers = fake_timers

    # ── The test ───────────────────────────────────────────────────────────

    def test_three_rebuilds_for_same_pl_id(self):
        """Call ``show_track_compact_table`` 3 times for the same pl_id.
        Assert:
        * For each timer category, exactly 2 handles were cancelled (the
          first two), and the 3rd was NOT cancelled.
        * Each tracking dict has exactly 1 entry for that pl_id.
        """
        tracks = _make_tracks(3)
        self._fake_timers.clear()

        # ── Call 1 ─────────────────────────────────────────────────────────
        self._tt.show_track_compact_table(
            tracks, self.PL_ID, "Test Playlist", MagicMock()
        )
        # After call 1, each category dict should have 1 entry.
        for attr in _CAT_DICT_ATTRS:
            d = getattr(self._tt, attr)
            assert self.PL_ID in d, f"{attr} missing pl_id after call 1"
            assert len(d) == 1, f"{attr} has {len(d)} entries after call 1"

        # ── Call 2 ─────────────────────────────────────────────────────────
        self._tt.show_track_compact_table(
            tracks, self.PL_ID, "Test Playlist", MagicMock()
        )
        for attr in _CAT_DICT_ATTRS:
            d = getattr(self._tt, attr)
            assert self.PL_ID in d, f"{attr} missing pl_id after call 2"
            assert len(d) == 1, f"{attr} has {len(d)} entries after call 2 "

        # ── Call 3 ─────────────────────────────────────────────────────────
        self._tt.show_track_compact_table(
            tracks, self.PL_ID, "Test Playlist", MagicMock()
        )
        for attr in _CAT_DICT_ATTRS:
            d = getattr(self._tt, attr)
            assert self.PL_ID in d, f"{attr} missing pl_id after call 3"
            assert len(d) == 1, f"{attr} has {len(d)} entries after call 3"

        # ── Assert cancel counts on each individual handle ──────────────────
        # Timer creation order per call:
        #   [0] add_queue_btn_timer  (stored in _add_queue_btn_timers)
        #   [1] desc_sel_btn_timer   (stored in _desc_sel_btn_timers)
        #   [2] status_refresh_timer (stored in _status_refresh_timers)
        #
        # After 3 calls, the flat list is:
        #   idx  0  1  2    3  4  5    6  7  8
        #   cat  A  D  S    A  D  S    A  D  S
        #   call 1  1  1    2  2  2    3  3  3
        #
        # Handles 0-5 should all have .cancel() called exactly once
        # (by calls 2 and 3 respectively).  Handles 6-8 (call 3) must
        # NOT be cancelled — they're the active ones.
        #
        # Per-category indexing: cat {A,D,S} = cat_idx 0,1,2
        #   call 1 handle = fake_timers[cat_idx + 0*3]
        #   call 2 handle = fake_timers[cat_idx + 1*3]
        #   call 3 handle = fake_timers[cat_idx + 2*3]

        for cat_idx in range(3):
            h0 = self._fake_timers[cat_idx + 0]  # call 1 → cancelled on call 2
            h1 = self._fake_timers[cat_idx + 3]  # call 2 → cancelled on call 3
            h2 = self._fake_timers[cat_idx + 6]  # call 3 → NOT cancelled

            h0.cancel.assert_called_once()
            h1.cancel.assert_called_once()
            h2.cancel.assert_not_called()

        # ── Verify dict entries point to the LAST handle ─────────────────────
        for cat_idx, attr in enumerate(_CAT_DICT_ATTRS):
            d = getattr(self._tt, attr)
            stored_handle = d[self.PL_ID]
            expected_handle = self._fake_timers[cat_idx + 6]  # call 3
            assert stored_handle is expected_handle, (
                f"{attr}[pl_id] does not reference the 3rd (latest) "
                f"timer handle"
            )
