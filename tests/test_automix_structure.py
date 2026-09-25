"""Unit tests for structural analysis: models, SonaraStructureProvider (fake API
result, never the real dependency), and StructureAnalysisService (cache/
failure isolation). A real-Sonara integration test is opt-in and lives
separately (see test_automix_structure_real_model.py), gated behind
PLAYLIST_CANVAS_TEST_SONARA=1.
"""

from __future__ import annotations

import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.automix.structure.cache import StructureAnalysisCache
from app.automix.structure.models import TrackSection, TrackStructureAnalysis
from app.automix.structure.provider import StructureAnalysisCancelled
from app.automix.structure.service import StructureAnalysisService
from app.automix.structure.sonara import SonaraStructureProvider, sonara_available
from app.models.playlist import PlaylistTrack


def _track(directory: Path, name: str = "a.wav", duration_seconds: float = 60.0) -> PlaylistTrack:
    path = directory / name
    if not path.exists():
        path.write_bytes(b"audio")
    return PlaylistTrack(file_path=str(path), title=name, duration_seconds=duration_seconds)


# -- TrackSection / TrackStructureAnalysis validation ------------------------

class TrackSectionValidationTests(unittest.TestCase):
    def test_valid_section(self) -> None:
        section = TrackSection(start_seconds=0.0, end_seconds=10.0, energy=0.5)
        self.assertEqual(section.start_seconds, 0.0)

    def test_end_before_start_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TrackSection(start_seconds=10.0, end_seconds=5.0)

    def test_end_equal_start_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TrackSection(start_seconds=5.0, end_seconds=5.0)

    def test_negative_start_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TrackSection(start_seconds=-1.0, end_seconds=5.0)

    def test_negative_energy_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TrackSection(start_seconds=0.0, end_seconds=5.0, energy=-0.1)

    def test_out_of_range_confidence_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TrackSection(start_seconds=0.0, end_seconds=5.0, confidence=1.5)

    def test_empty_label_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TrackSection(start_seconds=0.0, end_seconds=5.0, label="")


class TrackStructureAnalysisValidationTests(unittest.TestCase):
    def test_valid_minimal_result(self) -> None:
        analysis = TrackStructureAnalysis(
            track_id="a", source_path="a.wav", duration_seconds=60.0,
        )
        self.assertEqual(analysis.sections, ())

    def test_valid_full_result(self) -> None:
        analysis = TrackStructureAnalysis(
            track_id="a", source_path="a.wav", duration_seconds=60.0,
            intro_end_seconds=6.0, outro_start_seconds=52.0,
            sections=(
                TrackSection(start_seconds=0.0, end_seconds=6.0, energy=0.2),
                TrackSection(start_seconds=6.0, end_seconds=52.0, energy=0.6),
                TrackSection(start_seconds=52.0, end_seconds=60.0, energy=0.2),
            ),
            energy_curve=(0.1, 0.2, 0.3),
            energy_curve_hop_seconds=0.5,
            analyzer_id="sonara_structure", analyzer_version="1",
        )
        self.assertEqual(len(analysis.sections), 3)

    def test_overlapping_sections_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TrackStructureAnalysis(
                track_id="a", source_path="a.wav", duration_seconds=60.0,
                sections=(
                    TrackSection(start_seconds=0.0, end_seconds=10.0),
                    TrackSection(start_seconds=5.0, end_seconds=15.0),  # overlaps
                ),
            )

    def test_unsorted_sections_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TrackStructureAnalysis(
                track_id="a", source_path="a.wav", duration_seconds=60.0,
                sections=(
                    TrackSection(start_seconds=10.0, end_seconds=20.0),
                    TrackSection(start_seconds=0.0, end_seconds=10.0),
                ),
            )

    def test_section_end_past_duration_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TrackStructureAnalysis(
                track_id="a", source_path="a.wav", duration_seconds=10.0,
                sections=(TrackSection(start_seconds=0.0, end_seconds=15.0),),
            )

    def test_intro_end_out_of_range_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TrackStructureAnalysis(
                track_id="a", source_path="a.wav", duration_seconds=10.0,
                intro_end_seconds=15.0,
            )

    def test_outro_start_negative_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TrackStructureAnalysis(
                track_id="a", source_path="a.wav", duration_seconds=10.0,
                outro_start_seconds=-1.0,
            )

    def test_invalid_energy_curve_value_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TrackStructureAnalysis(
                track_id="a", source_path="a.wav", duration_seconds=10.0,
                energy_curve=(0.1, float("nan")), energy_curve_hop_seconds=0.5,
            )

    def test_negative_energy_curve_value_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TrackStructureAnalysis(
                track_id="a", source_path="a.wav", duration_seconds=10.0,
                energy_curve=(0.1, -0.2), energy_curve_hop_seconds=0.5,
            )

    def test_invalid_hop_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TrackStructureAnalysis(
                track_id="a", source_path="a.wav", duration_seconds=10.0,
                energy_curve=(0.1,), energy_curve_hop_seconds=0.0,
            )

    def test_energy_curve_without_hop_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TrackStructureAnalysis(
                track_id="a", source_path="a.wav", duration_seconds=10.0,
                energy_curve=(0.1, 0.2),
            )

    def test_energy_at_returns_nearest_sample(self) -> None:
        analysis = TrackStructureAnalysis(
            track_id="a", source_path="a.wav", duration_seconds=10.0,
            energy_curve=(0.1, 0.2, 0.3, 0.4), energy_curve_hop_seconds=1.0,
        )
        self.assertAlmostEqual(analysis.energy_at(2.0), 0.3)
        self.assertAlmostEqual(analysis.energy_at(0.0), 0.1)
        self.assertAlmostEqual(analysis.energy_at(100.0), 0.4)  # clamped, not extrapolated

    def test_energy_at_returns_none_without_curve(self) -> None:
        analysis = TrackStructureAnalysis(track_id="a", source_path="a.wav", duration_seconds=10.0)
        self.assertIsNone(analysis.energy_at(5.0))

    def test_cache_field_round_trip(self) -> None:
        original = TrackStructureAnalysis(
            track_id="a", source_path="a.wav", duration_seconds=60.0,
            intro_end_seconds=6.0, outro_start_seconds=52.0,
            sections=(TrackSection(start_seconds=0.0, end_seconds=6.0, energy=0.2, label=None, confidence=0.5),),
            energy_curve=(0.1, 0.2), energy_curve_hop_seconds=0.5,
            analyzer_id="sonara_structure", analyzer_version="1",
        )
        rebuilt = TrackStructureAnalysis.from_cache_fields("a", "a.wav", original.to_cache_fields())
        self.assertEqual(rebuilt, original)


# -- SonaraStructureProvider mapping (fake sonara.analyze_file) --------------

class _FakeSonaraModule:
    """Stands in for the real `sonara` module -- analyze_file(path, **kwargs) -> dict."""

    def __init__(self, result: dict | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def analyze_file(self, path: str, **kwargs) -> dict:
        self.calls.append((path, kwargs))
        if self.error is not None:
            raise self.error
        return self.result


_FAKE_SONARA_RESULT = {
    "duration_sec": 60.0,
    "energy_level": 7,
    "energy_curve": [0.1, 0.2, 0.3, 0.2],
    "energy_curve_hop_sec": 0.5,
    "intro_end_sec": 16.2,
    "outro_start_sec": 51.4,
    "segments": [
        {"start_sec": 0.0, "end_sec": 16.2, "energy": 0.15},
        {"start_sec": 16.2, "end_sec": 51.4, "energy": 0.5},
        {"start_sec": 51.4, "end_sec": 60.0, "energy": 0.2},
    ],
    "provenance": {"schema_version": 6},
}


class SonaraStructureProviderTests(unittest.TestCase):
    def test_provider_identity(self) -> None:
        provider = SonaraStructureProvider()
        self.assertEqual(provider.provider_id, "sonara_structure")
        self.assertTrue(provider.version)

    def test_maps_fake_api_result_to_track_structure_analysis(self) -> None:
        with TemporaryDirectory(prefix="structure-") as directory:
            track = _track(Path(directory))
            provider = SonaraStructureProvider()
            fake_module = _FakeSonaraModule(result=_FAKE_SONARA_RESULT)
            with patch.dict("sys.modules", {"sonara": fake_module}):
                result = provider.analyze(track, cancel_event=threading.Event())
            self.assertEqual(result.track_id, track.id)
            self.assertEqual(result.source_path, track.file_path)
            self.assertEqual(result.duration_seconds, 60.0)
            self.assertAlmostEqual(result.intro_end_seconds, 16.2)
            self.assertAlmostEqual(result.outro_start_seconds, 51.4)
            self.assertEqual(len(result.sections), 3)
            self.assertAlmostEqual(result.sections[0].end_seconds, 16.2)
            self.assertIsNone(result.sections[0].label)  # never fabricated
            self.assertEqual(result.energy_curve, (0.1, 0.2, 0.3, 0.2))
            self.assertAlmostEqual(result.energy_curve_hop_seconds, 0.5)
            self.assertEqual(result.analyzer_id, "sonara_structure")
            self.assertIn("schema6", result.analyzer_version)
            self.assertEqual(fake_module.calls, [(str(Path(track.file_path)), {"features": ["structure"]})])

    def test_with_ffmpeg_the_decoded_pcm_is_analyzed_not_the_file(self) -> None:
        # sonara's own AAC/M4A decode reported every time at twice its value.
        import numpy as np
        from types import SimpleNamespace

        from app.automix.analysis.basic import SAMPLE_RATE, BasicAnalysisProvider

        with TemporaryDirectory(prefix="structure-") as directory:
            track = _track(Path(directory))
            seen: dict[str, object] = {}

            def analyze_signal(y, *, sr, **kwargs):
                seen.update(length=len(y), dtype=y.dtype, sr=sr, kwargs=kwargs)
                return _FAKE_SONARA_RESULT

            def analyze_file(*_args, **_kwargs):
                raise AssertionError("the file must not be decoded by sonara")

            fake_module = SimpleNamespace(analyze_signal=analyze_signal, analyze_file=analyze_file)
            pcm = np.zeros(SAMPLE_RATE * 2, dtype=np.float32)
            with patch.dict("sys.modules", {"sonara": fake_module}), patch.object(
                BasicAnalysisProvider, "_decode_mono_pcm", return_value=pcm,
            ) as decode:
                result = SonaraStructureProvider(Path("ffmpeg")).analyze(track, cancel_event=threading.Event())
            decode.assert_called_once()
            self.assertEqual(seen, {"length": len(pcm), "dtype": np.float32, "sr": SAMPLE_RATE,
                                    "kwargs": {"features": ["structure"]}})
            self.assertAlmostEqual(result.outro_start_seconds, 51.4)

    def test_boundary_overshoot_past_duration_is_clamped(self) -> None:
        """Floating-point overshoot right at track end must not fail
        validation -- clamped into [0, duration], not silently discarded."""
        with TemporaryDirectory(prefix="structure-") as directory:
            track = _track(Path(directory), duration_seconds=60.0)
            overshoot_result = dict(_FAKE_SONARA_RESULT, outro_start_sec=60.0001)
            fake_module = _FakeSonaraModule(result=overshoot_result)
            provider = SonaraStructureProvider()
            with patch.dict("sys.modules", {"sonara": fake_module}):
                result = provider.analyze(track, cancel_event=threading.Event())
            self.assertEqual(result.outro_start_seconds, 60.0)

    def test_missing_sonara_raises_and_is_not_swallowed_here(self) -> None:
        """Unlike BeatThisAnalysisProvider, there is no fallback path --
        the caller (StructureAnalysisService) is responsible for isolating
        this as a per-track failure, never a degraded "success"."""
        with TemporaryDirectory(prefix="structure-") as directory:
            track = _track(Path(directory))
            provider = SonaraStructureProvider()
            with patch.dict("sys.modules", {"sonara": None}):
                with self.assertRaises(ImportError):
                    provider.analyze(track, cancel_event=threading.Event())

    def test_inference_failure_propagates(self) -> None:
        with TemporaryDirectory(prefix="structure-") as directory:
            track = _track(Path(directory))
            fake_module = _FakeSonaraModule(error=RuntimeError("boom"))
            provider = SonaraStructureProvider()
            with patch.dict("sys.modules", {"sonara": fake_module}):
                with self.assertRaises(RuntimeError):
                    provider.analyze(track, cancel_event=threading.Event())

    def test_cancellation_before_analyze_raises(self) -> None:
        with TemporaryDirectory(prefix="structure-") as directory:
            track = _track(Path(directory))
            provider = SonaraStructureProvider()
            cancel_event = threading.Event()
            cancel_event.set()
            with self.assertRaises(StructureAnalysisCancelled):
                provider.analyze(track, cancel_event=cancel_event)

    def test_cancellation_after_analyze_raises(self) -> None:
        with TemporaryDirectory(prefix="structure-") as directory:
            track = _track(Path(directory))
            provider = SonaraStructureProvider()
            cancel_event = threading.Event()

            class _CancellingModule(_FakeSonaraModule):
                def analyze_file(self, path, **kwargs):
                    cancel_event.set()
                    return super().analyze_file(path, **kwargs)

            fake_module = _CancellingModule(result=_FAKE_SONARA_RESULT)
            with patch.dict("sys.modules", {"sonara": fake_module}):
                with self.assertRaises(StructureAnalysisCancelled):
                    provider.analyze(track, cancel_event=cancel_event)

    def test_malformed_sonara_output_raises_valueerror_not_silently_accepted(self) -> None:
        """Overlapping segments from a malformed Sonara result must be
        rejected (TrackStructureAnalysis validation), never handed to a
        planner as-is."""
        with TemporaryDirectory(prefix="structure-") as directory:
            track = _track(Path(directory))
            malformed_result = dict(_FAKE_SONARA_RESULT, segments=[
                {"start_sec": 0.0, "end_sec": 30.0, "energy": 0.1},
                {"start_sec": 10.0, "end_sec": 40.0, "energy": 0.2},  # overlaps
            ])
            fake_module = _FakeSonaraModule(result=malformed_result)
            provider = SonaraStructureProvider()
            with patch.dict("sys.modules", {"sonara": fake_module}):
                with self.assertRaises(ValueError):
                    provider.analyze(track, cancel_event=threading.Event())


class SonaraAvailableTests(unittest.TestCase):
    def test_reports_false_when_module_unimportable(self) -> None:
        with patch.dict("sys.modules", {"sonara": None}):
            self.assertFalse(sonara_available())


# -- StructureAnalysisService: cache + failure isolation ----------------------

class _StubStructureProvider:
    provider_id = "stub_structure"
    version = "1"

    def __init__(self, *, fail_paths: frozenset[str] = frozenset()) -> None:
        self.fail_paths = fail_paths
        self.calls: list[str] = []

    def analyze(self, track, *, cancel_event, progress=None) -> TrackStructureAnalysis:
        self.calls.append(track.file_path)
        if track.file_path in self.fail_paths:
            raise RuntimeError("boom")
        return TrackStructureAnalysis(
            track_id=track.id, source_path=track.file_path, duration_seconds=track.duration_seconds,
            intro_end_seconds=5.0, analyzer_id=self.provider_id, analyzer_version=self.version,
        )


class StructureAnalysisServiceTests(unittest.TestCase):
    def test_cache_miss_calls_provider_and_populates_cache(self) -> None:
        with TemporaryDirectory(prefix="structure-service-") as directory:
            provider = _StubStructureProvider()
            cache = StructureAnalysisCache(
                Path(directory) / "cache", analyzer_id="stub_structure", analyzer_version="1",
            )
            service = StructureAnalysisService(provider, cache=cache)
            track = _track(Path(directory))
            result = service.analyze_tracks([track])
            self.assertEqual(len(provider.calls), 1)
            self.assertIn(track.id, result.analyses)
            self.assertIsNotNone(cache.load(track.file_path))

    def test_cache_hit_skips_provider(self) -> None:
        with TemporaryDirectory(prefix="structure-service-") as directory:
            provider = _StubStructureProvider()
            cache = StructureAnalysisCache(
                Path(directory) / "cache", analyzer_id="stub_structure", analyzer_version="1",
            )
            service = StructureAnalysisService(provider, cache=cache)
            track = _track(Path(directory))
            service.analyze_tracks([track])
            service.analyze_tracks([track])
            self.assertEqual(len(provider.calls), 1)

    def test_provider_version_change_invalidates_the_cache(self) -> None:
        with TemporaryDirectory(prefix="structure-service-") as directory:
            root = Path(directory) / "cache"
            track = _track(Path(directory))
            provider_v1 = _StubStructureProvider()
            StructureAnalysisService(
                provider_v1, cache=StructureAnalysisCache(root, analyzer_id="stub_structure", analyzer_version="1"),
            ).analyze_tracks([track])

            provider_v2 = _StubStructureProvider()
            provider_v2.version = "2"  # e.g. a Sonara package upgrade
            service_v2 = StructureAnalysisService(
                provider_v2, cache=StructureAnalysisCache(root, analyzer_id="stub_structure", analyzer_version="2"),
            )
            service_v2.analyze_tracks([track])
            self.assertEqual(len(provider_v2.calls), 1)  # re-analyzed, not served stale v1 cache

    def test_one_failed_track_does_not_corrupt_other_results(self) -> None:
        with TemporaryDirectory(prefix="structure-service-") as directory:
            good = _track(Path(directory), "good.wav")
            bad = _track(Path(directory), "bad.wav")
            provider = _StubStructureProvider(fail_paths=frozenset({bad.file_path}))
            service = StructureAnalysisService(
                provider,
                cache=StructureAnalysisCache(
                    Path(directory) / "cache", analyzer_id="stub_structure", analyzer_version="1",
                ),
            )
            result = service.analyze_tracks([good, bad])
            self.assertIn(good.id, result.analyses)
            self.assertIn(bad.id, result.failures)
            self.assertNotIn(bad.id, result.analyses)

    def test_failure_is_never_cached_as_success(self) -> None:
        """Regression, same spirit as the Beat This! fallback-cache-poisoning
        fix: a failed structure analysis must never be servable as a cache
        hit later, since there is no fallback result for it to have cached
        in the first place -- confirms the "only cache on success" path."""
        with TemporaryDirectory(prefix="structure-service-") as directory:
            track = _track(Path(directory))
            provider = _StubStructureProvider(fail_paths=frozenset({track.file_path}))
            cache = StructureAnalysisCache(
                Path(directory) / "cache", analyzer_id="stub_structure", analyzer_version="1",
            )
            service = StructureAnalysisService(provider, cache=cache)
            result = service.analyze_tracks([track])
            self.assertIn(track.id, result.failures)
            self.assertIsNone(cache.load(track.file_path))  # nothing was cached

            # The dependency "recovers" (no longer in fail_paths); a second
            # run must actually re-invoke the provider, not replay anything.
            provider.fail_paths = frozenset()
            result = service.analyze_tracks([track])
            self.assertIn(track.id, result.analyses)
            self.assertEqual(len(provider.calls), 2)

    def test_cancellation_before_start_skips_all_tracks(self) -> None:
        with TemporaryDirectory(prefix="structure-service-") as directory:
            provider = _StubStructureProvider()
            service = StructureAnalysisService(provider, use_cache=False)
            track = _track(Path(directory))
            cancel_event = threading.Event()
            cancel_event.set()
            result = service.analyze_tracks([track], cancel_event=cancel_event)
            self.assertEqual(provider.calls, [])
            self.assertEqual(result.analyses, {})
            self.assertEqual(result.failures, {})

    def test_empty_batch_returns_empty_result(self) -> None:
        service = StructureAnalysisService(_StubStructureProvider(), use_cache=False)
        result = service.analyze_tracks([])
        self.assertEqual(result.analyses, {})
        self.assertEqual(result.failures, {})

    def test_identical_media_is_analyzed_once_for_two_track_ids(self) -> None:
        with TemporaryDirectory(prefix="structure-service-") as directory:
            shared_path = Path(directory) / "shared.wav"
            shared_path.write_bytes(b"audio")
            track_a = PlaylistTrack(file_path=str(shared_path), title="a", duration_seconds=30.0, id="a")
            track_b = PlaylistTrack(file_path=str(shared_path), title="b", duration_seconds=30.0, id="b")
            provider = _StubStructureProvider()
            service = StructureAnalysisService(provider, use_cache=False)
            result = service.analyze_tracks([track_a, track_b])
            self.assertEqual(len(provider.calls), 1)
            self.assertEqual(result.analyses["a"].track_id, "a")
            self.assertEqual(result.analyses["b"].track_id, "b")


if __name__ == "__main__":
    unittest.main()
