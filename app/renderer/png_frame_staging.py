"""Bounded asynchronous PNG staging for the Canvas export fallback path."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import threading

from PySide6.QtGui import QImage, QImageWriter

from app.renderer.bounded_pipeline import (
    BoundedExportPipeline,
    ExportPipelineCancelledError,
    ExportPipelineError,
)


class PngFrameStagingError(RuntimeError):
    """Raised when an asynchronously staged PNG cannot be written safely."""


class PngFrameStagingCancelled(PngFrameStagingError):
    """Raised when the producer cancels PNG staging."""


@dataclass(frozen=True, slots=True)
class _PngWriteRequest:
    image: QImage
    path: Path
    stream_key: str


class PngFrameStagingPipeline:
    """Overlap ordered Canvas capture with bounded PNG compression and writes."""

    def __init__(
        self,
        on_written: Callable[[str, QImage, int], None],
        *,
        cancel_event: threading.Event,
        wait_callback: Callable[[], None] | None = None,
        queue_capacity: int = 3,
    ) -> None:
        self._on_written = on_written
        self._cancel_event = cancel_event
        self._wait_callback = wait_callback
        self._pipeline = BoundedExportPipeline(
            self._write,
            capacity=max(1, queue_capacity),
            name="png-frame-writer",
        )
        self._finished = False
        self._pipeline.start()

    @property
    def peak_buffered_frames(self) -> int:
        return self._pipeline.peak_buffered_items

    @property
    def pending_frames(self) -> int:
        return self._pipeline.pending_count

    def submit(self, image: QImage, path: Path, stream_key: str) -> None:
        if self._finished:
            raise PngFrameStagingError("PNG frame staging has already finished.")
        try:
            self._pipeline.submit(
                _PngWriteRequest(QImage(image), path, stream_key),
                producer_cancel_event=self._cancel_event,
                producer_wait_callback=self._wait_callback,
            )
        except ExportPipelineCancelledError as error:
            raise PngFrameStagingCancelled("PNG frame staging was cancelled.") from error
        except ExportPipelineError as error:
            raise PngFrameStagingError(str(error)) from error

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        try:
            self._pipeline.finish(wait_callback=self._wait_callback)
        except ExportPipelineCancelledError as error:
            raise PngFrameStagingCancelled("PNG frame staging was cancelled.") from error
        except ExportPipelineError as error:
            if self._cancel_event.is_set():
                raise PngFrameStagingCancelled(
                    "PNG frame staging was cancelled."
                ) from error
            raise PngFrameStagingError(str(error)) from error

    def cancel(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._pipeline.cancel()
        try:
            # Wait for an in-progress QImageWriter call before the caller removes
            # the temporary directory. Processing events keeps the modal cancel
            # UI responsive while that final bounded write exits.
            self._pipeline.finish(wait_callback=self._wait_callback)
        except ExportPipelineError:
            pass

    def _write(self, request: _PngWriteRequest) -> None:
        if self._cancel_event.is_set():
            raise PngFrameStagingCancelled("PNG frame staging was cancelled.")
        writer = QImageWriter(str(request.path), b"png")
        # Low compression keeps pixels lossless while reducing preparation CPU.
        writer.setCompression(1)
        writer.setOptimizedWrite(False)
        if not writer.write(request.image):
            raise PngFrameStagingError(
                f"Could not stage an export frame on disk: {writer.errorString()}"
            )
        try:
            byte_count = request.path.stat().st_size
        except OSError:
            byte_count = 0
        self._on_written(request.stream_key, request.image, byte_count)
