from __future__ import annotations

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
        with patch(
            "app.controllers.automix_analysis_controller.BasicAnalysisProvider", _StubProvider,
        ):
            worker = _AutoMixAnalysisWorker([track], Path("ffmpeg"))
            received: list[dict] = []
            worker.analyzed.connect(received.append)
            worker.run()
        self.assertEqual(len(received), 1)
        self.assertIn(track.id, received[0])
        self.assertEqual(received[0][track.id].bpm, 120.0)

    def test_cancel_before_run_suppresses_the_signal(self) -> None:
        track = _track("a.mp3")
        with patch(
            "app.controllers.automix_analysis_controller.BasicAnalysisProvider", _StubProvider,
        ):
            worker = _AutoMixAnalysisWorker([track], Path("ffmpeg"))
            worker.cancel()
            received: list[dict] = []
            worker.analyzed.connect(received.append)
            worker.run()
        self.assertEqual(received, [])


class AutoMixAnalysisControllerTests(unittest.TestCase):
    def test_start_with_no_tracks_creates_no_worker(self) -> None:
        controller = AutoMixAnalysisController()
        controller.start([], Path("ffmpeg"))
        self.assertIsNone(controller._worker)

    def test_cancel_with_no_worker_is_a_no_op(self) -> None:
        controller = AutoMixAnalysisController()
        controller.cancel()  # must not raise


if __name__ == "__main__":
    unittest.main()
