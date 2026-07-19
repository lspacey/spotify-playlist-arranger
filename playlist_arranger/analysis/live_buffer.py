"""Live audio analysis buffer — continuous per-track capture + MERT embedding.

Source-agnostic: works with both Spotify live WASAPI capture and local-file
batch analysis. Caller injects the shared threading.Lock and mode-check
callable to ensure mutual exclusion with UI/caller threads.

Design: LiveAnalyzeContext owns all buffer state as instance attributes,
not module globals — so multiple concurrent contexts (e.g. one for Spotify
live capture, one for local file batch) don't interfere."""

import collections
import dataclasses
import threading
import time
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

# ─── Config constants (re-imported here to avoid circular deps) ────────────────
from playlist_arranger.config import (
    MIN_COVERAGE_PCT,
    MAX_ANALYZE_BUFFER_S,
)


# ─── Dataclass ─────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class AnalyzeBuffer:
    """Single-track audio buffer for MERT analysis.
    Holds growing numpy chunks for one track. Replaced entirely on track change."""
    track_id: str | None = None
    track_info: dict | None = None
    chunks: list = dataclasses.field(default_factory=list)
    samples_count: int = 0
    sample_rate: int = 22050
    submitted: bool = False  # True once this buffer has been sent for analysis (prevents double-flush)


# ─── Context class — owns all buffer state ────────────────────────────────────

class LiveAnalyzeContext:
    """All state + methods for live audio analysis buffering.

    The caller injects:
      - mode_lock: shared threading.Lock (same object used by caller's UI/poll threads)
      - is_analyze_mode: callable → bool (reads caller's _analyze_mode flag)
      - capture: the audio.capture module reference (for actual_sr/audio_deque etc.)
    """

    def __init__(self, mode_lock: threading.Lock, is_analyze_mode: Callable[[], bool], capture_module,
                 save_track_worker_fn: Callable | None = None):
        self.mode_lock = mode_lock
        self.is_analyze_mode = is_analyze_mode
        self.capture = capture_module
        # Optional injection of save_track_worker for test isolation.
        # When None (production), _worker_loop imports the real function.
        self._save_track_worker_fn = save_track_worker_fn

        # Buffer state
        self._analyze_buf: AnalyzeBuffer | None = None

        # Playback state — set by caller's polling thread (gates collect_samples during pause)
        self._is_playing = threading.Event()
        self._is_playing.set()  # default: playing (poller clears on pause, sets on resume)

        # Worker state — FIFO deque ensures NO collected audio data is ever
        # silently dropped. Every submitted task represents real, valid,
        # already-collected audio that must be processed and saved.
        self._analyze_worker_busy: bool = False
        self._analyze_worker_queue: collections.deque = collections.deque()
        self._analyze_worker_lock = threading.Lock()
        self._analyze_worker_thread: threading.Thread | None = None

        # Poll thread state
        self._analyze_poll_thread: threading.Thread | None = None
        self._analyze_poll_stop = threading.Event()

        # Track-change callback — settable by caller after construction
        # Signature: callable(old_track_info, new_track_info) → None
        self.on_track_changed_cb: Callable | None = None

        # Analysis-complete callback — fired after save_track_worker succeeds
        # Signature: callable(track_info: dict) → None
        # Used e.g. by UI to auto-remove analyzed tracks from the analysis queue.
        self.on_analysis_complete_cb: Callable | None = None

        # Buffer-submitted callback — fired immediately when buffer is submitted
        # to the async worker (before worker processing begins). Used by batch
        # mode to advance to next track without waiting for worker completion.
        # Signature: callable(track_info: dict) → None
        self.on_buffer_submitted_cb: Callable | None = None

        # Buffer-discarded callback — fired when buffer is skipped due to
        # insufficient coverage (< MIN_COVERAGE_PCT). Used by batch mode to
        # revert "⏳ Processing" status.
        # Signature: callable(track_info: dict) → None
        self.on_buffer_discarded_cb: Callable | None = None

    # ── Flush ──────────────────────────────────────────────────────────────────

    def _flush_analyze_buffer(self, buf: AnalyzeBuffer, coverage_pct: float) -> bool:
        """Flush a completed AnalyzeBuffer: concatenate chunks, submit if coverage is sufficient.
        
        CALLER MUST hold self.mode_lock — this method does not acquire it internally.
        This serializes with collect_samples() and sync_analyze_buffer() which
        also mutate _analyze_buf under mode_lock."""
        assert self.mode_lock.locked(), "CALLER MUST hold self.mode_lock before calling _flush_analyze_buffer()"
        import numpy as np
        if not buf.chunks:
            logger.debug("Flush skipped: empty buffer")
            return False
        y_full = np.concatenate(buf.chunks)
        duration_s = buf.samples_count / max(buf.sample_rate, 1)
        logger.debug("Flushing analyze buffer: track=%s, coverage=%.1f%%, duration=%.1fs",
                     buf.track_info.get("name", "")[:30] if buf.track_info else "?",
                     coverage_pct * 100, duration_s)
        if coverage_pct >= MIN_COVERAGE_PCT:
            self._submit_analyze_task(buf.track_info, y_full)
            logger.info("Track complete: coverage=%.1f%%, submitting for analysis: %s",
                        coverage_pct * 100, buf.track_info.get("name", "")[:50] if buf.track_info else "?")
            # Mark submitted BEFORE firing callback (prevents double-flush in
            # concurrent paths like flush_before_stop + sync_analyze_buffer).
            buf.submitted = True
            # Fire buffer-submitted callback (batch mode advances immediately)
            cb = self.on_buffer_submitted_cb
            if cb:
                try:
                    cb(buf.track_info)
                except Exception:
                    logger.exception("on_buffer_submitted_cb crashed for %s",
                                     buf.track_info.get("name", "?")[:30] if buf.track_info else "?")
            return True
        else:
            dur_ms = buf.track_info.get("duration_ms", 0) if buf.track_info else 0
            expected_s = dur_ms / 1000.0 if dur_ms else 0
            logger.info("Track skipped: insufficient coverage=%.1f%% (<%.0f%% threshold): %s (captured %.1fs / expected %.1fs)",
                        coverage_pct * 100, MIN_COVERAGE_PCT * 100,
                        buf.track_info.get("name", "")[:50] if buf.track_info else "?",
                        duration_s, expected_s)
            # Fire buffer-discarded callback (batch mode reverts Processing status)
            cb = self.on_buffer_discarded_cb
            if cb:
                try:
                    cb(buf.track_info)
                except Exception:
                    logger.exception("on_buffer_discarded_cb crashed for %s",
                                     buf.track_info.get("name", "?")[:30] if buf.track_info else "?")
            return False

    # ── Sync / buffer lifecycle ────────────────────────────────────────────────

    def sync_analyze_buffer(self, current_track_info: dict | None):
        """Ensure the analyze buffer matches the currently playing track.

        Called whenever we observe what's currently playing (None if nothing is
        playing). Flushes/discards the old buffer if the track changed, and starts
        a new buffer if a track is now playing.

        Holds self.mode_lock for the entire body — serializes with collect_samples().
        """
        with self.mode_lock:
            if not self.is_analyze_mode():
                return

            current_id = current_track_info.get("id") if current_track_info else None

            # ── Same track still playing — check early flush via coverage ──────
            if self._analyze_buf is not None and self._analyze_buf.track_id == current_id and current_id is not None:
                if not self._analyze_buf.submitted:
                    dur_ms = self._analyze_buf.track_info.get("duration_ms", 0) if self._analyze_buf.track_info else 0
                    expected_samples = int(self._analyze_buf.sample_rate * dur_ms / 1000.0) if dur_ms else 0
                    coverage = self._analyze_buf.samples_count / expected_samples if expected_samples > 0 else 0.0
                    if coverage >= MIN_COVERAGE_PCT:
                        logger.debug(
                            "sync: early flush — same track reached sufficient coverage "
                            "(%.1f%% >= %.0f%% target) for %s",
                            coverage * 100, MIN_COVERAGE_PCT * 100,
                            self._analyze_buf.track_info.get("name", "?")[:30] if self._analyze_buf.track_info else "?",
                        )
                        self._flush_analyze_buffer(self._analyze_buf, coverage)
                        self._analyze_buf.submitted = True

            # ── Track changed (or playback stopped) — close out old buffer ─────
            elif self._analyze_buf is not None and self._analyze_buf.track_id != current_id:
                if not self._analyze_buf.submitted:
                    old_dur_ms = self._analyze_buf.track_info.get("duration_ms", 0) if self._analyze_buf.track_info else 0
                    expected_samples = int(self._analyze_buf.sample_rate * old_dur_ms / 1000.0) if old_dur_ms else 0
                    coverage = self._analyze_buf.samples_count / expected_samples if expected_samples > 0 else 0.0
                    logger.debug(
                        "sync: track changed — flushing buffer for %s (id=%s, samples=%d, coverage=%.1f%%)",
                        self._analyze_buf.track_info.get("name", "?")[:30] if self._analyze_buf.track_info else "?",
                        (self._analyze_buf.track_id or "?")[:8],
                        self._analyze_buf.samples_count,
                        coverage * 100,
                    )
                    self._flush_analyze_buffer(self._analyze_buf, coverage)
                else:
                    logger.debug("sync: track changed — buffer already submitted for %s, discarding without re-flush",
                                 self._analyze_buf.track_info.get("name", "?")[:30] if self._analyze_buf.track_info else "?")
                self._analyze_buf = None

            # ── Playback stopped (current_track_info is None) — drain buffer ───
            if self._analyze_buf is not None and current_track_info is None:
                if not self._analyze_buf.submitted:
                    dur_ms = self._analyze_buf.track_info.get("duration_ms", 0) if self._analyze_buf.track_info else 0
                    expected_samples = int(self._analyze_buf.sample_rate * dur_ms / 1000.0) if dur_ms else 0
                    coverage = self._analyze_buf.samples_count / expected_samples if expected_samples > 0 else 0.0
                    logger.debug(
                        "sync: playback stopped — flushing buffer for %s (coverage=%.1f%%)",
                        self._analyze_buf.track_info.get("name", "?")[:30] if self._analyze_buf.track_info else "?",
                        coverage * 100,
                    )
                    self._flush_analyze_buffer(self._analyze_buf, coverage)
                self._analyze_buf = None

            # ── Start a new buffer if a track is now playing and we don't have one
            if self._analyze_buf is None and current_track_info is not None:
                cap = self.capture
                self._analyze_buf = AnalyzeBuffer(
                    track_id=current_id,
                    track_info=current_track_info,
                    sample_rate=cap.actual_sr,
                )
                logger.debug("AnalyzeBuffer created: buf.sample_rate=%d, live cap.actual_sr=%d",
                             self._analyze_buf.sample_rate, cap.actual_sr)
                assert self._analyze_buf.sample_rate == cap.actual_sr, "sample_rate drifted from live capture rate"
                logger.debug("Analyze buffer started: track=%s id=%s",
                             current_track_info.get("name", "?")[:30],
                             (current_id or "?")[:8])

    def on_track_changed(self, old_track_info, new_track_info):
        """Called on every track transition by the polling thread."""
        self.sync_analyze_buffer(new_track_info)
        cb = self.on_track_changed_cb
        if cb:
            try:
                cb(old_track_info, new_track_info)
            except Exception:
                logger.exception("on_track_changed_cb crashed")

    # ── Audio sample collection ────────────────────────────────────────────────

    def set_playing(self, playing: bool):
        """Call from polling thread: True when Spotify reports is_playing, False otherwise."""
        if playing:
            self._is_playing.set()
        else:
            self._is_playing.clear()

    def collect_samples(self, new_mono_samples):
        """Add audio samples to the current analyze buffer.
        Gated on playback state (self._is_playing) AND analyze mode.
        Capped at MAX_ANALYZE_BUFFER_S.

        Holds self.mode_lock only for the read-check + append."""

        if not self._is_playing.is_set():
            return

        with self.mode_lock:
            if self._analyze_buf is None:
                return

            # Stop collecting once this buffer has been submitted for analysis —
            # prevents unbounded growth on loops/seeks after early flush
            if self._analyze_buf.submitted:
                return

            current_duration_s = (self._analyze_buf.samples_count + len(new_mono_samples)) / self._analyze_buf.sample_rate
            if current_duration_s > MAX_ANALYZE_BUFFER_S:
                logger.warning("Analyze buffer capped at %ds (max %ds)", int(current_duration_s), MAX_ANALYZE_BUFFER_S)
                return

            self._analyze_buf.chunks.append(new_mono_samples)
            self._analyze_buf.samples_count += len(new_mono_samples)

    # ── Async processing worker ────────────────────────────────────────────────

    def _worker_loop(self):
        """Single long-lived thread: drains the FIFO queue completely,
        processing every submitted task. NEVER drops any task — every
        enqueued task represents real, already-collected audio data."""
        # Use injected function if provided (test isolation), otherwise
        # import the real save_track_worker (production).
        if self._save_track_worker_fn is not None:
            save_track_worker = self._save_track_worker_fn
        else:
            from playlist_arranger.analysis.worker import save_track_worker  # noqa: F811

        while True:
            task = None
            while task is None:
                with self._analyze_worker_lock:
                    if self._analyze_worker_queue:
                        task = self._analyze_worker_queue.popleft()
                    else:
                        self._analyze_worker_busy = False
                if task is None:
                    time.sleep(0.5)
                    continue

            track_info = task["track_info"]
            y_full = task["y_full"]
            try:
                t0 = time.time()
                save_track_worker(track_info=track_info, playlist_name="Now Playing", playlist_uri="",
                                  y_full=y_full, status_cb=lambda msg: logger.info("NP: %s", msg))
                elapsed = time.time() - t0
                audio_s = len(y_full) / max(22050, 1)
                logger.info("Analysis complete: %s, %ds audio, took %.2fs",
                            track_info.get("name", "?")[:50], int(audio_s), elapsed)

                # Fire analysis-complete callback (e.g. auto-remove from queue)
                cb = self.on_analysis_complete_cb
                if cb:
                    try:
                        cb(track_info)
                    except Exception:
                        logger.exception("on_analysis_complete_cb crashed for %s",
                                         track_info.get("name", "?")[:30])

            except Exception:
                logger.exception("Analyze worker failed for %s", track_info.get("name", "?"))

    def _submit_analyze_task(self, track_info, y_full):
        """Enqueue an analyze task for the async worker.
        
        Uses a FIFO deque — every submitted task represents real, valid,
        already-collected audio data. No task is ever silently dropped."""
        with self._analyze_worker_lock:
            self._analyze_worker_queue.append({"track_info": track_info, "y_full": y_full})
            n = len(self._analyze_worker_queue)
            if n > 1:
                logger.info("Analyze worker queue: %d pending tasks (appended '%s')",
                            n, track_info.get("name", "?")[:30] if track_info else "?")
            if not self._analyze_worker_busy:
                self._analyze_worker_busy = True
                self._analyze_worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
                self._analyze_worker_thread.start()

    # ── Poll thread (WASAPI → buffer feeder) ───────────────────────────────────

    def _poll_thread_fn(self):
        """Continuous audio buffer feeder. Reads from WASAPI deque."""
        import numpy as np
        from playlist_arranger.audio.features import _to_mono

        while not self._analyze_poll_stop.is_set():
            am = self.is_analyze_mode()
            if not am:
                time.sleep(0.5)
                continue
            cap = self.capture
            if cap.audio_deque is not None:
                with cap.audio_lock:
                    new_samples = np.array(list(cap.audio_deque), dtype=np.float32)
                    cap.audio_deque.clear()
                if len(new_samples) > 0:
                    mono = _to_mono(new_samples, cap.actual_channels)
                    self.collect_samples(mono)
            time.sleep(0.5)

    def start_poll_thread(self):
        """Start the WASAPI → buffer feeding thread."""
        if self._analyze_poll_thread is not None and self._analyze_poll_thread.is_alive():
            return
        self._analyze_poll_stop.clear()
        self._analyze_poll_thread = threading.Thread(target=self._poll_thread_fn, daemon=True)
        self._analyze_poll_thread.start()
        logger.info("Analyze poll thread started")

    def stop_poll_thread(self):
        """Stop the WASAPI → buffer feeding thread.
        
        Does NOT discard the buffer — the caller must call flush_before_stop()
        first to flush any collected audio data before calling this."""
        self._analyze_poll_stop.set()
        logger.info("Analyze poll thread stopped")

    def flush_before_stop(self):
        """Flush current buffer if unsubmitted (for manual Stop button).
        
        Holds self.mode_lock to serialize with collect_samples() and
        sync_analyze_buffer() — prevents races where the poll thread
        is mid-write to _analyze_buf while we flush."""
        with self.mode_lock:
            if self._analyze_buf is not None and not self._analyze_buf.submitted:
                dur_ms = self._analyze_buf.track_info.get("duration_ms", 0) if self._analyze_buf.track_info else 0
                expected_samples = int(self._analyze_buf.sample_rate * dur_ms / 1000.0) if dur_ms else 0
                coverage = self._analyze_buf.samples_count / expected_samples if expected_samples > 0 else 0.0
                self._flush_analyze_buffer(self._analyze_buf, coverage)
            self._analyze_buf = None

    @property
    def analyze_buf(self):
        """Raw unprotected buffer reference.

        CALLER MUST hold self.mode_lock for the ENTIRE read + any
        subsequent attribute access (e.g. buf.track_id, buf.submitted) —
        this property does NOT lock internally (would deadlock on
        non-reentrant Lock re-acquire)."""
        return self._analyze_buf

    def get_locked_buf(self):
        """Returns buf only if caller holds mode_lock. Raises AssertionError otherwise.

        Use in production code instead of the bare .analyze_buf property
        to catch locking bugs early during development/testing."""
        assert self.mode_lock.locked(), "get_locked_buf() called without holding mode_lock"
        return self._analyze_buf
