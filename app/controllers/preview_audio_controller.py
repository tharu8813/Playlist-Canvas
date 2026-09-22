"""Background rendering of one blended preview audio track.

Roadmap Phase 6 section 9: "The full existing Preview must use the same
CompiledRenderPlan that would be used by export... pre-render a full
preview audio master." This controller does exactly that -- it calls
FFmpegRenderer.prepare_playlist_audio() (the same method render() calls
for export) off the GUI thread, so ExportPreviewDialog never re-derives
AutoMix/crossfade timing itself.

If rendering fails, is unavailable, or has not finished yet, the caller
falls back to legacy per-track playback -- this must never prevent
Preview from working, only add to it once ready.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QObject, QThread, Signal

from app.models.playlist import PlaylistTrack
from app.renderer.ffmpeg_renderer import FFmpegRenderer, RenderCancelledError, RenderError
from app.timeline.compiler import compile_playlist

LOGGER = logging.getLogger(__name__)


def prepare_audio_for_ui(renderer, tracks, directory, mode, crossfade_seconds,
                         settings, cancel_event, progress=None):
    """Resolve audio and timing together while the caller's modal UI stays responsive."""
    worker = _PreviewAudioWorker(renderer, tracks, directory, mode, crossfade_seconds,
                                 settings=settings, cancel_event=cancel_event)
    results, errors = [], []
    worker.ready.connect(lambda path, plan: results.append((Path(path), plan)))
    worker.failed.connect(errors.append)
    if progress is not None:
        worker.progress.connect(progress)
    loop = QEventLoop()
    worker.finished.connect(loop.quit)
    worker.start()
    loop.exec()
    worker.wait()
    worker.deleteLater()
    if cancel_event.is_set():
        raise RenderCancelledError("Audio preparation was cancelled.")
    if not results:
        raise RenderError(errors[0] if errors else "Could not prepare playlist audio.")
    return results[0]


class _PreviewAudioWorker(QThread):
    ready = Signal(str, object)
    """Emits the rendered audio file's path on success."""
    failed = Signal(str)
    progress = Signal(str, float, str)

    def __init__(
        self, renderer: FFmpegRenderer, tracks: list[PlaylistTrack], output_directory: Path,
        transition_mode: str, crossfade_seconds: float, parent: QObject | None = None,
        *, settings=None, cancel_event: threading.Event | None = None,
    ) -> None:
        super().__init__(parent)
        self._renderer = renderer
        self._tracks = tracks
        self._output_directory = output_directory
        self._transition_mode = transition_mode
        self._crossfade_seconds = crossfade_seconds
        self._cancel_event = cancel_event or threading.Event()
        self._settings = settings
        self.plan = compile_playlist(tracks)

    def _accept_plan(self, plan) -> None:
        self.plan = plan

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            from app.renderer.ffmpeg_renderer import RenderSettings

            self._output_directory.mkdir(parents=True, exist_ok=True)
            path = self._renderer.prepare_playlist_audio(
                self._tracks, self._output_directory, self._settings or RenderSettings(),
                transition_mode=self._transition_mode, crossfade_seconds=self._crossfade_seconds,
                cancel_event=self._cancel_event,
                plan_callback=self._accept_plan,
                progress_callback=self.progress.emit,
            )
        except RenderCancelledError:
            return
        except Exception as error:
            LOGGER.warning("Preview blended-audio render failed: %s", error)
            self.failed.emit(str(error))
            return
        if not self._cancel_event.is_set():
            self.ready.emit(str(path), self.plan)


class PreviewAudioController(QObject):
    """Owns at most one blended-preview-audio render at a time."""

    audio_ready = Signal(str, object)
    """Emits the rendered audio file's path."""
    audio_failed = Signal(str)
    progress = Signal(str, float, str)
    """Forwards the active worker's (stage, fraction, message) progress, if any."""

    def __init__(self, renderer: FFmpegRenderer, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._renderer = renderer
        self._worker: _PreviewAudioWorker | None = None
        self._pending = None
        self._shutting_down = False

    def start(
        self, tracks: list[PlaylistTrack], output_directory: Path,
        transition_mode: str, crossfade_seconds: float,
    ) -> None:
        """Render blended preview audio in the background, replacing any run underway."""
        if self._shutting_down:
            return
        self.cancel()
        if not tracks or transition_mode == "none":
            return
        if self._worker is not None:
            self._pending = (tracks, output_directory, transition_mode, crossfade_seconds,)
            return
        worker = _PreviewAudioWorker(
            self._renderer, tracks, output_directory, transition_mode, crossfade_seconds, self,
        )
        worker.ready.connect(
            lambda path, plan: self.audio_ready.emit(path, plan)
            if not worker._cancel_event.is_set() else None
        )
        worker.failed.connect(
            lambda message: self.audio_failed.emit(message)
            if not worker._cancel_event.is_set() else None
        )
        worker.finished.connect(lambda: self._forget(worker))
        worker.progress.connect(self.progress.emit)
        self._worker = worker
        worker.start()

    def _forget(self, worker: "_PreviewAudioWorker") -> None:
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
