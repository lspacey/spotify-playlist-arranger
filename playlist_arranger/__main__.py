"""Process entry point for ``python -m playlist_arranger``.

Deliberately thin — all real logic lives in ``playlist_arranger.main``,
which must ONLY ever be imported via its normal dotted package path
(never executed directly as ``__main__``).  Running ``main.py`` directly
causes it to be loaded as a SECOND independent module object whenever
other code does ``from playlist_arranger.main import ...``, producing
duplicate module-level initialization and desynchronised global state
(the 2026-07-28 "Not connected" after Spotify Connect bug).
"""

from playlist_arranger.main import main

if __name__ == "__main__":
    main()