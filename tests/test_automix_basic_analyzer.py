from __future__ import annotations

import os
import struct
import threading
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np

from app.automix.analysis.basic import (
    SAMPLE_RATE,
    BasicAnalysisProvider,
    normalize_tempo_octave,
)
from app.automix.analysis.provider import AnalysisCancelled
from app.models.playlist import PlaylistTrack


def _click_signal(bpm: float, duration: float, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """A synthetic metronome click track at ``bpm``, for analyzer unit tests."""
    interval = 60.0 / bpm
    signal = np.zeros(int(duration * sample_rate), dtype=np.float32)
    click_samples = int(0.01 * sample_rate)
    time = 0.0
    while time < duration:
        start = int(time * sample_rate)
        end = min(len(signal), start + click_samples)
        signal[start:end] = 1.0
        time += interval
    return signal


def _write_click_wav(path: Path, bpm: float, duration: float, sample_rate: int = SAMPLE_RATE) -> None:
    signal = _click_signal(bpm, duration, sample_rate)
    pcm16 = (signal * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(struct.pack(f"<{len(pcm16)}h", *pcm16.tolist()))


def _track(path: Path, duration_seconds: float) -> PlaylistTrack:
    return PlaylistTrack(file_path=str(path), title=path.stem, duration_seconds=duration_seconds)


class NormalizeTempoOctaveTests(unittest.TestCase):
    def test_low_tempo_is_doubled_into_range(self) -> None:
        self.assertAlmostEqual(normalize_tempo_octave(37.5), 75.0)
        self.assertAlmostEqual(normalize_tempo_octave(43.5), 87.0)

    def test_high_tempo_is_halved_into_range(self) -> None:
        self.assertAlmostEqual(normalize_tempo_octave(300.0), 150.0)

    def test_values_already_in_range_are_unchanged_even_if_a_half_double_sibling_also_fits(self) -> None:
        # Both 75 and 150 lie inside DEFAULT_TEMPO_RANGE; normalize_tempo_octave
        # only folds a value that is actually outside the range, so it never
        # relabels one in-range tempo as its in-range sibling.
        self.assertAlmostEqual(normalize_tempo_octave(75.0), 75.0)
        self.assertAlmostEqual(normalize_tempo_octave(150.0), 150.0)

    def test_in_range_tempo_is_unchanged(self) -> None:
        self.assertAlmostEqual(normalize_tempo_octave(128.0), 128.0)

    def test_zero_or_negative_bpm_passes_through(self) -> None:
        self.assertEqual(normalize_tempo_octave(0.0), 0.0)
        self.assertEqual(normalize_tempo_octave(-5.0), -5.0)


class BasicAnalysisProviderTests(unittest.TestCase):
    """Exercises the rhythm-analysis logic directly, bypassing real FFmpeg decode."""

    def _analyze_signal(self, provider: BasicAnalysisProvider, signal: np.ndarray, duration: float):
        track = _track(Path("stub.wav"), duration)
        with patch.object(provider, "_decode_mono_pcm", return_value=signal):
            return provider.analyze(track, cancel_event=threading.Event())

    def test_bpm_detected_for_synthetic_clicks(self) -> None:
        provider = BasicAnalysisProvider(Path("ffmpeg"))
        for bpm in (120.0, 128.0, 150.0):
            with self.subTest(bpm=bpm):
                signal = _click_signal(bpm, duration=8.0)
                result = self._analyze_signal(provider, signal, duration=8.0)
                self.assertIsNotNone(result.bpm)
                # Beat trackers can land on a tempo octave; a click track's
                # detected BPM should be within a documented tolerance of the
                # true value OR exactly half/double of it.
                candidates = (bpm, bpm / 2.0, bpm * 2.0)
                self.assertTrue(
                    any(abs(result.bpm - candidate) <= candidate * 0.06 for candidate in candidates),
                    f"bpm={result.bpm} not close to any of {candidates}",
                )
                self.assertGreater(result.bpm_confidence, 0.5)
                self.assertTrue(result.beats)

    def test_silence_returns_no_bpm_not_false_confidence(self) -> None:
        provider = BasicAnalysisProvider(Path("ffmpeg"))
        signal = np.zeros(int(8.0 * SAMPLE_RATE), dtype=np.float32)
        result = self._analyze_signal(provider, signal, duration=8.0)
        self.assertIsNone(result.bpm)
        self.assertEqual(result.bpm_confidence, 0.0)
        self.assertEqual(result.beats, ())

    def test_very_short_signal_returns_no_bpm(self) -> None:
        provider = BasicAnalysisProvider(Path("ffmpeg"))
        signal = _click_signal(120.0, duration=0.5)
        result = self._analyze_signal(provider, signal, duration=0.5)
        self.assertIsNone(result.bpm)
        self.assertEqual(result.beats, ())

    def test_empty_signal_is_handled_without_crashing(self) -> None:
        provider = BasicAnalysisProvider(Path("ffmpeg"))
        result = self._analyze_signal(provider, np.array([], dtype=np.float32), duration=3.0)
        self.assertIsNone(result.bpm)

    def test_downbeats_are_provisional_and_bpm_only_quality(self) -> None:
        provider = BasicAnalysisProvider(Path("ffmpeg"))
        result = self._analyze_signal(provider, _click_signal(128.0, duration=8.0), duration=8.0)
        self.assertTrue(result.downbeats)
        self.assertEqual(result.meter_numerator, 4)
        self.assertLess(result.meter_confidence, 0.5)
        # A provisional downbeat guess must never look "reliable" on its own.
        self.assertEqual(result.beat_alignment_quality(), "bpm_only")

    def test_cancellation_before_decode_raises_without_decoding(self) -> None:
        provider = BasicAnalysisProvider(Path("ffmpeg"))
        track = _track(Path("stub.wav"), 8.0)
        cancel_event = threading.Event()
        cancel_event.set()
        with patch.object(provider, "_decode_mono_pcm") as decode:
            with self.assertRaises(AnalysisCancelled):
                provider.analyze(track, cancel_event=cancel_event)
            decode.assert_not_called()

    def test_missing_ffmpeg_executable_raises_a_controlled_error(self) -> None:
        with TemporaryDirectory(prefix="automix-basic-") as directory:
            source = Path(directory) / "a.wav"
            source.write_bytes(b"not really audio")
            provider = BasicAnalysisProvider(Path(directory) / "does-not-exist.exe")
            with self.assertRaises(RuntimeError):
                provider.analyze(_track(source, 5.0), cancel_event=threading.Event())

    def test_missing_source_file_raises_a_controlled_error(self) -> None:
        provider = BasicAnalysisProvider(Path("ffmpeg"))
        track = _track(Path("does-not-exist.mp3"), 5.0)
        with self.assertRaises(RuntimeError):
            provider.analyze(track, cancel_event=threading.Event())


@unittest.skipUnless(
    os.environ.get("PLAYLIST_CANVAS_TEST_FFMPEG", "").strip(),
    "Set PLAYLIST_CANVAS_TEST_FFMPEG to run real FFmpeg decode + analysis checks.",
)
class RealFFmpegBasicAnalysisProviderTests(unittest.TestCase):
    def test_real_decode_and_analysis_of_a_click_track(self) -> None:
        executable = Path(os.environ["PLAYLIST_CANVAS_TEST_FFMPEG"].strip())
        with TemporaryDirectory(prefix="automix-basic-real-") as directory:
            source = Path(directory) / "click.wav"
            _write_click_wav(source, bpm=128.0, duration=10.0)
            provider = BasicAnalysisProvider(executable)
            result = provider.analyze(_track(source, 10.0), cancel_event=threading.Event())
            self.assertIsNotNone(result.bpm)
            candidates = (128.0, 64.0, 256.0)
            self.assertTrue(any(abs(result.bpm - candidate) <= candidate * 0.06 for candidate in candidates))


if __name__ == "__main__":
    unittest.main()
