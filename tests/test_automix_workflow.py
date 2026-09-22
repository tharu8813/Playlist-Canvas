from __future__ import annotations

import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.automix.cache import AnalysisCache
from app.automix.models import TrackAnalysis
from app.automix.settings import AutoMixTransitionSettings
from app.automix.workflow import AutoMixWorkflow
from app.models.playlist import PlaylistTrack


class _StubProvider:
    provider_id = "stub"
    version = "1"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def analyze(self, track, *, cancel_event, progress=None) -> TrackAnalysis:
        self.calls.append(track.file_path)
        return TrackAnalysis(
            track_id=track.id, source_path=track.file_path, duration_seconds=track.duration_seconds,
            bpm=120.0, bpm_confidence=0.9,
            beats=tuple(i * 0.5 for i in range(int(track.duration_seconds / 0.5))),
            downbeats=tuple(i * 2.0 for i in range(int(track.duration_seconds / 2.0))),
            meter_numerator=4, meter_denominator=4, meter_confidence=0.8,
            analyzer_id=self.provider_id, analyzer_version=self.version,
        )


def _track(directory: Path, name: str, duration: float = 60.0) -> PlaylistTrack:
    path = directory / name
    path.write_bytes(b"audio")
    return PlaylistTrack(file_path=str(path), title=name, duration_seconds=duration)


class AutoMixWorkflowTests(unittest.TestCase):
    def test_build_analyzes_then_plans(self) -> None:
        with TemporaryDirectory(prefix="automix-workflow-") as directory:
            tracks = [_track(Path(directory), "a.mp3"), _track(Path(directory), "b.mp3")]
            provider = _StubProvider()
            cache = AnalysisCache(Path(directory) / "cache", analyzer_id="stub", analyzer_version="1")
            workflow = AutoMixWorkflow(provider, cache=cache)
            result = workflow.build(tracks, AutoMixTransitionSettings(enabled=True))
            self.assertEqual(len(provider.calls), 2)
            self.assertEqual(len(result.plan.audio.clips), 2)
            self.assertEqual(result.failures, {})

    def test_second_analyze_call_reuses_cache_for_unchanged_tracks(self) -> None:
        with TemporaryDirectory(prefix="automix-workflow-") as directory:
            tracks = [_track(Path(directory), "a.mp3")]
            provider = _StubProvider()
            cache = AnalysisCache(Path(directory) / "cache", analyzer_id="stub", analyzer_version="1")
            workflow = AutoMixWorkflow(provider, cache=cache)
            workflow.analyze(tracks)
            workflow.analyze(tracks)
            self.assertEqual(len(provider.calls), 1)

    def test_changing_transition_settings_only_replans_no_new_analysis(self) -> None:
        with TemporaryDirectory(prefix="automix-workflow-") as directory:
            tracks = [_track(Path(directory), "a.mp3"), _track(Path(directory), "b.mp3")]
            provider = _StubProvider()
            cache = AnalysisCache(Path(directory) / "cache", analyzer_id="stub", analyzer_version="1")
            workflow = AutoMixWorkflow(provider, cache=cache)
            analyses = workflow.analyze(tracks).analyses
            self.assertEqual(len(provider.calls), 2)

            plan_8_bars = workflow.plan(tracks, analyses, AutoMixTransitionSettings(enabled=True, preferred_bars=8))
            plan_4_bars = workflow.plan(tracks, analyses, AutoMixTransitionSettings(enabled=True, preferred_bars=4))
            # Neither re-plan touched the analyzer.
            self.assertEqual(len(provider.calls), 2)
            self.assertNotEqual(plan_8_bars, plan_4_bars)

    def test_cancellation_before_start_returns_cleanly(self) -> None:
        with TemporaryDirectory(prefix="automix-workflow-") as directory:
            tracks = [_track(Path(directory), "a.mp3")]
            workflow = AutoMixWorkflow(
                _StubProvider(), cache=AnalysisCache(Path(directory) / "cache", analyzer_id="stub", analyzer_version="1"),
            )
            cancel_event = threading.Event()
            cancel_event.set()
            result = workflow.analyze(tracks, cancel_event=cancel_event)
            self.assertEqual(result.analyses, {})
            self.assertEqual(result.failures, {})

    def test_automix_disabled_setting_produces_a_sequential_equivalent_plan(self) -> None:
        with TemporaryDirectory(prefix="automix-workflow-") as directory:
            tracks = [_track(Path(directory), "a.mp3"), _track(Path(directory), "b.mp3")]
            workflow = AutoMixWorkflow(
                _StubProvider(), cache=AnalysisCache(Path(directory) / "cache", analyzer_id="stub", analyzer_version="1"),
            )
            result = workflow.build(tracks, AutoMixTransitionSettings(enabled=False))
            self.assertEqual(result.plan.audio.transitions, ())
            self.assertEqual(result.plan.duration_seconds, 120.0)


if __name__ == "__main__":
    unittest.main()
