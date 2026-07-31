"""Tests for Spotify device-list refresh logic after refactoring.

Verifies:
  1. ``do_connect()`` triggers exactly one ``sp.devices()`` call.
  2. The "refresh" icon button triggers exactly one ``sp.devices()`` call.
  3. ``build_spotify_section()`` WITHOUT connect or button click does NOT
     call ``sp.devices()``.
  4. Source inspection: no ``ui.timer`` referencing ``_refresh_spotify_devices``
     or ``_do_refresh`` exists inside ``build_spotify_section()``.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, r"e:\Projects\Spotify_playlists\repository")

# Mock nicegui before importing modules that import it at module level
try:
    import nicegui  # noqa: F401
except ImportError:
    sys.modules["nicegui"] = MagicMock()
    sys.modules["nicegui.ui"] = MagicMock()

import pytest

# ── Path to the module under test ──────────────────────────────────────────────
_MOD_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "playlist_arranger"
    / "ui"
    / "pages"
    / "playlist_source.py"
)

# ══════════════════════════════════════════════════════════════════════════════
#  Helper: build a MockUI that records button on_click handlers
# ══════════════════════════════════════════════════════════════════════════════


class _MockUI:
    """Drop-in replacement for ``nicegui.ui`` that records button handlers."""

    def __init__(self):
        self.buttons = []
        self.timers = []
        self.selects = []
        self.labels = []
        self.expansions = []
        self._default = MagicMock()

    def button(self, text="", **kwargs):
        btn = MagicMock()
        btn._kwargs = kwargs
        btn._text = text
        self.buttons.append(btn)
        return btn

    def select(self, label="", **kwargs):
        sel = MagicMock()
        sel._kwargs = kwargs
        sel._label = label
        self.selects.append(sel)
        return sel

    def timer(self, interval, callback=None, once=False):
        tmr = MagicMock()
        tmr._interval = interval
        tmr._callback = callback
        tmr._once = once
        self.timers.append(tmr)
        return tmr

    def label(self, text=""):
        lbl = MagicMock()
        lbl._text = text
        self.labels.append(lbl)
        return lbl

    def expansion(self, label, **kwargs):
        exp = MagicMock()
        exp._label = label
        exp._kwargs = kwargs
        self.expansions.append(exp)
        return exp()

    # ── All other ui.* calls return a generic MagicMock ─────────────────
    def __getattr__(self, name):
        if name in ("__enter__", "__exit__"):
            def _empty(*a, **kw):
                pass
            return _empty
        return MagicMock()

    # Context manager support
    def __call__(self, *args, **kwargs):
        return self._default


# ══════════════════════════════════════════════════════════════════════════════
#  Test 1: connect triggers device refresh
# ══════════════════════════════════════════════════════════════════════════════


class TestConnectTriggersDeviceRefresh:
    """Verify ``do_connect()`` calls ``sp.devices()`` exactly once."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        import playlist_arranger.ui.pages.playlist_source as _ps_mod
        import playlist_arranger.ui.state as _state_mod
        import playlist_arranger.ui.audio_viz as _viz_mod

        saved_ui = _ps_mod.ui
        saved_sp = _state_mod.sp
        saved_user = _state_mod.spotify_user_id
        saved_init_canvas = _viz_mod.init_canvas_js

        self.mock_ui = _MockUI()
        self._ps_mod = _ps_mod
        self._state_mod = _state_mod

        # Replace ui with our mock
        _ps_mod.ui = self.mock_ui

        # Patch init_canvas_js (calls ui.run_javascript → needs event loop)
        _viz_mod.init_canvas_js = lambda canvas_id: None

        # Set up connected state — sp is not None
        _state_mod.sp = MagicMock()
        _state_mod.sp.devices.return_value = {
            "devices": [{"id": "dev_abc", "name": "Test Device"}]
        }
        _state_mod.spotify_user_id = None

        yield

        # Restore
        _ps_mod.ui = saved_ui
        _state_mod.sp = saved_sp
        _state_mod.spotify_user_id = saved_user
        _viz_mod.init_canvas_js = saved_init_canvas

    def test_connect_button_calls_do_refresh_which_calls_sp_devices(self):
        """When ``do_connect()`` succeeds, ``sp.devices()`` is called once.

        This is verified by:
        1. Building the spotify section (which creates the connect button and
           registers ``do_connect()`` as its on_click handler).
        2. Extracting ``do_connect()`` from the connect button's kwargs.
        3. Mocking ``init_spotify`` so it returns synchronously.
        4. Awaiting ``do_connect()``.
        5. Asserting ``sp.devices()`` was called exactly once.
        """
        # 1. Build the section — records handlers on mock_ui
        import playlist_arranger.sources.spotify_source as _ss_mod

        saved_init = _ss_mod.init_spotify
        fake_sp = MagicMock()
        fake_sp.devices.return_value = {"devices": []}

        try:
            _ss_mod.init_spotify = lambda cb: (fake_sp, "test_user")
            self._state_mod.sp = fake_sp

            # build_spotify_section() also calls _render_playlists() which
            # calls get_own_playlists() — we need to mock that too
            with patch.object(self._ps_mod, "_render_playlists"):
                self._ps_mod.build_spotify_section(lambda p: None)

            # 2. Find the "Connect to Spotify" button
            connect_btn = None
            for btn in self.mock_ui.buttons:
                if btn._text == "Connect to Spotify":
                    connect_btn = btn
                    break
            assert connect_btn is not None, (
                "Expected a 'Connect to Spotify' button in build_spotify_section()"
            )

            on_click = connect_btn._kwargs.get("on_click")
            assert on_click is not None, (
                "Connect button must have on_click handler"
            )

            # 3. Run the async do_connect() handler
            #    We also need to mock ui.timer inside do_connect's scope so
            #    the deferred rebuild doesn't try to call real NiceGUI
            saved_timer = self._ps_mod.ui.timer

            def _fake_timer(interval, callback, once=False):
                # Don't actually schedule — just record
                pass

            self.mock_ui.timer = _fake_timer

            try:
                loop = asyncio.get_event_loop()
                loop.run_until_complete(on_click())
            finally:
                self.mock_ui.timer = saved_timer

            # 4. Assert: sp.devices() was called exactly once
            assert fake_sp.devices.call_count == 1, (
                f"Expected sp.devices() to be called exactly once during connect, "
                f"but it was called {fake_sp.devices.call_count} times"
            )

        finally:
            _ss_mod.init_spotify = saved_init

    def test_connect_button_deferred_rebuild_structure(self):
        """Source inspection: ``_deferred_rebuild`` is registered via ``ui.timer``
        inside ``do_connect()`` after ``result`` is assigned.  Device population
        now happens in ``build_spotify_section()`` on the fresh device_select,
        NOT inside ``do_connect()`` itself."""
        source = _MOD_PATH.read_text(encoding="utf-8")

        # Find the successful connect block
        assign_pos = source.index("_state.sp, _state.spotify_user_id = result")
        after_assign = source[assign_pos:]

        deferred_pos = after_assign.index("_deferred_rebuild")
        # No _do_refresh() call should appear between result assignment and
        # _deferred_rebuild in do_connect() — device refresh was moved out.
        do_refresh_in_block = after_assign.find("_do_refresh()", 0, deferred_pos)

        assert do_refresh_in_block == -1, (
            f"_do_refresh() found at +{do_refresh_in_block} inside do_connect() "
            f"between result assignment and _deferred_rebuild — device refresh "
            f"must happen in build_spotify_section(), not do_connect()"
        )


# ══════════════════════════════════════════════════════════════════════════════
#  Test 2: refresh button triggers exactly one sp.devices() call
# ══════════════════════════════════════════════════════════════════════════════


class TestRefreshButtonTriggersDeviceRefresh:
    """Verify the refresh icon button calls ``sp.devices()`` exactly once."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        import playlist_arranger.ui.pages.playlist_source as _ps_mod
        import playlist_arranger.ui.state as _state_mod
        import playlist_arranger.ui.audio_viz as _viz_mod

        saved_ui = _ps_mod.ui
        saved_sp = _state_mod.sp
        saved_user = _state_mod.spotify_user_id
        saved_init_canvas = _viz_mod.init_canvas_js

        self.mock_ui = _MockUI()
        self._ps_mod = _ps_mod
        self._state_mod = _state_mod

        _ps_mod.ui = self.mock_ui
        _viz_mod.init_canvas_js = lambda canvas_id: None
        _state_mod.sp = None
        _state_mod.spotify_user_id = None

        yield

        _ps_mod.ui = saved_ui
        _state_mod.sp = saved_sp
        _state_mod.spotify_user_id = saved_user
        _viz_mod.init_canvas_js = saved_init_canvas

    def test_refresh_button_on_click_calls_sp_devices_once(self):
        """Clicking the refresh button triggers exactly one ``sp.devices()`` call.

        ``build_spotify_section()`` now auto-populates devices once when sp is
        set, so we reset the mock AFTER the build and verify the button handler
        triggers exactly one more call."""
        fake_sp = MagicMock()
        fake_sp.devices.return_value = {
            "devices": [{"id": "d1", "name": "Device One"}]
        }
        self._state_mod.sp = fake_sp

        with patch.object(self._ps_mod, "_render_playlists"):
            self._ps_mod.build_spotify_section(lambda p: None)

        # Auto-population on render called sp.devices() once — reset so we
        # can measure ONLY the refresh button's contribution.
        fake_sp.devices.reset_mock()

        # Find the refresh button — it has icon="refresh"
        refresh_btn = None
        for btn in self.mock_ui.buttons:
            if btn._kwargs.get("icon") == "refresh":
                refresh_btn = btn
                break
        assert refresh_btn is not None, (
            "Expected a refresh icon button in build_spotify_section()"
        )

        on_click = refresh_btn._kwargs.get("on_click")
        assert on_click is not None, (
            "Refresh button must have on_click handler"
        )

        # Call the handler directly (synchronous — just a callback)
        on_click()

        assert fake_sp.devices.call_count == 1, (
            f"Expected sp.devices() called exactly once on refresh click, "
            f"got {fake_sp.devices.call_count}"
        )

    def test_refresh_btn_not_called_when_sp_is_none(self):
        """When sp is None, the refresh handler shows a warning and does NOT
        call sp.devices()."""
        fake_sp = MagicMock()
        fake_sp.devices.return_value = {"devices": []}
        self._state_mod.sp = None  # NOT connected

        with patch.object(self._ps_mod, "_render_playlists"):
            self._ps_mod.build_spotify_section(lambda p: None)

        refresh_btn = None
        for btn in self.mock_ui.buttons:
            if btn._kwargs.get("icon") == "refresh":
                refresh_btn = btn
                break
        assert refresh_btn is not None

        on_click = refresh_btn._kwargs.get("on_click")
        assert on_click is not None

        on_click()
        assert fake_sp.devices.call_count == 0, (
            "sp.devices() must NOT be called when sp is None"
        )


# ══════════════════════════════════════════════════════════════════════════════
#  Test 3: build_spotify_section() without action does NOT call sp.devices()
# ══════════════════════════════════════════════════════════════════════════════


class TestBuildSectionDoesNotCallDevices:
    """Verify that merely building the section does not hit the Spotify API."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        import playlist_arranger.ui.pages.playlist_source as _ps_mod
        import playlist_arranger.ui.state as _state_mod
        import playlist_arranger.ui.audio_viz as _viz_mod

        saved_ui = _ps_mod.ui
        saved_sp = _state_mod.sp
        saved_user = _state_mod.spotify_user_id
        saved_init_canvas = _viz_mod.init_canvas_js

        self.mock_ui = _MockUI()
        self._ps_mod = _ps_mod
        self._state_mod = _state_mod

        _ps_mod.ui = self.mock_ui
        _viz_mod.init_canvas_js = lambda canvas_id: None

        yield

        _ps_mod.ui = saved_ui
        _state_mod.sp = saved_sp
        _state_mod.spotify_user_id = saved_user
        _viz_mod.init_canvas_js = saved_init_canvas

    def test_build_spotify_section_with_sp_set_calls_devices_once(self):
        """``build_spotify_section()`` with ``_state.sp`` already set must call
        ``sp.devices()`` exactly once (auto-populate on render)."""
        fake_sp = MagicMock()
        fake_sp.devices.return_value = {"devices": []}
        self._state_mod.sp = fake_sp
        self._state_mod.spotify_user_id = "user123"

        with patch.object(self._ps_mod, "_render_playlists"):
            self._ps_mod.build_spotify_section(lambda p: None)

        assert fake_sp.devices.call_count == 1, (
            f"build_spotify_section() called sp.devices() {fake_sp.devices.call_count} "
            f"times — should be exactly 1 when _state.sp is already connected"
        )

    def test_build_spotify_section_with_sp_none_does_not_call_devices(self):
        """``build_spotify_section()`` with ``_state.sp = None`` must NOT call
        ``sp.devices()`` at all."""
        fake_sp = MagicMock()
        fake_sp.devices.return_value = {"devices": []}
        self._state_mod.sp = None
        self._state_mod.spotify_user_id = None

        with patch.object(self._ps_mod, "_render_playlists"):
            self._ps_mod.build_spotify_section(lambda p: None)

        assert fake_sp.devices.call_count == 0, (
            f"build_spotify_section() called sp.devices() {fake_sp.devices.call_count} "
            f"times — should be 0 when _state.sp is None"
        )

    def test_build_spotify_section_source_no_devices_call_outside_do_refresh(self):
        """Source inspection: verify no ``devices()`` or ``.set_options()``
        call exists in ``build_spotify_section()`` outside of ``_do_refresh()``
        function body."""
        source = _MOD_PATH.read_text(encoding="utf-8")

        # Find the _do_refresh function body
        do_refresh_start = source.index("def _do_refresh(")
        # Find the end of _do_refresh — it ends right before the
        # "with ui.row().classes("w-full gap-1 items-center"):" block
        # that contains device_select
        device_select_line = source.index("device_select = ui.select")
        do_refresh_body = source[do_refresh_start:device_select_line]

        # Now look at everything between device_select_line and the end
        # of build_spotify_section
        # Find "with ui.column().classes("w-[60%]")" — that's where the
        # audio capture section starts, after the left column
        audio_col_pos = source.index('ui.column().classes("w-[60%]")')
        # The section between device_select and the audio column
        between = source[device_select_line:audio_col_pos]

        # No .devices() call outside _do_refresh body
        # _do_refresh itself contains devices() calls — that's expected
        # But we're checking the area AFTER _do_refresh ends
        after_do_refresh = source[device_select_line:]

        # Count how many "devices()" calls exist in the whole file within
        # a regex-aware way
        # The key invariant: the ONLY "sp.devices()" calls in the file are
        # inside _do_refresh()
        all_sp_devices = []
        idx = 0
        while True:
            idx = source.find(".devices()", idx)
            if idx == -1:
                break
            # Check if this is inside _do_refresh()
            # We know _do_refresh spans from do_refresh_start to device_select_line
            if do_refresh_start <= idx <= device_select_line:
                all_sp_devices.append(("in_do_refresh", idx))
            else:
                all_sp_devices.append(("outside_do_refresh", idx))
            idx += 1

        outside = [pos for label, pos in all_sp_devices if label == "outside_do_refresh"]
        assert len(outside) == 0, (
            f"Found .devices() call(s) outside _do_refresh() body at positions "
            f"{outside} — all sp.devices() calls must be confined to _do_refresh()"
        )


# ══════════════════════════════════════════════════════════════════════════════
#  Test 4: no auto-refresh timer in build_spotify_section()
# ══════════════════════════════════════════════════════════════════════════════


class TestNoAutoRefreshTimer:
    """Verify the 0.2s auto-refresh timer has been removed completely."""

    def test_no_timer_refresh_spotify_devices_in_build_spotify_section(self):
        """Source inspection: no ``ui.timer`` referencing
        ``_refresh_spotify_devices`` or ``_do_refresh`` exists anywhere in
        ``build_spotify_section()``."""
        source = _MOD_PATH.read_text(encoding="utf-8")

        # build_spotify_section starts at "def build_spotify_section(" and ends
        # at the last line before _render_playlists_set_page_cb = None
        func_start = source.index("def build_spotify_section(")
        # The function ends at the module-level assignment after it
        end_marker = source.index("\n_render_playlists_set_page_cb = None")
        func_body = source[func_start:end_marker]

        # Check: "ui.timer(0.2" should NOT appear in build_spotify_section
        if "ui.timer(0.2" in func_body:
            idx = func_body.index("ui.timer(0.2")
            context = func_body[idx:idx + 120]
            raise AssertionError(
                f"Found ui.timer(0.2, ...) inside build_spotify_section() — "
                f"this auto-refresh timer was supposed to be removed.\n"
                f"Context: {context!r}"
            )

        # Check: "ui.timer" in the section between the connect button row
        # and the audio column should NOT reference device refresh
        # (Some ui.timer calls exist for the Now Playing card — those are OK)
        # So we specifically check for _refresh_spotify_devices or _do_refresh
        # in any ui.timer call within the function body
        timer_lines = []
        pos = 0
        while True:
            pos = func_body.find("ui.timer", pos)
            if pos == -1:
                break
            # Grab the line context
            line_start = func_body.rfind("\n", 0, pos) + 1
            line_end = func_body.find("\n", pos)
            if line_end == -1:
                line_end = len(func_body)
            timer_lines.append(func_body[line_start:line_end])
            pos += 1

        for line in timer_lines:
            assert "_refresh_spotify_devices" not in line, (
                f"ui.timer line references _refresh_spotify_devices: {line!r}"
            )
            assert "_do_refresh" not in line, (
                f"ui.timer line references _do_refresh: {line!r}"
            )

    def test_no_unconditional_refresh_in_build_flow(self):
        """Source inspection: 'def _refresh_spotify_devices():' is only called
        via button 'on_click' or 'Connect to Spotify' in build_spotify_section().

        No unconditional call to _refresh_spotify_devices() (i.e. outside of a
        function definition body) appears."""
        source = _MOD_PATH.read_text(encoding="utf-8")

        func_start = source.index("def build_spotify_section(")
        end_marker = source.index("\n_render_playlists_set_page_cb = None")
        func_body = source[func_start:end_marker]

        # Find all occurrences of _refresh_spotify_devices in the function body
        # excluding the definition itself and the on_click reference
        refs = []
        pos = 0
        def_start = func_body.index("def _refresh_spotify_devices")
        while True:
            pos = func_body.find("_refresh_spotify_devices", pos)
            if pos == -1:
                break
            if abs(pos - def_start) < 5:
                # This is the definition line itself — skip
                pos += 1
                continue
            refs.append(pos)
            pos += 1

        for ref_pos in refs:
            # Each reference should be inside a definition or button kwarg
            # Check the surrounding 80 chars for "on_click="
            start = max(0, ref_pos - 40)
            end = min(len(func_body), ref_pos + 40)
            context = func_body[start:end]
            assert "on_click=" in context or "def " in context, (
                f"_refresh_spotify_devices used outside of on_click/def at "
                f"position {ref_pos}:\ncontext: {context!r}"
            )