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

from PySide6.QtCore import QObject, QThread, Signal

from app.models.playlist import PlaylistTrack
from app.renderer.ffmpeg_renderer import FFmpegRenderer, RenderCancelledError, RenderError

LOGGER = logging.getLogger(__name__)


class _PreviewAudioWorker(QThread):
    ready = Signal(str)
    """Emits the rendered audio file's path on success."""
    failed = Signal(str)

    def __init__(
        self, renderer: FFmpegRenderer, tracks: list[PlaylistTrack], output_directory: Path,
        transition_mode: str, crossfade_seconds: float, parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._renderer = renderer
        self._tracks = tracks
        self._output_directory = output_directory
        self._transition_mode = transition_mode
        self._crossfade_seconds = crossfade_seconds
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            from app.renderer.ffmpeg_renderer import RenderSettings

            self._output_directory.mkdir(parents=True, exist_ok=True)
            path = self._renderer.prepare_playlist_audio(
                self._tracks, self._output_directory, RenderSettings(),
                transition_mode=self._transition_mode, crossfade_seconds=self._crossfade_seconds,
                cancel_event=self._cancel_event,
            )
        except RenderCancelledError:
            return
        except (RenderError, OSError) as error:
            LOGGER.warning("Preview blended-audio render failed: %s", error)
            self.failed.emit(str(error))
            return
        if not self._cancel_event.is_set():
            self.ready.emit(str(path))


class PreviewAudioController(QObject):
    """Owns at most one blended-preview-audio render at a time."""

    audio_ready = Signal(str)
    """Emits the rendered audio file's path."""
    audio_failed = Signal(str)

    def __init__(self, renderer: FFmpegRenderer, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._renderer = renderer
        self._worker: _PreviewAudioWorker | None = None

    def start(
        self, tracks: list[PlaylistTrack], output_directory: Path,
        transition_mode: str, crossfade_seconds: float,
    ) -> None:
        """Render blended preview audio in the background, replacing any run underway."""
        self.cancel()
        if not tracks or transition_mode == "none":
            return
        worker = _PreviewAudioWorker(
            self._renderer, tracks, output_directory, transition_mode, crossfade_seconds, self,
        )
        worker.ready.connect(self.audio_ready)
        worker.failed.connect(self.audio_failed)
        worker.finished.connect(lambda: self._forget(worker))
        self._worker = worker
        worker.start()

    def _forget(self, worker: "_PreviewAudioWorker") -> None:
        if self._worker is worker:
            self._worker = None
        worker.deleteLater()

    def cancel(self) -> None:
        """Stop the in-flight render, if any, and wait for its thread to exit."""
        worker, self._worker = self._worker, None
        if worker is None:
            return
        try:
            running = worker.isRunning()
        except RuntimeError:
            # The underlying C++ QThread was already destroyed.
            return
        if running:
            worker.cancel()
            worker.wait(5000)
