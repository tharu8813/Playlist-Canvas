"""Bounded asynchronous PNG staging for the Canvas export fallback path."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
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
    """Overlap Canvas capture with bounded parallel PNG compression and writes."""

    def __init__(
        self,
        on_written: Callable[[str, QImage, int], None],
        *,
        cancel_event: threading.Event,
        wait_callback: Callable[[], None] | None = None,
        queue_capacity: int = 3,
        worker_count: int | None = None,
    ) -> None:
        self._on_written = on_written
        self._cancel_event = cancel_event
        self._wait_callback = wait_callback
        total_capacity = max(1, int(queue_capacity))
        resolved_workers = self._resolve_worker_count(
            total_capacity, worker_count,
        )
        base_capacity, remainder = divmod(total_capacity, resolved_workers)
        self._pipelines = [
            BoundedExportPipeline(
                self._write,
                capacity=base_capacity + (1 if index < remainder else 0),
                name=f"png-frame-writer-{index + 1}",
            )
            for index in range(resolved_workers)
        ]
        self._next_pipeline = 0
        self._peak_buffered_frames = 0
        self._finished = False
        for pipeline in self._pipelines:
            pipeline.start()

    @staticmethod
    def _resolve_worker_count(
        queue_capacity: int, worker_count: int | None,
    ) -> int:
        """Use a small CPU-aware pool without turning export into disk thrash."""
        if worker_count is None:
            cpu_count = os.cpu_count() or 2
            worker_count = 2 if cpu_count >= 4 and queue_capacity >= 2 else 1
        return max(1, min(3, queue_capacity, int(worker_count)))

    @property
    def worker_count(self) -> int:
        return len(self._pipelines)

    @property
    def peak_buffered_frames(self) -> int:
        return self._peak_buffered_frames

    @property
    def pending_frames(self) -> int:
        return sum(pipeline.pending_count for pipeline in self._pipelines)

    def submit(self, image: QImage, path: Path, stream_key: str) -> None:
        if self._finished:
            raise PngFrameStagingError("PNG frame staging has already finished.")
        pipeline = self._select_pipeline()
        try:
            pipeline.submit(
                _PngWriteRequest(QImage(image), path, stream_key),
                producer_cancel_event=self._cancel_event,
                producer_wait_callback=self._wait_callback,
            )
            self._peak_buffered_frames = max(
                self._peak_buffered_frames, self.pending_frames,
            )
        except ExportPipelineCancelledError as error:
            self.cancel()
            raise PngFrameStagingCancelled("PNG frame staging was cancelled.") from error
        except ExportPipelineError as error:
            self.cancel()
            raise PngFrameStagingError(str(error)) from error

    def _select_pipeline(self) -> BoundedExportPipeline[_PngWriteRequest]:
        """Prefer the least queued writer while rotating ties across workers."""
        count = len(self._pipelines)
        ranked = [
            (
                pipeline.pending_count,
                (index - self._next_pipeline) % count,
                index,
                pipeline,
            )
            for index, pipeline in enumerate(self._pipelines)
        ]
        _pending, _rotation, index, pipeline = min(
            ranked, key=lambda entry: (entry[0], entry[1]),
        )
        self._next_pipeline = (index + 1) % count
        return pipeline

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        failure: ExportPipelineError | None = None
        for pipeline in self._pipelines:
            try:
                pipeline.finish(wait_callback=self._wait_callback)
            except ExportPipelineError as error:
                if failure is None:
                    failure = error
                for pending in self._pipelines:
                    if pending is not pipeline:
                        pending.cancel()
        if failure is None:
            return
        if (
            isinstance(failure, ExportPipelineCancelledError)
            or self._cancel_event.is_set()
        ):
            raise PngFrameStagingCancelled(
                "PNG frame staging was cancelled."
            ) from failure
        raise PngFrameStagingError(str(failure)) from failure

    def cancel(self) -> None:
        if self._finished:
            return
        self._finished = True
        for pipeline in self._pipelines:
            pipeline.cancel()
        for pipeline in self._pipelines:
            try:
                # Wait for in-progress QImageWriter calls before the caller removes
                # the temporary directory. Processing events keeps modal cancel
                # UI responsive while the final bounded writes exit.
                pipeline.finish(wait_callback=self._wait_callback)
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
