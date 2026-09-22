from __future__ import annotations

import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.automix.analysis.provider import AnalysisCancelled
from app.automix.analysis.service import AnalysisService
from app.automix.cache import AnalysisCache
from app.automix.models import TrackAnalysis
from app.automix.settings import AutoMixAnalysisSettings
from app.models.playlist import PlaylistTrack


class _StubProvider:
    provider_id = "stub"
    version = "1"

    def __init__(self, *, fail_paths: frozenset[str] = frozenset()) -> None:
        self.fail_paths = fail_paths
        self.calls: list[str] = []
        self.lock = threading.Lock()

    def analyze(self, track, *, cancel_event, progress=None) -> TrackAnalysis:
        with self.lock:
            self.calls.append(track.file_path)
        if track.file_path in self.fail_paths:
            raise RuntimeError("boom")
        return TrackAnalysis(
            track_id=track.id, source_path=track.file_path, duration_seconds=track.duration_seconds,
            bpm=120.0, analyzer_id=self.provider_id, analyzer_version=self.version,
        )


class _RecoveringHybridProvider:
    """Simulates a hybrid provider (like BeatThisAnalysisProvider) whose
    advanced engine is unavailable at first, then recovers -- its own
    analyze() always reports the *actual* analyzer_id/version that produced
    each result (its own identity when the advanced engine ran, or the
    fallback's when it degraded), exactly like BeatThisAnalysisProvider
    does by returning its owned BasicAnalysisProvider's result verbatim."""

    provider_id = "beat_this"
    version = "1"

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.available = False

    def analyze(self, track, *, cancel_event, progress=None) -> TrackAnalysis:
        self.calls.append(track.file_path)
        if self.available:
            return TrackAnalysis(
                track_id=track.id, source_path=track.file_path, duration_seconds=track.duration_seconds,
                bpm=120.0, analyzer_id=self.provider_id, analyzer_version=self.version,
            )
        return TrackAnalysis(
            track_id=track.id, source_path=track.file_path, duration_seconds=track.duration_seconds,
            bpm=118.0, analyzer_id="basic", analyzer_version="2",
        )


class _CancellingProvider:
    provider_id = "cancelling"
    version = "1"

    def analyze(self, track, *, cancel_event, progress=None) -> TrackAnalysis:
        raise AnalysisCancelled("cancelled mid-track")


def _track(directory: Path, name: str) -> PlaylistTrack:
    path = directory / name
    if not path.exists():
        path.write_bytes(b"audio")
    return PlaylistTrack(file_path=str(path), title=name, duration_seconds=30.0)


class AnalysisServiceTests(unittest.TestCase):
    def test_cache_miss_calls_provider_and_populates_cache(self) -> None:
        with TemporaryDirectory(prefix="automix-service-") as directory:
            provider = _StubProvider()
            cache = AnalysisCache(Path(directory) / "cache", analyzer_id="stub", analyzer_version="1")
            service = AnalysisService(provider, cache=cache)
            track = _track(Path(directory), "a.mp3")
            result = service.analyze_tracks([track])
            self.assertEqual(len(provider.calls), 1)
            self.assertIn(track.id, result.analyses)
            self.assertEqual(result.analyses[track.id].bpm, 120.0)
            self.assertIsNotNone(cache.load(track.file_path))

    def test_cache_hit_skips_provider(self) -> None:
        with TemporaryDirectory(prefix="automix-service-") as directory:
            provider = _StubProvider()
            cache = AnalysisCache(Path(directory) / "cache", analyzer_id="stub", analyzer_version="1")
            service = AnalysisService(provider, cache=cache)
            track = _track(Path(directory), "a.mp3")
            service.analyze_tracks([track])
            self.assertEqual(len(provider.calls), 1)
            service.analyze_tracks([track])
            self.assertEqual(len(provider.calls), 1, "second call should be served entirely from cache")

    def test_use_cache_false_always_calls_provider(self) -> None:
        with TemporaryDirectory(prefix="automix-service-") as directory:
            provider = _StubProvider()
            service = AnalysisService(provider, settings=AutoMixAnalysisSettings(use_cache=False))
            track = _track(Path(directory), "a.mp3")
            service.analyze_tracks([track])
            service.analyze_tracks([track])
            self.assertEqual(len(provider.calls), 2)

    def test_one_failed_track_does_not_corrupt_other_results(self) -> None:
        with TemporaryDirectory(prefix="automix-service-") as directory:
            good = _track(Path(directory), "good.mp3")
            bad = _track(Path(directory), "bad.mp3")
            provider = _StubProvider(fail_paths=frozenset({bad.file_path}))
            service = AnalysisService(
                provider, cache=AnalysisCache(Path(directory) / "cache", analyzer_id="stub", analyzer_version="1"),
            )
            result = service.analyze_tracks([good, bad])
            self.assertIn(good.id, result.analyses)
            self.assertIn(bad.id, result.failures)
            self.assertNotIn(bad.id, result.analyses)
            self.assertNotIn(good.id, result.failures)

    def test_deterministic_mapping_by_track_id(self) -> None:
        with TemporaryDirectory(prefix="automix-service-") as directory:
            tracks = [_track(Path(directory), f"track{i}.mp3") for i in range(5)]
            provider = _StubProvider()
            service = AnalysisService(
                provider, cache=AnalysisCache(Path(directory) / "cache", analyzer_id="stub", analyzer_version="1"),
            )
            result = service.analyze_tracks(tracks)
            self.assertEqual(set(result.analyses), {track.id for track in tracks})
            for track in tracks:
                self.assertEqual(result.analyses[track.id].track_id, track.id)
                self.assertEqual(result.analyses[track.id].source_path, track.file_path)

    def test_identical_media_is_analyzed_once_for_two_track_ids(self) -> None:
        with TemporaryDirectory(prefix="automix-service-") as directory:
            shared_path = Path(directory) / "shared.mp3"
            shared_path.write_bytes(b"audio")
            track_a = PlaylistTrack(file_path=str(shared_path), title="a", duration_seconds=30.0, id="a")
            track_b = PlaylistTrack(file_path=str(shared_path), title="b", duration_seconds=30.0, id="b")
            provider = _StubProvider()
            service = AnalysisService(
                provider, cache=AnalysisCache(Path(directory) / "cache", analyzer_id="stub", analyzer_version="1"),
            )
            result = service.analyze_tracks([track_a, track_b])
            self.assertEqual(len(provider.calls), 1)
            self.assertEqual(result.analyses["a"].track_id, "a")
            self.assertEqual(result.analyses["b"].track_id, "b")

    def test_cancellation_before_start_skips_all_tracks(self) -> None:
        with TemporaryDirectory(prefix="automix-service-") as directory:
            provider = _StubProvider()
            service = AnalysisService(
                provider, cache=AnalysisCache(Path(directory) / "cache", analyzer_id="stub", analyzer_version="1"),
            )
            track = _track(Path(directory), "a.mp3")
            cancel_event = threading.Event()
            cancel_event.set()
            result = service.analyze_tracks([track], cancel_event=cancel_event)
            self.assertEqual(provider.calls, [])
            self.assertEqual(result.analyses, {})
            self.assertEqual(result.failures, {})

    def test_provider_cancellation_mid_track_is_not_treated_as_failure(self) -> None:
        with TemporaryDirectory(prefix="automix-service-") as directory:
            service = AnalysisService(
                _CancellingProvider(),
                cache=AnalysisCache(Path(directory) / "cache", analyzer_id="cancelling", analyzer_version="1"),
            )
            track = _track(Path(directory), "a.mp3")
            result = service.analyze_tracks([track])
            self.assertEqual(result.analyses, {})
            self.assertEqual(result.failures, {})

    def test_empty_batch_returns_empty_result(self) -> None:
        service = AnalysisService(_StubProvider())
        result = service.analyze_tracks([])
        self.assertEqual(result.analyses, {})
        self.assertEqual(result.failures, {})

    def test_fallback_result_is_not_cached_as_a_provider_success(self) -> None:
        """Regression: a hybrid provider's own-identity cache namespace must
        not be permanently poisoned by one fallback result -- once the
        advanced engine becomes available again, the next analyze_tracks()
        call must actually invoke it, not keep replaying the stale
        fallback it was necessarily written under."""
        with TemporaryDirectory(prefix="automix-service-") as directory:
            provider = _RecoveringHybridProvider()
            cache = AnalysisCache(
                Path(directory) / "cache",
                analyzer_id=provider.provider_id, analyzer_version=provider.version,
            )
            service = AnalysisService(provider, cache=cache)
            track = _track(Path(directory), "a.mp3")

            # First run: the advanced engine is unavailable; the hybrid
            # provider degrades to a basic-analyzer-provenance result.
            result = service.analyze_tracks([track])
            self.assertEqual(len(provider.calls), 1)
            self.assertEqual(result.analyses[track.id].analyzer_id, "basic")

            provider.available = True

            # Second run must re-invoke the provider despite the cache
            # entry that now exists under provider.provider_id's namespace.
            result = service.analyze_tracks([track])
            self.assertEqual(len(provider.calls), 2)
            self.assertEqual(result.analyses[track.id].analyzer_id, "beat_this")

            # Third run: a genuine success is now cached and must be served
            # from it without a third provider call.
            result = service.analyze_tracks([track])
            self.assertEqual(len(provider.calls), 2)
            self.assertEqual(result.analyses[track.id].analyzer_id, "beat_this")

    def test_progress_reports_completion_for_every_track(self) -> None:
        with TemporaryDirectory(prefix="automix-service-") as directory:
            tracks = [_track(Path(directory), f"track{i}.mp3") for i in range(3)]
            service = AnalysisService(
                _StubProvider(),
                cache=AnalysisCache(Path(directory) / "cache", analyzer_id="stub", analyzer_version="1"),
            )
            snapshots: list[tuple[int, int]] = []
            service.analyze_tracks(
                tracks, progress=lambda completed, total, _message: snapshots.append((completed, total)),
            )
            self.assertTrue(snapshots)
            self.assertEqual(snapshots[-1], (3, 3))


if __name__ == "__main__":
    unittest.main()
