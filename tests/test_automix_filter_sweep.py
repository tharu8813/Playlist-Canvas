from __future__ import annotations

import os
import subprocess
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from app.automix.renderer import (
    SAMPLE_RATE,
    SWEEP_CEILING_HZ,
    SWEEP_FLOOR_HZ,
    AutoMixAudioPipeline,
    build_filter_graph,
    sweep_cutoffs,
)
from app.timeline.models import TransitionType
from app.timeline.render_plan import AudioRenderClip, AudioRenderPlan, AudioRenderTransition, TransitionDsp


def _clip(track_id: str, start: float, seconds: float, rate: float = 1.0) -> AudioRenderClip:
    return AudioRenderClip(track_id, track_id, start, 0.0, seconds, playback_rate=rate)


def _sweep(a: str, b: str, start: float, duration: float) -> AudioRenderTransition:
    return AudioRenderTransition(a, b, start, duration, TransitionType.BEAT_MATCH, dsp=TransitionDsp.FILTER_SWEEP)


class SweepGraphTests(unittest.TestCase):
    def test_cutoffs_run_down_on_the_incoming_side_and_up_on_the_outgoing_side(self) -> None:
        initial, incoming = sweep_cutoffs(30.0, 10.0, None)
        self.assertEqual(initial, SWEEP_CEILING_HZ)
        times, cutoffs = zip(*incoming)
        self.assertAlmostEqual(times[0], 1.0)
        self.assertAlmostEqual(times[-1], 8.0)
        self.assertAlmostEqual(cutoffs[-1], SWEEP_FLOOR_HZ)
        self.assertTrue(all(a > b for a, b in zip(cutoffs, cutoffs[1:])))
        initial, outgoing = sweep_cutoffs(30.0, None, 10.0)
        self.assertEqual(initial, SWEEP_FLOOR_HZ)
        times, cutoffs = zip(*outgoing)
        self.assertAlmostEqual(times[0], 22.0)
        self.assertAlmostEqual(times[-1], 29.0)
        self.assertTrue(all(a < b for a, b in zip(cutoffs, cutoffs[1:])))

    def test_graph_gives_each_swept_clip_its_own_named_highpass_and_sums_the_overlap(self) -> None:
        clips = (_clip("a", 0.0, 30.0), _clip("b", 22.0, 30.0))
        graph, _label = build_filter_graph(clips, (_sweep("a", "b", 22.0, 8.0),))
        self.assertIn("highpass@sweep0=f=10.00", graph)
        self.assertIn("highpass@sweep1=f=4000.00", graph)
        self.assertIn("asetnsamples=n=480:p=0", graph)
        self.assertIn("acrossfade=d=8.000000:curve1=nofade:curve2=nofade", graph)
        self.assertIn("alimiter", graph)
        self.assertNotIn("acrossover", graph)  # no band split
        legacy, _ = build_filter_graph(clips, (_sweep("a", "b", 22.0, 8.0),), transition_dsp=False)
        self.assertNotIn("asendcmd", legacy)

    def test_graph_is_deterministic(self) -> None:
        clips = (_clip("a", 0.0, 30.0), _clip("b", 22.0, 30.0))
        self.assertEqual(build_filter_graph(clips, (_sweep("a", "b", 22.0, 8.0),)),
                         build_filter_graph(clips, (_sweep("a", "b", 22.0, 8.0),)))


def _write_noise_wav(path: Path, seconds: float, seed: int) -> None:
    samples = np.random.default_rng(seed).normal(0.0, 0.15, int(seconds * SAMPLE_RATE))
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())


@unittest.skipUnless(os.environ.get("PLAYLIST_CANVAS_TEST_FFMPEG", "").strip(), "Real FFmpeg is opt-in")
class RealSweepRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ffmpeg = Path(os.environ["PLAYLIST_CANVAS_TEST_FFMPEG"].strip())
        directory = TemporaryDirectory(prefix="automix-sweep-")
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.paths = {}
        for seed, name in enumerate("abc"):
            self.paths[name] = str(self.directory / f"{name}.wav")
            _write_noise_wav(Path(self.paths[name]), 30.0, seed)

    def decode(self, path: Path) -> np.ndarray:
        raw = subprocess.run([str(self.ffmpeg), "-v", "error", "-i", str(path), "-f", "f32le", "pipe:1"],
                             capture_output=True, check=True).stdout
        return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2)

    def test_chain_with_a_rate_shifted_clip_keeps_exact_duration_stereo_and_headroom(self) -> None:
        clips = (_clip("a", 0.0, 30.0), _clip("b", 22.0, 30.0, rate=1.05), _clip("c", 42.0, 30.0))
        b_end = 22.0 + 30.0 / 1.05
        transitions = (_sweep("a", "b", 22.0, 8.0), _sweep("b", "c", 42.0, b_end - 42.0))
        result = AutoMixAudioPipeline(self.ffmpeg).render(
            AudioRenderPlan(clips, transitions), self.paths, self.directory / "out", container="flac",
        )
        self.assertAlmostEqual(result.duration_seconds, 72.0, delta=0.05)
        audio = self.decode(result.path)
        self.assertEqual(audio.shape[1], 2)
        self.assertLessEqual(float(np.max(np.abs(audio))), 0.98)

    def test_outgoing_lows_are_gone_by_the_end_of_the_sweep(self) -> None:
        clips = (_clip("a", 0.0, 30.0), _clip("b", 22.0, 30.0))
        silent = str(self.directory / "silence.wav")
        with wave.open(silent, "wb") as wav_file:  # an all-silent incoming track isolates the outgoing side
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(SAMPLE_RATE)
            wav_file.writeframes(bytes(2 * 30 * SAMPLE_RATE))
        result = AutoMixAudioPipeline(self.ffmpeg).render(
            AudioRenderPlan(clips, (_sweep("a", "b", 22.0, 8.0),)), {"a": self.paths["a"], "b": silent},
            self.directory / "out", container="flac",
        )
        mono = self.decode(result.path).mean(axis=1)

        def low_power(start: float) -> float:
            block = mono[int(start * SAMPLE_RATE):int((start + 0.5) * SAMPLE_RATE)]
            spectrum = np.abs(np.fft.rfft(block)) ** 2
            return float(spectrum[np.fft.rfftfreq(len(block), 1 / SAMPLE_RATE) < 150].sum())

        drop_db = 10 * np.log10(low_power(20.0) / max(low_power(28.4), 1e-12))
        self.assertGreater(drop_db, 30.0)


if __name__ == "__main__":
    unittest.main()
