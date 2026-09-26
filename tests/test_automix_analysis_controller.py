from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app.automix.models import TrackAnalysis
from app.automix.structure.models import TrackStructureAnalysis
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

    def test_background_analysis_uses_a_reduced_worker_pool(self) -> None:
        from app.automix.workflow import AutoMixWorkflow
        from app.controllers.automix_analysis_controller import BACKGROUND_ANALYSIS_WORKERS

        created = []

        def spy(provider, **kwargs):
            created.append(kwargs["analysis_settings"].max_workers)
            return AutoMixWorkflow(provider, **kwargs)

        with (
            patch("app.automix.analysis.basic.BasicAnalysisProvider", _StubProvider),
            patch("app.automix.workflow.AutoMixWorkflow", spy),
        ):
            _AutoMixAnalysisWorker([_track("a.mp3")], Path("ffmpeg")).run()
        self.assertEqual(created, [BACKGROUND_ANALYSIS_WORKERS])
        self.assertLess(BACKGROUND_ANALYSIS_WORKERS, 4)  # below the foreground pool

    def test_run_reports_both_stages_up_front_then_each_track(self) -> None:
        tracks = [_track("a.mp3"), _track("b.mp3")]
        with (
            patch("app.automix.analysis.basic.BasicAnalysisProvider", _StubProvider),
            patch("app.automix.structure.sonara.sonara_available", return_value=True),
            patch("app.automix.structure.service.StructureAnalysisService.analyze_tracks") as structure,
        ):
            structure.side_effect = lambda tracks, cancel_event, progress: (
                progress(len(tracks), len(tracks), "done"), type("R", (), {"analyses": {}})())[1]
            worker = _AutoMixAnalysisWorker(tracks, Path("ffmpeg"), enable_structure_analysis=True)
            reports: list[tuple[str, int, int]] = []
            worker.progress.connect(lambda *report: reports.append(report))
            worker.run()
        self.assertEqual(reports[:2], [("rhythm", 0, 2), ("structure", 0, 2)])
        self.assertIn(("rhythm", 2, 2), reports)
        self.assertEqual(reports[-1], ("structure", 2, 2))

    def test_controller_reports_stages_and_running_state_of_the_current_pass_only(self) -> None:
        controller = AutoMixAnalysisController()
        worker = _AutoMixAnalysisWorker([_track("a.mp3")], Path("ffmpeg"), controller)
        controller._worker = worker
        changes: list[dict] = []
        running: list[bool] = []
        controller.progress_changed.connect(changes.append)
        controller.running_changed.connect(running.append)
        controller._report(worker, "rhythm", 1, 3)
        self.assertEqual(changes, [{"rhythm": (1, 3)}])
        self.assertTrue(controller.is_running)
        stale = _AutoMixAnalysisWorker([_track("b.mp3")], Path("ffmpeg"), controller)
        controller._report(stale, "rhythm", 3, 3)  # a superseded pass
        self.assertEqual(len(changes), 1)
        controller.cancel()
        self.assertEqual(running, [False])  # hidden at once, not after the worker drains
        self.assertEqual(controller.stages, {})
        controller._report(worker, "rhythm", 2, 3)  # cancelled: ignored
        self.assertEqual(len(changes), 1)

    def test_default_provider_id_is_basic(self) -> None:
        track = _track("a.mp3")
        worker = _AutoMixAnalysisWorker([track], Path("ffmpeg"))
        self.assertEqual(worker._provider_id, "basic")

    def test_pending_replacement_carries_the_requested_provider_id(self) -> None:
        controller = AutoMixAnalysisController()
        controller._worker = _AutoMixAnalysisWorker([_track("old.mp3")], Path("ffmpeg"), controller)
        new_tracks = [_track("new.mp3")]
        controller.start(new_tracks, Path("ffmpeg"), provider_id="beat_this")
        self.assertEqual(controller._pending, (new_tracks, Path("ffmpeg"), "beat_this", False))

    def test_pending_replacement_carries_enable_structure_analysis(self) -> None:
        controller = AutoMixAnalysisController()
        controller._worker = _AutoMixAnalysisWorker([_track("old.mp3")], Path("ffmpeg"), controller)
        new_tracks = [_track("new.mp3")]
        controller.start(new_tracks, Path("ffmpeg"), enable_structure_analysis=True)
        self.assertEqual(controller._pending, (new_tracks, Path("ffmpeg"), "basic", True))

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

    def test_structure_analysis_runs_after_rhythm_analysis_when_enabled_and_available(self) -> None:
        track = _track("a.mp3")
        with (
            patch("app.automix.analysis.basic.BasicAnalysisProvider", _StubProvider),
            patch("app.automix.structure.sonara.sonara_available", return_value=True),
            patch("app.automix.structure.service.StructureAnalysisService.analyze_tracks") as analyze_tracks,
        ):
            from app.automix.structure.service import StructureAnalysisBatchResult
            structure_result = TrackStructureAnalysis(
                track_id=track.id, source_path=track.file_path, duration_seconds=30.0,
                analyzer_id="sonara_structure", analyzer_version="1",
            )
            analyze_tracks.return_value = StructureAnalysisBatchResult(
                analyses={track.id: structure_result}, failures={},
            )
            worker = _AutoMixAnalysisWorker(
                [track], Path("ffmpeg"), enable_structure_analysis=True,
            )
            rhythm_received: list[dict] = []
            structure_received: list[dict] = []
            worker.analyzed.connect(rhythm_received.append)
            worker.structures_analyzed.connect(structure_received.append)
            worker.run()
        self.assertEqual(len(rhythm_received), 1)  # rhythm analysis unaffected
        self.assertEqual(len(structure_received), 1)
        self.assertIs(structure_received[0][track.id], structure_result)

    def test_structure_analysis_is_skipped_when_disabled(self) -> None:
        track = _track("a.mp3")
        with (
            patch("app.automix.analysis.basic.BasicAnalysisProvider", _StubProvider),
            patch("app.automix.structure.sonara.sonara_available") as sonara_available,
        ):
            worker = _AutoMixAnalysisWorker([track], Path("ffmpeg"), enable_structure_analysis=False)
            structure_received: list[dict] = []
            worker.structures_analyzed.connect(structure_received.append)
            worker.run()
        sonara_available.assert_not_called()  # never even probed when disabled
        self.assertEqual(structure_received, [])

    def test_structure_analysis_is_a_no_op_when_sonara_is_not_installed(self) -> None:
        """Rhythm analysis must succeed and structure must silently no-op --
        never a crash, never a failed-batch signal."""
        track = _track("a.mp3")
        with (
            patch("app.automix.analysis.basic.BasicAnalysisProvider", _StubProvider),
            patch("app.automix.structure.sonara.sonara_available", return_value=False),
        ):
            worker = _AutoMixAnalysisWorker([track], Path("ffmpeg"), enable_structure_analysis=True)
            rhythm_received: list[dict] = []
            structure_received: list[dict] = []
            worker.analyzed.connect(rhythm_received.append)
            worker.structures_analyzed.connect(structure_received.append)
            worker.run()  # must not raise
        self.assertEqual(len(rhythm_received), 1)
        self.assertEqual(structure_received, [])


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
