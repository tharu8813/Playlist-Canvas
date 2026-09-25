"""Unit tests for BeatThisAnalysisProvider using a fake inference backend.

Never imports/downloads the real torch/beat_this dependency or model (see
roadmap "normal tests must not use the network/a model") -- _load_model is
always patched here. A real-model integration test is opt-in and lives
separately (see test_automix_beat_this_real_model.py), gated behind
PLAYLIST_CANVAS_TEST_BEAT_THIS=1.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from app.automix.analysis.basic import BasicAnalysisProvider
from app.automix.analysis.beat_this import (
    BeatThisAnalysisProvider,
    _beats_per_bar_counts,
    _bpm_from_beats,
    _downbeat_alignment_score,
    _estimate_meter_numerator,
    _meter_confidence,
    _sanitize_timestamps,
)
from app.automix.analysis.provider import AnalysisCancelled
from app.automix.analysis.registry import create_analysis_provider
from app.automix.models import RELIABLE_METER_CONFIDENCE, TrackAnalysis
from app.models.playlist import PlaylistTrack


def _track(name: str = "a.wav", duration_seconds: float = 30.0) -> PlaylistTrack:
    return PlaylistTrack(file_path=name, title=name, duration_seconds=duration_seconds)


def _basic_result(
    track: PlaylistTrack, *, bpm: float | None = 100.0, silent: bool = False,
) -> TrackAnalysis:
    """``silent=True`` reproduces BasicAnalysisProvider's own early return
    for a too-short/silent track (only identity fields set, energy=None).
    ``bpm=None, silent=False`` reproduces librosa finding no *usable* BPM on
    an otherwise fully-decoded, non-silent track (energy IS set) -- the
    distinction BeatThisAnalysisProvider.analyze() now relies on."""
    if silent:
        return TrackAnalysis(
            track_id=track.id, source_path=track.file_path, duration_seconds=track.duration_seconds,
            analyzer_id="basic", analyzer_version="2",
        )
    return TrackAnalysis(
        track_id=track.id, source_path=track.file_path, duration_seconds=track.duration_seconds,
        bpm=bpm, bpm_confidence=0.5 if bpm is not None else 0.0,
        beats=(1.0, 1.6, 2.2) if bpm is not None else (),
        downbeats=(1.0,) if bpm is not None else (),
        meter_numerator=4 if bpm is not None else None,
        meter_denominator=4 if bpm is not None else None,
        meter_confidence=0.3 if bpm is not None else 0.0,
        key="C major", key_confidence=0.4, energy=0.2,
        analyzer_id="basic", analyzer_version="2",
    )


_SIGNAL = np.zeros(22050, dtype=np.float32)


@contextmanager
def _basic(return_value=None, side_effect=None):
    """Stub BasicAnalysisProvider's decode + signal analysis (what the hybrid provider calls)."""
    with (
        patch.object(BasicAnalysisProvider, "_decode_mono_pcm", return_value=_SIGNAL),
        patch.object(BasicAnalysisProvider, "analyze_signal", return_value=return_value, side_effect=side_effect),
    ):
        yield


class _FakeFile2Beats:
    """Stands in for beat_this.inference.Audio2Beats: a callable(signal, sr) -> (beats, downbeats)."""

    def __init__(self, beats: np.ndarray, downbeats: np.ndarray, error: Exception | None = None) -> None:
        self.beats = beats
        self.downbeats = downbeats
        self.error = error
        self.calls: list[int] = []

    def __call__(self, signal: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray]:
        self.calls.append(sample_rate)
        if self.error is not None:
            raise self.error
        return self.beats, self.downbeats


class BeatThisAnalysisProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        # Hermetic: never run the real Demucs detector on these fake files, even
        # when it is installed. Vocal tests inject their own fake ``_vocals``.
        patcher = patch("app.automix.analysis.beat_this.vocal_detection_available", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_provider_identity(self) -> None:
        provider = BeatThisAnalysisProvider(Path("ffmpeg"))
        self.assertEqual(provider.provider_id, "beat_this")
        self.assertTrue(provider.version)

    def test_analyze_replaces_rhythm_fields_with_model_output(self) -> None:
        track = _track(duration_seconds=10.0)
        provider = BeatThisAnalysisProvider(Path("ffmpeg"))
        basic_result = _basic_result(track)
        # A steady 120 BPM grid: 0.5s apart.
        beats = np.array([1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5])
        downbeats = np.array([1.0, 3.0])
        fake_model = _FakeFile2Beats(beats, downbeats)
        with (
            _basic(basic_result),
            patch.object(provider, "_load_model", return_value=fake_model),
        ):
            result = provider.analyze(track, cancel_event=threading.Event())
        self.assertEqual(result.analyzer_id, "beat_this")
        self.assertEqual(result.beats, tuple(beats.tolist()))
        self.assertEqual(result.downbeats, tuple(downbeats.tolist()))
        self.assertAlmostEqual(result.bpm, 120.0, delta=0.5)
        self.assertGreater(result.bpm_confidence, 0.9)  # a perfectly steady grid
        self.assertGreater(result.meter_confidence, 0.5)  # unlocks "reliable" (>= 0.5)
        self.assertEqual(result.meter_numerator, 4)
        self.assertEqual(result.beat_alignment_quality(), "reliable")
        # Fields the hybrid provider reuses verbatim from BasicAnalysisProvider.
        self.assertEqual(result.key, basic_result.key)
        self.assertEqual(result.energy, basic_result.energy)

    def _analyze_with_vocals(self, detect) -> TrackAnalysis:
        track = _track(duration_seconds=10.0)
        provider = BeatThisAnalysisProvider(Path("ffmpeg"))
        provider._vocals = SimpleNamespace(detect=detect)
        fake_model = _FakeFile2Beats(np.arange(1.0, 5.0, 0.5), np.array([1.0, 3.0]))
        with _basic(_basic_result(track)), patch.object(provider, "_load_model", return_value=fake_model):
            return provider.analyze(track, cancel_event=threading.Event())

    def test_detected_vocal_spans_reach_the_analysis(self) -> None:
        result = self._analyze_with_vocals(lambda path, duration, cancel: ((0.5, 3.0), (7.0, 10.0)))
        self.assertEqual(result.vocal_activity, ((0.5, 3.0), (7.0, 10.0)))
        self.assertEqual(result.analyzer_id, "beat_this")

    def test_failed_vocal_detection_stays_unknown_and_is_not_a_cache_hit_later(self) -> None:
        def broken(path, duration, cancel):
            raise OSError("model download failed")

        result = self._analyze_with_vocals(broken)
        self.assertEqual(result.vocal_activity, ())
        self.assertIsNotNone(result.bpm)  # rhythm analysis is unaffected
        self.assertNotEqual(result.analyzer_id, "beat_this")  # AnalysisService retries it next time

    def test_every_beat_reported_as_downbeat_is_kept_but_never_reliable(self) -> None:
        """The exact P1 finding: a homogeneous synthetic click fixture made
        the model report every beat as also a downbeat. This must not be
        trusted as a meter -- but the model's own downbeats array must
        still be reported unmodified (never quietly truncated to
        downbeats[::4] to "fix" it, see module docstring)."""
        track = _track(duration_seconds=60.0)
        provider = BeatThisAnalysisProvider(Path("ffmpeg"))
        basic_result = _basic_result(track)
        beats = np.arange(0.0, 30.0, 0.5)  # steady grid, 60 beats
        downbeats = beats.copy()  # every beat also reported as a downbeat
        fake_model = _FakeFile2Beats(beats, downbeats)
        with (
            _basic(basic_result),
            patch.object(provider, "_load_model", return_value=fake_model),
        ):
            result = provider.analyze(track, cancel_event=threading.Event())
        # The model's beats/downbeats are reported as-is, not discarded.
        self.assertEqual(result.downbeats, tuple(beats.tolist()))
        self.assertEqual(result.analyzer_id, "beat_this")
        # But the implied meter is correctly distrusted.
        self.assertIsNone(result.meter_numerator)
        self.assertLess(result.meter_confidence, RELIABLE_METER_CONFIDENCE)
        self.assertNotEqual(result.beat_alignment_quality(), "reliable")

    def test_skips_inference_when_basic_analysis_found_no_signal(self) -> None:
        """A genuinely too-short/silent track (BasicAnalysisProvider's own
        early return, energy left at its default None): Beat This inference
        must not even be attempted."""
        track = _track(duration_seconds=1.0)
        provider = BeatThisAnalysisProvider(Path("ffmpeg"))
        basic_result = _basic_result(track, silent=True)
        with (
            _basic(basic_result),
            patch.object(provider, "_load_model") as load_model,
        ):
            result = provider.analyze(track, cancel_event=threading.Event())
        load_model.assert_not_called()
        self.assertIs(result, basic_result)

    def test_attempts_inference_when_basic_found_no_bpm_on_a_non_silent_track(self) -> None:
        """Regression: librosa failing to find a usable BPM is not the same
        thing as the track being silent/too-short (energy IS set here) --
        Beat This must still be attempted, and used if it succeeds."""
        track = _track(duration_seconds=30.0)
        provider = BeatThisAnalysisProvider(Path("ffmpeg"))
        basic_result = _basic_result(track, bpm=None)
        self.assertIsNotNone(basic_result.energy)  # sanity: not the silent-track shape
        beats = np.array([1.0, 1.5, 2.0, 2.5, 3.0, 3.5])
        downbeats = np.array([1.0, 3.0])
        fake_model = _FakeFile2Beats(beats, downbeats)
        with (
            _basic(basic_result),
            patch.object(provider, "_load_model", return_value=fake_model) as load_model,
        ):
            result = provider.analyze(track, cancel_event=threading.Event())
        load_model.assert_called_once()
        self.assertEqual(fake_model.calls, [22050])  # the FFmpeg-decoded analysis signal
        self.assertEqual(result.analyzer_id, "beat_this")
        self.assertAlmostEqual(result.bpm, 120.0, delta=0.5)
        self.assertEqual(result.beats, tuple(beats.tolist()))

    def test_falls_back_to_basic_when_beat_this_is_not_installed(self) -> None:
        track = _track()
        provider = BeatThisAnalysisProvider(Path("ffmpeg"))
        basic_result = _basic_result(track)
        with (
            _basic(basic_result),
            patch.object(provider, "_load_model", side_effect=ImportError("No module named 'beat_this'")),
        ):
            result = provider.analyze(track, cancel_event=threading.Event())
        self.assertIs(result, basic_result)

    def test_falls_back_to_basic_when_inference_raises(self) -> None:
        """A corrupted model / inference crash must degrade, not propagate."""
        track = _track()
        provider = BeatThisAnalysisProvider(Path("ffmpeg"))
        basic_result = _basic_result(track)
        fake_model = _FakeFile2Beats(np.array([]), np.array([]), error=RuntimeError("corrupted checkpoint"))
        with (
            _basic(basic_result),
            patch.object(provider, "_load_model", return_value=fake_model),
        ):
            result = provider.analyze(track, cancel_event=threading.Event())
        self.assertIs(result, basic_result)

    def test_falls_back_to_basic_downbeats_when_model_reports_no_downbeats(self) -> None:
        """Beats found, but no downbeats: keep bpm from the model, but keep
        the basic analyzer's own provisional bar guess rather than an empty one."""
        track = _track()
        provider = BeatThisAnalysisProvider(Path("ffmpeg"))
        basic_result = _basic_result(track)
        beats = np.array([1.0, 1.5, 2.0, 2.5, 3.0])
        fake_model = _FakeFile2Beats(beats, np.array([]))
        with (
            _basic(basic_result),
            patch.object(provider, "_load_model", return_value=fake_model),
        ):
            result = provider.analyze(track, cancel_event=threading.Event())
        self.assertAlmostEqual(result.bpm, 120.0, delta=0.5)
        self.assertEqual(result.downbeats, basic_result.downbeats)
        self.assertEqual(result.meter_confidence, basic_result.meter_confidence)

    def test_cancellation_before_inference_raises_analysis_cancelled(self) -> None:
        track = _track()
        provider = BeatThisAnalysisProvider(Path("ffmpeg"))
        basic_result = _basic_result(track)
        cancel_event = threading.Event()

        def load_model_and_cancel():
            cancel_event.set()
            return _FakeFile2Beats(np.array([1.0, 1.5, 2.0]), np.array([1.0]))

        with (
            _basic(basic_result),
            patch.object(provider, "_load_model", side_effect=load_model_and_cancel),
        ):
            with self.assertRaises(AnalysisCancelled):
                provider.analyze(track, cancel_event=cancel_event)

    def test_model_is_loaded_once_and_reused_across_tracks(self) -> None:
        provider = BeatThisAnalysisProvider(Path("ffmpeg"))
        basic_result_a = _basic_result(_track("a.wav"))
        basic_result_b = _basic_result(_track("b.wav"))
        fake_model = _FakeFile2Beats(np.array([1.0, 1.5, 2.0, 2.5]), np.array([1.0]))
        with (
            _basic(side_effect=[basic_result_a, basic_result_b]),
            patch.object(provider, "_load_model", return_value=fake_model) as load_model,
        ):
            provider.analyze(_track("a.wav"), cancel_event=threading.Event())
            provider.analyze(_track("b.wav"), cancel_event=threading.Event())
        # _load_model is called per analyze(); real caching is inside the
        # unpatched _load_model itself (asserted separately below).
        self.assertEqual(load_model.call_count, 2)

    def test_load_model_caches_the_instance_across_calls(self) -> None:
        """Also exercises the real (unmocked) _load_model() lazy-import path,
        against fake torch/beat_this modules -- never the real dependency."""
        provider = BeatThisAnalysisProvider(Path("ffmpeg"))
        calls: list[str] = []

        def fake_file2beats(**kwargs):
            calls.append(kwargs["device"])
            return _FakeFile2Beats(np.array([]), np.array([]))

        fake_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
        fake_beat_this = SimpleNamespace()
        fake_beat_this_inference = SimpleNamespace(Audio2Beats=fake_file2beats)

        with patch.dict(
            "sys.modules",
            {
                "torch": fake_torch,
                "beat_this": fake_beat_this,
                "beat_this.inference": fake_beat_this_inference,
            },
        ):
            first = provider._load_model()
            second = provider._load_model()
        self.assertIs(first, second)
        self.assertEqual(calls, ["cpu"])  # loaded exactly once


class BpmAndMeterConfidenceTests(unittest.TestCase):
    def test_bpm_from_steady_beats_is_high_confidence(self) -> None:
        beats = np.arange(0.0, 10.0, 0.5)  # 120 BPM, perfectly steady
        bpm, confidence = _bpm_from_beats(beats)
        self.assertAlmostEqual(bpm, 120.0, delta=0.5)
        self.assertGreaterEqual(confidence, 0.9)

    def test_bpm_confidence_is_bounded_and_drops_with_jitter(self) -> None:
        rng = np.random.default_rng(0)
        beats = np.cumsum(np.full(20, 0.5) + rng.uniform(-0.15, 0.15, 20))
        bpm, confidence = _bpm_from_beats(beats)
        self.assertIsNotNone(bpm)
        self.assertGreaterEqual(confidence, 0.0)
        self.assertLessEqual(confidence, 1.0)
        self.assertLess(confidence, 0.9)

    def test_too_few_beats_is_unusable(self) -> None:
        bpm, confidence = _bpm_from_beats(np.array([1.0, 1.5]))
        self.assertIsNone(bpm)
        self.assertEqual(confidence, 0.0)

    def test_meter_confidence_bounded_and_rewards_regular_bars(self) -> None:
        beats = np.arange(0.0, 20.0, 0.5)
        downbeats = np.arange(0.0, 20.0, 2.0)  # every 4th beat, perfectly regular
        confidence = _meter_confidence(beats, downbeats)
        self.assertGreaterEqual(confidence, 0.5)  # clears RELIABLE_METER_CONFIDENCE
        self.assertLessEqual(confidence, 1.0)

    def test_meter_confidence_is_zero_with_too_few_downbeats(self) -> None:
        self.assertEqual(_meter_confidence(np.array([1.0, 2.0]), np.array([1.0])), 0.0)

    def test_every_beat_reported_as_a_downbeat_is_never_reliable(self) -> None:
        """The exact bug report: a fully homogeneous/unaccented signal can
        make the model report every beat as also a downbeat
        (beats_per_bar ~= 1) -- this must never be trusted as a reliable
        meter, regardless of how "regular" the (degenerate) bar spacing
        trivially looks."""
        beats = np.arange(0.0, 60.0, 0.5)  # perfectly steady beat grid
        downbeats = beats.copy()  # every beat is also reported as a downbeat
        confidence = _meter_confidence(beats, downbeats)
        self.assertLess(confidence, RELIABLE_METER_CONFIDENCE)

    def test_downbeat_overprediction_regression_100_beats_100_downbeats(self) -> None:
        beats = np.arange(0.0, 50.0, 0.5)  # 100 beats
        downbeats = beats.copy()  # 100 downbeats (every beat)
        self.assertEqual(len(beats), 100)
        self.assertEqual(len(downbeats), 100)
        confidence = _meter_confidence(beats, downbeats)
        self.assertLess(confidence, RELIABLE_METER_CONFIDENCE)

    def test_correctly_spaced_downbeats_regression_100_beats_25_downbeats(self) -> None:
        beats = np.arange(0.0, 50.0, 0.5)  # 100 beats
        downbeats = beats[0::4]  # 25 downbeats, evenly every 4th beat (real 4/4)
        self.assertEqual(len(beats), 100)
        self.assertEqual(len(downbeats), 25)
        confidence = _meter_confidence(beats, downbeats)
        self.assertGreaterEqual(confidence, RELIABLE_METER_CONFIDENCE)


class MeterNumeratorEstimationTests(unittest.TestCase):
    def test_estimates_four_four_from_evenly_spaced_downbeats(self) -> None:
        beats = np.arange(0.0, 20.0, 0.5)
        downbeats = np.arange(0.0, 20.0, 2.0)  # every 4th beat
        numerator, consistency = _estimate_meter_numerator(beats, downbeats)
        self.assertEqual(numerator, 4)
        self.assertGreaterEqual(consistency, 0.9)

    def test_estimates_three_four_from_evenly_spaced_downbeats(self) -> None:
        """roadmap example: a 3/4 fixture must be reported as 3, never
        silently mislabeled as 4/4."""
        beats = np.array([0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0])
        downbeats = np.array([0.0, 1.5, 3.0])
        numerator, consistency = _estimate_meter_numerator(beats, downbeats)
        self.assertEqual(numerator, 3)
        self.assertGreaterEqual(consistency, 0.9)

    def test_one_to_one_downbeats_are_not_a_plausible_bar_length(self) -> None:
        beats = np.arange(0.0, 20.0, 0.5)
        downbeats = beats.copy()
        numerator, _consistency = _estimate_meter_numerator(beats, downbeats)
        self.assertIsNone(numerator)

    def test_inconsistent_bar_counts_yield_no_numerator(self) -> None:
        """Per-bar beat counts disagreeing too much (not just implausible)
        must also refuse to guess, per MINIMUM_BAR_COUNT_CONSISTENCY."""
        # Downbeats spaced 4, 2, 7, 3, 5 beats apart -- no dominant count.
        beats = np.arange(0.0, 21.0, 1.0)  # 21 beats, indices 0..20
        downbeats = beats[[0, 4, 6, 13, 16, 21 - 1]]
        numerator, _consistency = _estimate_meter_numerator(beats, downbeats)
        self.assertIsNone(numerator)

    def test_beats_per_bar_counts_uses_beat_index_span_between_downbeats(self) -> None:
        beats = np.arange(0.0, 12.0, 1.0)
        downbeats = np.array([0.0, 4.0, 8.0])
        counts = _beats_per_bar_counts(beats, downbeats)
        self.assertEqual(list(counts), [4, 4])

    def test_downbeat_alignment_score_penalizes_off_grid_downbeats(self) -> None:
        beats = np.arange(0.0, 10.0, 0.5)
        on_grid = _downbeat_alignment_score(beats, np.array([0.0, 2.0, 4.0]))
        off_grid = _downbeat_alignment_score(beats, np.array([0.2, 2.3, 4.35]))  # far from any beat
        self.assertEqual(on_grid, 1.0)
        self.assertLess(off_grid, 1.0)


class SanitizeTimestampsTests(unittest.TestCase):
    def test_sorts_dedupes_and_clips_to_duration(self) -> None:
        values = np.array([5.0, 1.0, 1.0005, 20.0, -1.0, float("nan")])
        cleaned = _sanitize_timestamps(values, duration_seconds=10.0)
        self.assertEqual(cleaned, (1.0, 5.0, 10.0))


class AnalysisProviderRegistryTests(unittest.TestCase):
    def test_default_provider_id_is_basic(self) -> None:
        provider = create_analysis_provider("basic", Path("ffmpeg"))
        self.assertEqual(provider.provider_id, "basic")

    def test_unknown_provider_id_raises_instead_of_silently_falling_back(self) -> None:
        """A typo'd/unrecognized provider_id is a wiring bug, not a
        deliberate choice -- must not be silently swallowed into "basic"."""
        with self.assertRaises(ValueError):
            create_analysis_provider("nonexistent", Path("ffmpeg"))

    def test_beat_this_provider_id_returns_a_beat_this_provider(self) -> None:
        """Construction alone must not import torch/beat_this (lazy import
        happens inside analyze()/_load_model(), not __init__)."""
        provider = create_analysis_provider("beat_this", Path("ffmpeg"))
        self.assertEqual(provider.provider_id, "beat_this")
        self.assertIsInstance(provider, BeatThisAnalysisProvider)

    def test_auto_is_the_onnx_model_else_the_light_analyzer_never_pytorch(self) -> None:
        # PyTorch analyzers are opt-in by explicit id only (too heavy by default).
        for onnx, expected in ((True, "beat_this_onnx"), (False, "basic")):
            for torch in (False, True):
                with self.subTest(onnx=onnx, beat_this_installed=torch), patch(
                    "app.automix.analysis.registry.beat_this_available", return_value=torch,
                ), patch("app.automix.analysis.beat_this_onnx.onnx_beats_available", return_value=onnx):
                    self.assertEqual(create_analysis_provider("auto", Path("ffmpeg")).provider_id, expected)

    def test_beat_this_available_does_not_import_torch(self) -> None:
        """find_spec-based probing must not trigger the actual heavy import."""
        from app.automix.analysis.registry import beat_this_available
        with patch.dict("sys.modules", {"torch": None}):
            # torch stubbed unimportable: probe must report False, not raise.
            self.assertFalse(beat_this_available())


class BundledCheckpointTests(unittest.TestCase):
    def test_a_frozen_build_loads_its_shipped_checkpoint_and_otherwise_the_name(self) -> None:
        from tempfile import TemporaryDirectory

        from app.automix.analysis.beat_this import BUNDLED_CHECKPOINT_DIRECTORY, bundled_checkpoint

        self.assertIsNone(bundled_checkpoint("final0"))  # not frozen: beat_this resolves the name
        with TemporaryDirectory() as bundle:
            with patch("sys._MEIPASS", bundle, create=True):
                self.assertIsNone(bundled_checkpoint("final0"))  # frozen but not shipped
                shipped = Path(bundle) / BUNDLED_CHECKPOINT_DIRECTORY / "final0.ckpt"
                shipped.parent.mkdir(parents=True)
                shipped.write_bytes(b"weights")
                self.assertEqual(bundled_checkpoint("final0"), str(shipped))
                # The cache identity stays name-based, so bundled and downloaded copies share entries.
                self.assertIn("+final0+", BeatThisAnalysisProvider(Path("ffmpeg")).version)


if __name__ == "__main__":
    unittest.main()
