from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app.automix.models import TrackAnalysis
from app.controllers.automix_analysis_controller import (
    AutoMixAnalysisController,
    _AutoMixAnalysisWorker,
)
from app.models.playlist import PlaylistTrack


class _StubProvider:
    provider_id = "stub"
    version = "1"

    def __init__(self, _executable) -> None:
        pass

    def analyze(self, track, *, cancel_event, progress=None) -> TrackAnalysis:
        return TrackAnalysis(
            track_id=track.id, source_path=track.file_path, duration_seconds=track.duration_seconds,
            bpm=120.0, analyzer_id=self.provider_id, analyzer_version=self.version,
        )


def _track(name: str) -> PlaylistTrack:
    return PlaylistTrack(file_path=name, title=name, duration_seconds=30.0)


class AutoMixAnalysisWorkerTests(unittest.TestCase):
    def test_run_emits_analyzed_results(self) -> None:
        track = _track("a.mp3")
        with patch("app.automix.analysis.basic.BasicAnalysisProvider", _StubProvider):
            worker = _AutoMixAnalysisWorker([track], Path("ffmpeg"))
            received: list[dict] = []
            worker.analyzed.connect(received.append)
            worker.run()
        self.assertEqual(len(received), 1)
        self.assertIn(track.id, received[0])
        self.assertEqual(received[0][track.id].bpm, 120.0)

    def test_cancel_before_run_suppresses_the_signal(self) -> None:
        track = _track("a.mp3")
        with patch("app.automix.analysis.basic.BasicAnalysisProvider", _StubProvider):
            worker = _AutoMixAnalysisWorker([track], Path("ffmpeg"))
            worker.cancel()
            received: list[dict] = []
            worker.analyzed.connect(received.append)
            worker.run()
        self.assertEqual(received, [])

    def test_provider_id_selects_the_beat_this_provider(self) -> None:
        track = _track("a.mp3")
        with patch("app.automix.analysis.beat_this.BeatThisAnalysisProvider", _StubProvider):
            worker = _AutoMixAnalysisWorker([track], Path("ffmpeg"), provider_id="beat_this")
            received: list[dict] = []
            worker.analyzed.connect(received.append)
            worker.run()
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][track.id].analyzer_id, "stub")

    def test_default_provider_id_is_basic(self) -> None:
        track = _track("a.mp3")
        worker = _AutoMixAnalysisWorker([track], Path("ffmpeg"))
        self.assertEqual(worker._provider_id, "basic")

    def test_pending_replacement_carries_the_requested_provider_id(self) -> None:
        controller = AutoMixAnalysisController()
        controller._worker = _AutoMixAnalysisWorker([_track("old.mp3")], Path("ffmpeg"), controller)
        new_tracks = [_track("new.mp3")]
        controller.start(new_tracks, Path("ffmpeg"), provider_id="beat_this")
        self.assertEqual(controller._pending, (new_tracks, Path("ffmpeg"), "beat_this"))

    def test_missing_librosa_dependency_is_handled_without_crashing(self) -> None:
        # A module set to None in sys.modules makes Python's import system
        # raise ImportError for it -- simulating "librosa is not installed"
        # without needing to actually uninstall it for this test.
        track = _track("a.mp3")
        with patch.dict(sys.modules, {"app.automix.analysis.basic": None}):
            worker = _AutoMixAnalysisWorker([track], Path("ffmpeg"))
            received: list[dict] = []
            worker.analyzed.connect(received.append)
            worker.run()  # must not raise
        self.assertEqual(received, [])


class AutoMixAnalysisControllerTests(unittest.TestCase):
    def test_start_with_no_tracks_creates_no_worker(self) -> None:
        controller = AutoMixAnalysisController()
        controller.start([], Path("ffmpeg"))
        self.assertIsNone(controller._worker)

    def test_cancel_with_no_worker_is_a_no_op(self) -> None:
        controller = AutoMixAnalysisController()
        controller.cancel()  # must not raise

    def test_a_worker_that_finishes_on_its_own_is_forgotten(self) -> None:
        """Regression: cancel()/start() must not touch a worker's C++ object
        after it finished naturally and was scheduled for deleteLater()."""
        controller = AutoMixAnalysisController()
        worker = _AutoMixAnalysisWorker([_track("a.mp3")], Path("ffmpeg"), controller)
        controller._worker = worker
        controller._forget(worker)
        self.assertIsNone(controller._worker)

    def test_cancel_survives_an_already_deleted_qt_object(self) -> None:
        """isRunning() on a destroyed QThread raises RuntimeError in PySide6;
        cancel() must treat that the same as "nothing to cancel", not crash."""
        class _DeletedWorker:
            def isRunning(self) -> bool:
                raise RuntimeError("libshiboken: Internal C++ object already deleted.")

        controller = AutoMixAnalysisController()
        controller._worker = _DeletedWorker()
        controller.cancel()  # must not raise
        self.assertIsNone(controller._worker)


if __name__ == "__main__":
    unittest.main()
