"""AutoMix editor audio: one transition's window, rendered alone, and each track's waveform.

The editor never renders the playlist. An edit re-plans on the UI thread
(``compile_automix``, CPU only) and this controller renders just the selected
junction's window (``progressive.render_window``: the same clips, ramp and
transition as the full mix, cut to a run-up and a tail) on a worker thread,
to a FLAC at the playlist's preview level. Requests are debounced, every
worker result carries the generation it was started for (a newer request
makes it stale), and finished windows are cached by what they contain, so
undoing back to a heard version plays at once. Export renders the same plan
from the same overrides; nothing here is ever used for it.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from collections import OrderedDict
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from PySide6.QtCore import QObject, QThread, QTimer, Signal

from app.automix.cache import BoundedMemo, canonical_media_path
from app.automix.progressive import render_window
from app.automix.renderer import AutoMixAudioPipeline, AutoMixRenderCancelled, AutoMixRenderError
from app.controllers.progressive_automix_controller import playlist_gain
from app.models.playlist import PlaylistTrack
from app.utils.qt_worker_lifecycle import stop_qthread_now
from app.utils.subprocess_utils import hidden_process_kwargs

LOGGER = logging.getLogger(__name__)

PEAKS_PER_SECOND = 200
"""Waveform resolution: a (min, max) pair per 5 ms, enough for a few seconds across a wide window."""
_PEAK_SAMPLE_RATE = 8000
CACHE_WINDOWS = 12
"""Rendered windows kept (each a few MB of FLAC); the oldest is deleted first."""

IDLE, WAITING, RENDERING, READY, FAILED, UNAVAILABLE = (
    "idle", "waiting", "rendering", "ready", "failed", "unavailable")


def file_identity(path: str) -> tuple[str, int, int] | None:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return canonical_media_path(path), stat.st_size, stat.st_mtime_ns


_PEAKS = BoundedMemo(64)


def track_peaks(executable: Path, path: str) -> np.ndarray | None:
    """``(n, 2)`` float32 (min, max) per 1/PEAKS_PER_SECOND s of ``path``'s mono mix; None if unreadable."""
    identity = file_identity(path)
    if identity is None:
        return None
    known = _PEAKS.get(identity)
    if known is not None:
        return known
    try:
        completed = subprocess.run(
            [str(executable), "-hide_banner", "-loglevel", "error", "-nostdin", "-i", path,
             "-vn", "-ac", "1", "-ar", str(_PEAK_SAMPLE_RATE), "-f", "s16le", "-"],
            capture_output=True, timeout=120, **hidden_process_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as error:
        LOGGER.warning("Waveform decode failed for %s: %s", path, error)
        return None
    if completed.returncode != 0:
        return None
    samples = np.frombuffer(completed.stdout, dtype="<i2").astype(np.float32) / 32768.0
    bucket = _PEAK_SAMPLE_RATE // PEAKS_PER_SECOND
    usable = len(samples) // bucket * bucket
    if usable == 0:
        return None
    blocks = samples[:usable].reshape(-1, bucket)
    peaks = np.stack((blocks.min(axis=1), blocks.max(axis=1)), axis=1)
    _PEAKS.put(identity, peaks)
    return peaks


class _PeaksWorker(QThread):
    ready = Signal(str, object)

    def __init__(self, executable: Path, tracks: list[tuple[str, str]], cancel: threading.Event,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._executable, self._tracks, self._cancel = executable, tracks, cancel

    def run(self) -> None:
        for track_id, path in self._tracks:
            if self._cancel.is_set():
                return
            self.ready.emit(track_id, track_peaks(self._executable, path))


class _WindowWorker(QThread):
    ready = Signal(int, object, str, float, float, float, float)
    """generation, cache key, path, file origin, window start, window end (timeline seconds), gain."""
    failed = Signal(int, str)

    def __init__(self, executable: Path, plan, index: int, tracks: list[PlaylistTrack], key: object,
                 directory: Path, generation: int, cancel: threading.Event, gain: float | None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._executable, self._plan, self._index, self._tracks = executable, plan, index, tracks
        self._key, self._directory, self._generation = key, directory, generation
        self._cancel, self._gain = cancel, gain

    def run(self) -> None:
        try:
            gain = self._gain if self._gain is not None else playlist_gain(
                self._executable, self._tracks, self._cancel)
            if gain is None or self._cancel.is_set():
                return
            window = render_window(self._plan, self._index, gain)
            prepared = AutoMixAudioPipeline(self._executable).render(
                window.plan, {track.id: track.file_path for track in self._tracks}, self._directory,
                cancel_event=self._cancel, container="flac", resting_dsp=window.resting_dsp,
            )
        except AutoMixRenderCancelled:
            return
        except (AutoMixRenderError, OSError, ValueError) as error:
            if not self._cancel.is_set():
                self.failed.emit(self._generation, str(error))
            return
        if not self._cancel.is_set():
            self.ready.emit(self._generation, self._key, str(prepared.path), window.origin, window.start,
                            window.end, gain)


class TransitionAuditionController(QObject):
    """Renders the selected junction's window on request (see module docstring)."""

    state_changed = Signal(str, str)
    """(state: IDLE/WAITING/RENDERING/READY/FAILED/UNAVAILABLE, detail such as an error message)."""
    audio_ready = Signal(str, float, float, float)
    """(file, origin, start, end): file second t plays timeline second origin + t; start..end is the window."""
    peaks_ready = Signal(str, object)
    """(track id, waveform peaks or None)."""

    DEBOUNCE_MS = 350

    def __init__(self, executable: Path | None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._executable = Path(executable) if executable is not None else None
        self._temp: TemporaryDirectory | None = None
        self._generation = 0
        self._cancel = threading.Event()
        self._peaks_cancel = threading.Event()
        self._workers: set[QThread] = set()
        self._pending: tuple[object, object, int, list[PlaylistTrack]] | None = None
        self._cache: OrderedDict[object, tuple[str, float, float, float]] = OrderedDict()
        self._gain: float | None = None
        self._loaded_peaks: set[str] = set()
        self._closed = False
        self.state = IDLE
        self.render_count = 0
        """Window renders started (diagnostics/tests)."""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.DEBOUNCE_MS)
        self._timer.timeout.connect(self._start)

    @property
    def available(self) -> bool:
        return self._executable is not None

    def request(self, plan, index: int, tracks: list[PlaylistTrack]) -> None:
        """Audition junction ``index`` of ``plan``: cached at once, else rendered after the debounce."""
        if self._closed:
            return
        if self._executable is None:
            self._set_state(UNAVAILABLE)
            return
        window = render_window(plan, index, 1.0)
        paths = {track.id: track.file_path for track in tracks}
        key = (repr(window), tuple(file_identity(paths.get(clip.track_id, "")) for clip in window.plan.clips))
        cached = self._cache.get(key)
        if cached is not None and Path(cached[0]).is_file():
            self._cache.move_to_end(key)
            self._supersede()
            self._pending = None
            self._set_state(READY)
            self.audio_ready.emit(*cached)
            return
        self._supersede()
        self._pending = (key, plan, index, list(tracks))
        self._set_state(WAITING)
        self._timer.start()

    def flush(self) -> None:
        """Start a debounced render now (e.g. the user pressed play)."""
        if self._timer.isActive():
            self._timer.stop()
            self._start()

    def retry(self) -> None:
        if self._pending is not None and self.state == FAILED:
            self._start()

    def load_peaks(self, tracks: list[PlaylistTrack]) -> None:
        """Waveforms for ``tracks`` not loaded yet, one worker at a time (memoized per file)."""
        if self._executable is None or self._closed:
            return
        wanted = [(track.id, track.file_path) for track in tracks if track.id not in self._loaded_peaks]
        if not wanted:
            return
        self._loaded_peaks.update(track_id for track_id, _path in wanted)
        worker = _PeaksWorker(self._executable, wanted, self._peaks_cancel, self)
        worker.ready.connect(self.peaks_ready)
        self._run(worker)

    def shutdown(self) -> None:
        """Cancel everything, wait for the workers, delete every rendered window."""
        self._closed = True
        self._timer.stop()
        self._supersede()
        self._peaks_cancel.set()
        workers, self._workers = list(self._workers), set()
        for worker in workers:
            stop_qthread_now(worker)
        self._cache.clear()
        if self._temp is not None:
            self._temp.cleanup()
            self._temp = None

    # -- internals -------------------------------------------------------------

    def _supersede(self) -> None:
        """Make any running render stale and stop it."""
        self._generation += 1
        self._cancel.set()
        self._cancel = threading.Event()

    def _set_state(self, state: str, detail: str = "") -> None:
        self.state = state
        self.state_changed.emit(state, detail)

    def _run(self, worker: QThread) -> None:
        self._workers.add(worker)
        worker.finished.connect(lambda worker=worker: self._release(worker))
        worker.start()

    def _release(self, worker: QThread) -> None:
        if worker in self._workers:
            self._workers.discard(worker)
            worker.deleteLater()

    def _start(self) -> None:
        if self._pending is None or self._closed:
            return
        key, plan, index, tracks = self._pending
        self._supersede()
        self.render_count += 1
        if self._temp is None:
            self._temp = TemporaryDirectory(prefix="automix-audition-", ignore_cleanup_errors=True)
        directory = Path(self._temp.name) / f"window-{self.render_count}"
        worker = _WindowWorker(self._executable, plan, index, tracks, key, directory,
                               self._generation, self._cancel, self._gain, self)
        worker.ready.connect(self._on_ready)
        worker.failed.connect(self._on_failed)
        self._set_state(RENDERING)
        self._run(worker)

    def _on_ready(self, generation: int, key: object, path: str, origin: float, start: float, end: float,
                  gain: float) -> None:
        if self._closed:
            return
        self._gain = gain
        self._cache[key] = (path, origin, start, end)  # valid for its key even when stale
        while len(self._cache) > CACHE_WINDOWS:
            _key, (old_path, *_times) = self._cache.popitem(last=False)
            shutil.rmtree(Path(old_path).parent, ignore_errors=True)
        if generation != self._generation:
            return
        self._pending = None
        self._set_state(READY)
        self.audio_ready.emit(path, origin, start, end)

    def _on_failed(self, generation: int, message: str) -> None:
        if generation == self._generation and not self._closed:
            LOGGER.warning("AutoMix editor audition render failed: %s", message)
            self._set_state(FAILED, message)
