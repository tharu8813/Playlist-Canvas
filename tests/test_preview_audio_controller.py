from __future__ import annotations

import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.controllers.preview_audio_controller import (
    PreviewAudioController,
    _PreviewAudioWorker,
)
from app.models.playlist import PlaylistTrack
from app.renderer.ffmpeg_renderer import FFmpegRenderer, RenderCancelledError, RenderError


def _renderer() -> FFmpegRenderer:
    renderer = FFmpegRenderer.__new__(FFmpegRenderer)
    renderer.executable = Path("ffmpeg")
    return renderer


def _track(name: str) -> PlaylistTrack:
    return PlaylistTrack(file_path=name, title=name, duration_seconds=30.0)


class PreviewAudioWorkerTests(unittest.TestCase):
    def test_run_emits_ready_with_the_rendered_path(self) -> None:
        with TemporaryDirectory(prefix="preview-audio-") as directory:
            renderer = _renderer()
            worker = _PreviewAudioWorker(
                renderer, [_track("a.mp3")], Path(directory), "crossfade", 3.0,
            )
            with patch.object(renderer, "prepare_playlist_audio", return_value=Path(directory) / "out.m4a"):
                received: list[str] = []
                worker.ready.connect(received.append)
                worker.run()
            self.assertEqual(received, [str(Path(directory) / "out.m4a")])

    def test_render_failure_emits_failed_not_an_exception(self) -> None:
        with TemporaryDirectory(prefix="preview-audio-") as directory:
            renderer = _renderer()
            worker = _PreviewAudioWorker(
                renderer, [_track("a.mp3")], Path(directory), "automix", 3.0,
            )
            with patch.object(renderer, "prepare_playlist_audio", side_effect=RenderError("boom")):
                failures: list[str] = []
                worker.failed.connect(failures.append)
                worker.run()  # must not raise
            self.assertEqual(failures, ["boom"])

    def test_cancellation_emits_neither_signal(self) -> None:
        with TemporaryDirectory(prefix="preview-audio-") as directory:
            renderer = _renderer()
            worker = _PreviewAudioWorker(
                renderer, [_track("a.mp3")], Path(directory), "automix", 3.0,
            )
            with patch.object(renderer, "prepare_playlist_audio", side_effect=RenderCancelledError("cancelled")):
                ready: list[str] = []
                failures: list[str] = []
                worker.ready.connect(ready.append)
                worker.failed.connect(failures.append)
                worker.run()
            self.assertEqual(ready, [])
            self.assertEqual(failures, [])


class PreviewAudioControllerTests(unittest.TestCase):
    def test_start_with_transition_mode_none_creates_no_worker(self) -> None:
        controller = PreviewAudioController(_renderer())
        controller.start([_track("a.mp3")], Path("."), "none", 3.0)
        self.assertIsNone(controller._worker)

    def test_start_with_no_tracks_creates_no_worker(self) -> None:
        controller = PreviewAudioController(_renderer())
        controller.start([], Path("."), "automix", 3.0)
        self.assertIsNone(controller._worker)

    def test_cancel_with_no_worker_is_a_no_op(self) -> None:
        controller = PreviewAudioController(_renderer())
        controller.cancel()  # must not raise

    def test_a_worker_that_finishes_on_its_own_is_forgotten(self) -> None:
        controller = PreviewAudioController(_renderer())
        worker = _PreviewAudioWorker(_renderer(), [_track("a.mp3")], Path("."), "automix", 3.0, controller)
        controller._worker = worker
        controller._forget(worker)
        self.assertIsNone(controller._worker)

    def test_cancel_survives_an_already_deleted_qt_object(self) -> None:
        class _DeletedWorker:
            def isRunning(self) -> bool:
                raise RuntimeError("libshiboken: Internal C++ object already deleted.")

        controller = PreviewAudioController(_renderer())
        controller._worker = _DeletedWorker()
        controller.cancel()  # must not raise
        self.assertIsNone(controller._worker)


if __name__ == "__main__":
    unittest.main()
