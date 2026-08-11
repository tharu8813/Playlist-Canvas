"""Background QThread wrapper around the synchronous FFmpeg renderer."""

from __future__ import annotations

import threading
import logging
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from app.models.playlist import PlaylistTrack
from app.renderer.ffmpeg_renderer import (
    FFmpegRenderer,
    RenderCancelledError,
    RenderError,
    RenderResult,
    RenderFrame,
    RenderSettings,
    StaticOverlayLayer,
    VisualizerOverlay,
)
from app.utils.logging_setup import report_unexpected_error


LOGGER = logging.getLogger(__name__)


class RenderWorker(QThread):
    """Runs an FFmpeg export without blocking the Qt event loop."""

    progress = Signal(str, float, str)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, renderer: FFmpegRenderer, image: QImage | list[QImage] | list[RenderFrame],
                 tracks: list[PlaylistTrack], output_path: str | Path,
                 settings: RenderSettings | None = None,
                 visualizers: list[VisualizerOverlay] | None = None,
                 static_layers: list[StaticOverlayLayer] | None = None) -> None:
        super().__init__()
        self.renderer = renderer
        self.image = [
            RenderFrame(frame.image if isinstance(frame.image, Path) else QImage(frame.image), frame.duration_seconds)
            if isinstance(frame, RenderFrame) else QImage(frame)
            for frame in image
        ] if isinstance(image, list) else QImage(image)
        self.tracks = list(tracks)
        self.output_path = Path(output_path)
        self.settings = settings
        self.visualizers = list(visualizers or [])
        self.static_layers = list(static_layers or [])
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """Request a cooperative FFmpeg stop at the next process polling interval."""
        self._cancel_event.set()

    def run(self) -> None:
        """Execute the render and forward outcome signals to the GUI thread."""
        try:
            result = self.renderer.render(
                self.image, self.tracks, self.output_path, self.settings,
                progress_callback=self.progress.emit, cancel_event=self._cancel_event,
                visualizers=self.visualizers, static_layers=self.static_layers,
            )
        except RenderCancelledError:
            self.cancelled.emit()
        except RenderError as error:
            self.failed.emit(str(error))
        except Exception as error:
            # Never let an unexpected worker exception escape QThread.  Besides
            # avoiding a process-level Qt abort, this preserves the traceback in
            # the persistent log and returns control to the export error dialog.
            LOGGER.exception("Unexpected export worker failure")
            report_unexpected_error("Export worker", error)
            self.failed.emit(f"Unexpected export failure: {error}")
        else:
            self.succeeded.emit(result)
