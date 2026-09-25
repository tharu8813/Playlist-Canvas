"""Beat This! via ONNX Runtime: chunking/peak picking, and the shipped model end to end.

The model tests run whenever onnxruntime and the committed model file are
present (no network, no PyTorch). Parity with the PyTorch model was measured
separately on real music (F 0.997 beats / 0.995 downbeats, see
app/automix/analysis/beat_this_onnx.py); these check the wiring on a
synthetic accented click track.
"""

from __future__ import annotations

import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from app.automix.analysis.basic import SAMPLE_RATE
from app.automix.analysis.beat_this_onnx import (
    FPS,
    BeatThisOnnxAnalysisProvider,
    beats_from_logits,
    chunk_starts,
    onnx_beats_available,
    peak_times,
)
from app.models.playlist import PlaylistTrack


def accented_clicks(bpm: float, seconds: float) -> np.ndarray:
    """Beat 1 of each 4/4 bar a loud low thump, the others quiet high clicks, over a quiet bed."""
    signal = 0.04 * np.sin(2 * np.pi * 220.0 * np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE)
    click = np.arange(int(0.09 * SAMPLE_RATE))
    envelope = np.exp(-30.0 * click / SAMPLE_RATE)
    for index, time in enumerate(np.arange(0.0, seconds - 0.1, 60.0 / bpm)):
        downbeat = index % 4 == 0
        start = int(time * SAMPLE_RATE)
        tone = np.sin(2 * np.pi * (130.0 if downbeat else 1400.0) * click / SAMPLE_RATE)
        signal[start:start + len(click)] += (0.9 if downbeat else 0.3) * envelope * tone
    return np.clip(signal, -1.0, 1.0).astype(np.float32)


class ChunkingAndPeakTests(unittest.TestCase):
    def test_chunks_overlap_by_two_borders_and_the_last_ends_the_piece(self) -> None:
        self.assertEqual(chunk_starts(1000), [-6])
        self.assertEqual(chunk_starts(3000), [-6, 1482, 1506])

    def test_adjacent_peak_frames_merge_and_downbeats_snap_to_beats(self) -> None:
        logits = np.full(200, -5.0)
        logits[[50, 51, 120]] = 3.0
        self.assertTrue(np.allclose(peak_times(logits), [50.5 / FPS, 120 / FPS]))
        _, downbeats = beats_from_logits(logits, np.where(np.arange(200) == 119, 2.0, -5.0))
        self.assertTrue(np.allclose(downbeats, [120 / FPS]))

    def test_no_peak_above_half_probability_means_no_beats(self) -> None:
        beats, downbeats = beats_from_logits(np.full(100, -1.0), np.full(100, -1.0))
        self.assertEqual((len(beats), len(downbeats)), (0, 0))


@unittest.skipUnless(onnx_beats_available(), "onnxruntime or the Beat This ONNX model is not installed")
class ShippedModelTests(unittest.TestCase):
    def analyze(self, bpm: float, seconds: float):
        with patch("app.automix.analysis.beat_this.vocal_detection_available", return_value=False):
            provider = BeatThisOnnxAnalysisProvider(Path("ffmpeg"))
        track = PlaylistTrack("clicks.wav", "Clicks", duration_seconds=seconds)
        with patch.object(provider._basic, "_decode_mono_pcm", return_value=accented_clicks(bpm, seconds)):
            return provider.analyze(track, cancel_event=threading.Event())

    def test_accented_track_longer_than_one_chunk_is_a_reliable_four_four(self) -> None:
        result = self.analyze(128.0, 70.0)  # 3500 frames: three overlapping chunks
        self.assertEqual(result.analyzer_id, "beat_this_onnx")
        self.assertAlmostEqual(result.bpm, 128.0, delta=1.0)
        self.assertEqual(result.meter_numerator, 4)
        self.assertEqual(result.beat_alignment_quality(), "reliable")
        # Downbeats on the loud thumps, not on a quiet click.
        bar = 4 * 60.0 / 128.0
        phase = np.remainder(np.asarray(result.downbeats) + bar / 8, bar) - bar / 8
        self.assertLess(float(np.max(np.abs(phase))), 0.05)

    def test_short_track_fits_one_padded_chunk(self) -> None:
        result = self.analyze(100.0, 20.0)
        self.assertAlmostEqual(result.bpm, 100.0, delta=1.0)

    def test_cache_identity_names_the_model_and_runtime(self) -> None:
        version = BeatThisOnnxAnalysisProvider(Path("ffmpeg")).version
        self.assertIn("beat_this_final0_int8", version)
        self.assertIn("+ort", version)


if __name__ == "__main__":
    unittest.main()
