"""pytest session-level fixtures — diagnostics, cleanup, and anti-hang guards."""

import pathlib
import sys
import threading
import logging

import pytest

logger = logging.getLogger(__name__)

# ── Repo-root discovery (works regardless of cwd) ────────────────────────────
_repo_root = pathlib.Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))


def pytest_sessionfinish(session, exitstatus):
    """Run after ALL tests complete.  FAIL the session if any non-daemon
    thread (other than MainThread) is still alive — this prevents silent
    process hangs in CI/automated runs and catches missing-thread-teardown
    bugs at the moment they're introduced, not when someone notices the
    terminal never returns to the shell prompt."""
    live_threads = threading.enumerate()
    non_daemon = [t for t in live_threads if not t.daemon]
    daemon = [t for t in live_threads if t.daemon]

    logger.info(
        "Test session finished (exit=%d).  Threads alive: %d total, "
        "%d non-daemon, %d daemon.",
        exitstatus, len(live_threads), len(non_daemon), len(daemon),
    )

    # asyncio event-loop helper threads are created by the standard library
    # (not user code) and are cleaned up at interpreter shutdown — tolerate them.
    _KNOWN_NON_LEAK_THREAD_NAMES = frozenset(
        {"asyncio_0", "pydevd.CommandThread", "pydevd.Reader",
         "pydevd.Writer", "StdErr"}
    )
    extra = [
        t for t in non_daemon
        if t is not threading.main_thread()
        and t.name not in _KNOWN_NON_LEAK_THREAD_NAMES
    ]
    if extra:
        names = [(t.name, type(t).__name__) for t in extra]
        thread_pids = []
        for t in extra:
            try:
                thread_pids.append(f"{t.name}: is_alive={t.is_alive()}, "
                                   f"daemon={t.daemon}, ident={t.ident}")
            except Exception:
                thread_pids.append(f"{t.name}: <error reading state>")
        msg = (
            f"THREAD LEAK DETECTED: {len(extra)} non-daemon thread(s) still "
            f"alive after test session — this will block process exit.\n"
            + "\n".join(thread_pids)
        )
        logger.error(msg)
        # Fail the session so this doesn't go unnoticed in CI.
        # pytest.exit() raises SystemExit(1) after all teardown completes.
        pytest.exit(msg, returncode=1)


# Re-export for tests that want the repo-root without hardcoding paths.
def _repo_path() -> pathlib.Path:
    return _repo_root
