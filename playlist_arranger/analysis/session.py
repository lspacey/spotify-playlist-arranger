"""AnalysisSession — manages sequential playback + analysis of missing tracks."""

import time
import threading
import numpy as np

from playlist_arranger.config import POLL_FAST
from playlist_arranger.audio import capture as _cap
from playlist_arranger.audio.features import _to_mono
from playlist_arranger.analysis.worker import save_track_worker

try:
    import spotipy

    HAS_SPOTIPY = True
except ImportError:
    HAS_SPOTIPY = False


class AnalysisSession:
    """Manages sequential playback + analysis of missing tracks."""

    def __init__(
        self,
        sp,
        tracks,
        playlist_name,
        playlist_uri,
        spotify_device_id,
        progress_cb=None,
        on_track_done=None,
    ):
        self.sp = sp
        self.tracks = tracks
        self.playlist_name = playlist_name
        self.playlist_uri = playlist_uri
        self.spotify_device_id = spotify_device_id
        self.progress_cb = progress_cb  # callable(msg: str)
        self.on_track_done = on_track_done  # callable(track_id: str)
        self._stop = threading.Event()

    def _log(self, msg: str) -> None:
        if self.progress_cb:
            self.progress_cb(msg)

    def _retry_after(self, exc):
        try:
            h = getattr(exc, "headers", None) or {}
            return int(h.get("Retry-After", 30)) + 1
        except Exception:
            return 31

    def _wait_start(self, track_id, timeout=30.0):
        """Poll API until this track is confirmed playing."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                cp = self.sp.current_playback()
                if (
                    cp
                    and (cp.get("item") or {}).get("id") == track_id
                    and cp.get("is_playing")
                ):
                    return True, cp.get("progress_ms", 0)
            except spotipy.exceptions.SpotifyException as exc:
                if exc.http_status == 429:
                    time.sleep(self._retry_after(exc))
                    continue
            time.sleep(POLL_FAST)
        return False, 0

    def _wait_for_end(self, duration_ms, start_wall, start_offset_ms):
        """Wait until the track finishes using wall-clock time only."""
        dm, ds = divmod(duration_ms // 1000, 60)
        while not self._stop.is_set():
            elapsed_ms = int((time.time() - start_wall) * 1000) + start_offset_ms
            elapsed_ms = min(elapsed_ms, duration_ms)
            remaining_s = max(0, (duration_ms - elapsed_ms) // 1000)
            em, es = divmod(elapsed_ms // 1000, 60)
            pct = elapsed_ms / max(duration_ms, 1) * 100
            self._log(
                f"▶ {em}:{es:02d} / {dm}:{ds:02d}  ({pct:.0f}%)  ~{remaining_s}s left"
            )
            if elapsed_ms >= duration_ms:
                return True
            time.sleep(min(1.0, remaining_s + 0.2))
        return False

    def run(self):
        from playlist_arranger.sources.spotify_source import play_track_on_device

        total = len(self.tracks)
        self._log(f"Analyzing {total} missing track(s)")

        for idx, track in enumerate(self.tracks, 1):
            if self._stop.is_set():
                break

            self._log(f"[{idx}/{total}] {track['name']} — {track['artist']}")

            # Reset audio buffers
            with _cap.full_buf_lock:
                _cap.full_buf = None
            with _cap.start_buf_lock:
                _cap.start_buf = None
                _cap.start_buf_done = False

            # Start playback
            try:
                play_track_on_device(
                    self.sp,
                    f"spotify:track:{track['id']}",
                    self.spotify_device_id,
                )
            except RuntimeError as exc:
                self._log(f"{exc} — skipping")
                time.sleep(2.0)
                continue

            started, offset_ms = self._wait_start(track["id"])
            if not started:
                self._log("Did not start within 30s — skipping")
                continue

            start_wall = time.time() - offset_ms / 1000.0
            dur_ms = track["duration_ms"]

            if not self._wait_for_end(dur_ms, start_wall, offset_ms):
                # stopped by Ctrl+C
                break

            # Snapshot audio
            with _cap.full_buf_lock:
                y_snap = np.array(_cap.full_buf) if _cap.full_buf is not None else None
            with _cap.start_buf_lock:
                y_start_snap = (
                    np.array(_cap.start_buf)
                    if _cap.start_buf is not None
                    else None
                )
            with _cap.audio_lock:
                y_live = _to_mono(
                    np.array(_cap.audio_deque, dtype=np.float32),
                    _cap.actual_channels,
                )

            y_save = (
                y_snap
                if (y_snap is not None and len(y_snap) > _cap.actual_sr * 5)
                else y_live
            )
            if y_save is None or len(y_save) < _cap.actual_sr * 5:
                self._log("Not enough audio — skipping")
                continue

            save_track_worker(
                track_info=track,
                playlist_name=self.playlist_name,
                playlist_uri=self.playlist_uri,
                y_full=y_save,
                y_start_snap=y_start_snap,
                status_cb=self._log,
            )
            self._log("Done")

            if self.on_track_done:
                self.on_track_done(track["id"])

            if idx < total and not self._stop.is_set():
                time.sleep(1.5)

        self._log("Analysis session complete!")

    def stop(self):
        self._stop.set()