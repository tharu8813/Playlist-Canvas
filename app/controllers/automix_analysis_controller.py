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

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QObject, QThread, Signal

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
        self._pending = None
        self._shutting_down = False

    def start(self, tracks: list[PlaylistTrack], ffmpeg_executable: Path) -> None:
        """Analyze ``tracks`` in the background, replacing any run already underway."""
        if self._shutting_down:
            return
        self.cancel()
        if not tracks:
            return
        if self._worker is not None:
            self._pending = (tracks, ffmpeg_executable,)
            return
        worker = _AutoMixAnalysisWorker(tracks, ffmpeg_executable, self)
        worker.analyzed.connect(
            lambda result: self.analyses_updated.emit(result)
            if not worker._cancel_event.is_set() else None
        )
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
        if self._shutting_down:
            # shutdown() takes exclusive ownership of this worker's
            # wait()/deleteLater() sequence once shutdown has started (see
            # shutdown() below). Scheduling deletion here too raced
            # shutdown()'s own wait()/deleteLater() call on Windows: the
            # deferred-delete event could destroy the C++ QThread object
            # while shutdown()'s nested event loop was still processing
            # events for it, producing a 0xC0000409 fail-fast crash the
            # very first time this was exercised under real threading.
            return
        worker.deleteLater()
        pending, self._pending = self._pending, None
        if pending is not None:
            self.start(*pending)

    def cancel(self) -> None:
        """Request cancellation; retain ownership until finished is delivered."""
        self._pending = None
        worker = self._worker
        if worker is None:
            return
        try:
            worker.isRunning()  # Detect an already-deleted Qt wrapper.
            worker.cancel()  # Suppress even results already queued for delivery.
        except RuntimeError:
            self._worker = None

    def shutdown(self) -> None:
        """Finish cancellation before the owner or its temporary files are deleted.

        Takes exclusive ownership of the worker's wait()/deleteLater()
        sequence: disconnecting `finished` here (before reconnecting it to
        the local wait loop below) guarantees `_forget()` cannot run again
        for this worker, so there is no other code path left that could
        delete the underlying C++ QThread object while this method still
        holds and waits on it.
        """
        self._shutting_down = True
        self.cancel()
        worker, self._worker = self._worker, None
        if worker is None:
            return
        try:
            worker.finished.disconnect()
        except RuntimeError:
            return  # The C++ QThread object is already gone.
        try:
            running = worker.isRunning()
        except RuntimeError:
            return
        if running:
            loop = QEventLoop()
            worker.finished.connect(loop.quit)
            # Paint and queued completion signals keep flowing during shutdown.
            # New user actions must not reenter the owner's destruction path.
            loop.exec(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        try:
            worker.wait()
        except RuntimeError:
            pass
        worker.deleteLater()
        # A plain deleteLater() only *schedules* deletion for whenever some
        # later, unrelated event loop iteration happens to process it --
        # which, in practice, was often a *different* controller's own
        # shutdown() nested loop (MainWindow shuts several of these down in
        # sequence on close). Processing several controllers' leftover
        # QThread deletions interleaved with another controller's still-
        # live thread completion inside the same loop pass reproduced the
        # 0xC0000409 crash deterministically. Forcing this one object's
        # DeferredDelete to run right now removes that ambiguity entirely.
        QCoreApplication.sendPostedEvents(worker, QEvent.Type.DeferredDelete)
