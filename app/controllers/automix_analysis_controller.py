"""Background AutoMix analysis for the currently loaded playlist.

Runs off the GUI thread (roadmap Phase 1 section 7 / Phase 6 section 4:
"Do not freeze the UI"). Results here are session-only, in-memory display
data for MainWindow's playlist badges and track-details panel -- the
durable, cross-session cache is AnalysisCache (app/automix/cache.py);
this controller only orchestrates one background pass over the current
playlist and reports what came back.

``app.automix.analysis.basic`` (and, through it, ``librosa`` and its
dependency chain) is imported lazily, inside the worker thread's own
``run()``, not at module scope. AutoMix is optional -- the roadmap's
"keep the basic engine usable" rule extends to "installing the app at
all": MainWindow imports this controller unconditionally at startup, so
a top-level ``import librosa`` here would make a missing/broken librosa
installation prevent the entire application from launching, not just
AutoMix.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from app.automix.models import TrackAnalysis
from app.models.playlist import PlaylistTrack

LOGGER = logging.getLogger(__name__)


class _AutoMixAnalysisWorker(QThread):
    analyzed = Signal(dict)
    """Emits track_id -> TrackAnalysis for every track that analyzed successfully."""

    def __init__(
        self, tracks: list[PlaylistTrack], ffmpeg_executable: Path, parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._tracks = tracks
        self._ffmpeg_executable = ffmpeg_executable
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            from app.automix.analysis.basic import BasicAnalysisProvider
            from app.automix.workflow import AutoMixWorkflow
        except ImportError as error:
            LOGGER.warning("AutoMix analysis is unavailable: %s", error)
            return
        workflow = AutoMixWorkflow(BasicAnalysisProvider(self._ffmpeg_executable))
        result = workflow.analyze(self._tracks, cancel_event=self._cancel_event)
        if not self._cancel_event.is_set():
            self.analyzed.emit(result.analyses)


class AutoMixAnalysisController(QObject):
    """Owns at most one AutoMix analysis pass at a time."""

    analyses_updated = Signal(dict)
    """track_id -> TrackAnalysis for the tracks that just finished analyzing."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._worker: _AutoMixAnalysisWorker | None = None

    def start(self, tracks: list[PlaylistTrack], ffmpeg_executable: Path) -> None:
        """Analyze ``tracks`` in the background, replacing any run already underway."""
        self.cancel()
        if not tracks:
            return
        worker = _AutoMixAnalysisWorker(tracks, ffmpeg_executable, self)
        worker.analyzed.connect(self.analyses_updated)
        # Clear our reference *before* scheduling deletion: a worker that
        # finishes on its own (not via cancel()) would otherwise leave
        # self._worker pointing at a QThread whose C++ object deleteLater()
        # has already destroyed by the time the next start()/cancel() call
        # touches it ("libshiboken: ... already deleted").
        worker.finished.connect(lambda: self._forget(worker))
        self._worker = worker
        worker.start()

    def _forget(self, worker: "_AutoMixAnalysisWorker") -> None:
        if self._worker is worker:
            self._worker = None
        worker.deleteLater()

    def cancel(self) -> None:
        """Stop the in-flight analysis, if any, and wait for its thread to exit."""
        worker, self._worker = self._worker, None
        if worker is None:
            return
        try:
            running = worker.isRunning()
        except RuntimeError:
            # The underlying C++ QThread was already destroyed (it finished
            # and was deleted between us reading self._worker and now).
            return
        if running:
            worker.cancel()
            worker.wait(5000)
