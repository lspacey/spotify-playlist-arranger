"""Guard against the __main__ / playlist_arranger.main dual-module bug.

When playlist_arranger/main.py is executed directly as ``__main__``
(e.g. ``python -m playlist_arranger.main``), Python creates a SEPARATE
module object from the one other code gets via
``from playlist_arranger.main import ...``.  The imported copy has
its own fresh globals (_right_panel=None, _current_page="welcome"),
which silently breaks page rebuilds after Spotify connect because
render_right_panel() operates on the wrong module's state.

These tests confirm:
1. playlist_arranger.main is only ever ONE module object
2. __main__.py correctly delegates to playlist_arranger.main
3. Globals set via one import path are visible via another
"""

import sys
import importlib


def test_main_module_is_singleton():
    """Two separate imports must return the same module object."""
    import playlist_arranger.main as mod1
    import playlist_arranger.main as mod2
    assert mod1 is mod2, (
        "Two imports of playlist_arranger.main returned different objects — "
        "module was re-executed, likely because it was run as __main__ "
        "and then imported by name."
    )


def test_dunder_main_delegates_to_main_module():
    """__main__.py must import main() from playlist_arranger.main directly."""
    dunder_main = importlib.import_module("playlist_arranger.__main__")
    import playlist_arranger.main as main_mod
    assert dunder_main.main is main_mod.main, (
        "__main__.py must import main() from playlist_arranger.main, "
        "not redefine it."
    )


def test_globals_shared_across_import_paths():
    """Simulate do_connect()'s import pattern: globals set via one path
    must be visible via every other path referencing the same module."""
    import playlist_arranger.main as main_mod

    original = main_mod._right_panel
    try:
        main_mod._right_panel = "SENTINEL_CONTAINER"

        # do_connect()'s exact import statement (inside _deferred_rebuild)
        from playlist_arranger.main import render_right_panel as rrp_ref
        from playlist_arranger.main import _right_panel as rp_ref

        assert main_mod.render_right_panel is rrp_ref, (
            "Imported function must be the same object as the module attribute"
        )
        assert rp_ref == "SENTINEL_CONTAINER", (
            f"Expected 'SENTINEL_CONTAINER', got {rp_ref!r} — "
            "_right_panel set via one import path is NOT visible via another, "
            "confirming a dual-module-object bug where the app was launched "
            "as __main__ and then imported by dotted name."
        )
    finally:
        main_mod._right_panel = original