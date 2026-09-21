from __future__ import annotations

import os
import struct
import subprocess
import threading
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from app.automix.renderer import (
    SAMPLE_RATE,
    AutoMixAudioPipeline,
    AutoMixRenderCancelled,
    AutoMixRenderError,
    _atempo_filters,
    build_filter_graph,
)
from app.timeline.models import TransitionType
from app.timeline.render_plan import AudioRenderClip, AudioRenderPlan, AudioRenderTransition
from app.utils.subprocess_utils import hidden_process_kwargs


def _clip(clip_id: str, track_id: str, start: float, source_out: float, *,
          source_in: float = 0.0, rate: float = 1.0, gain: float = 1.0) -> AudioRenderClip:
    return AudioRenderClip(
        clip_id=clip_id, track_id=track_id, timeline_start=start,
        source_in=source_in, source_out=source_out, playback_rate=rate, gain=gain,
    )


class AtempoFiltersTests(unittest.TestCase):
    def test_identity_rate_needs_no_filter(self) -> None:
        self.assertEqual(_atempo_filters(1.0), [])

    def test_in_range_rate_is_a_single_filter(self) -> None:
        self.assertEqual(_atempo_filters(1.5), ["atempo=1.500000"])

    def test_out_of_range_rate_is_decomposed_within_bounds(self) -> None:
        filters = _atempo_filters(3.0)
        self.assertGreater(len(filters), 1)
        product = 1.0
        for entry in filters:
            factor = float(entry.split("=")[1])
            self.assertGreaterEqual(factor, 0.5)
            self.assertLessEqual(factor, 2.0)
            product *= factor
        self.assertAlmostEqual(product, 3.0, places=4)

    def test_very_slow_rate_is_decomposed_within_bounds(self) -> None:
        filters = _atempo_filters(0.2)
        product = 1.0
        for entry in filters:
            factor = float(entry.split("=")[1])
            self.assertGreaterEqual(factor, 0.5)
            self.assertLessEqual(factor, 2.0)
            product *= factor
        self.assertAlmostEqual(product, 0.2, places=4)


class BuildFilterGraphTests(unittest.TestCase):
    def test_empty_clips_raises(self) -> None:
        with self.assertRaises(AutoMixRenderError):
            build_filter_graph([], [])

    def test_single_clip_has_no_concat_or_crossfade(self) -> None:
        graph, label = build_filter_graph([_clip("a", "a", 0.0, 60.0)], [])
        self.assertEqual(label, "c0")
        self.assertIn("[0:a]atrim=start=0.000000:end=60.000000", graph)
        self.assertNotIn("concat", graph)
        self.assertNotIn("acrossfade", graph)

    def test_adjacent_clips_with_no_transition_use_concat(self) -> None:
        clips = [_clip("a", "a", 0.0, 60.0), _clip("b", "b", 60.0, 60.0)]
        graph, label = build_filter_graph(clips, [])
        self.assertIn("concat=n=2:v=0:a=1", graph)
        self.assertNotIn("acrossfade", graph)
        self.assertEqual(label, "m1")

    def test_gap_between_clips_inserts_silence(self) -> None:
        clips = [_clip("a", "a", 0.0, 60.0), _clip("b", "b", 65.0, 60.0)]
        graph, _label = build_filter_graph(clips, [])
        self.assertIn(f"anullsrc=r={SAMPLE_RATE}:cl=stereo:d=5.000000", graph)
        self.assertEqual(graph.count("concat=n=2:v=0:a=1"), 2)

    def test_transition_uses_acrossfade_with_matching_duration(self) -> None:
        clips = [_clip("a", "a", 0.0, 60.0), _clip("b", "b", 52.0, 60.0, source_in=8.0)]
        transitions = [AudioRenderTransition(
            clip_a="a", clip_b="b", timeline_start=52.0, duration=8.0, type=TransitionType.EQUAL_POWER,
        )]
        graph, _label = build_filter_graph(clips, transitions)
        self.assertIn("acrossfade=d=8.000000:curve1=qsin:curve2=qsin", graph)
        self.assertNotIn("concat", graph)

    def test_crossfade_type_uses_linear_curve(self) -> None:
        clips = [_clip("a", "a", 0.0, 60.0), _clip("b", "b", 57.0, 60.0)]
        transitions = [AudioRenderTransition(
            clip_a="a", clip_b="b", timeline_start=57.0, duration=3.0, type=TransitionType.CROSSFADE,
        )]
        graph, _label = build_filter_graph(clips, transitions)
        self.assertIn("curve1=tri:curve2=tri", graph)

    def test_beat_match_uses_equal_power_style_curve(self) -> None:
        clips = [_clip("a", "a", 0.0, 60.0), _clip("b", "b", 52.0, 60.0)]
        transitions = [AudioRenderTransition(
            clip_a="a", clip_b="b", timeline_start=52.0, duration=8.0, type=TransitionType.BEAT_MATCH,
        )]
        graph, _label = build_filter_graph(clips, transitions)
        self.assertIn("curve1=qsin:curve2=qsin", graph)

    def test_rate_and_gain_are_applied_per_clip(self) -> None:
        clips = [_clip("a", "a", 0.0, 60.0, rate=1.2, gain=0.5)]
        graph, _label = build_filter_graph(clips, [])
        self.assertIn("atempo=1.200000", graph)
        self.assertIn("volume=0.500000", graph)

    def test_three_clip_chain_folds_left_to_right(self) -> None:
        clips = [
            _clip("a", "a", 0.0, 60.0),
            _clip("b", "b", 52.0, 60.0),
            _clip("c", "c", 108.0, 60.0),
        ]
        transitions = [
            AudioRenderTransition("a", "b", 52.0, 8.0, TransitionType.EQUAL_POWER),
            AudioRenderTransition("b", "c", 108.0, 4.0, TransitionType.EQUAL_POWER),
        ]
        graph, label = build_filter_graph(clips, transitions)
        self.assertEqual(graph.count("acrossfade"), 2)
        self.assertEqual(label, "m2")


def _write_tone_wav(path: Path, frequency: float, duration: float, sample_rate: int = SAMPLE_RATE) -> None:
    t = np.linspace(0.0, duration, int(duration * sample_rate), endpoint=False)
    signal = 0.5 * np.sin(2 * np.pi * frequency * t)
    pcm16 = (signal * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(struct.pack(f"<{len(pcm16)}h", *pcm16.tolist()))


@unittest.skipUnless(
    os.environ.get("PLAYLIST_CANVAS_TEST_FFMPEG", "").strip(),
    "Set PLAYLIST_CANVAS_TEST_FFMPEG to run real FFmpeg AutoMix render checks.",
)
class RealAutoMixRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.executable = Path(os.environ["PLAYLIST_CANVAS_TEST_FFMPEG"].strip())
        self._directory = TemporaryDirectory(prefix="automix-render-")
        self.directory = Path(self._directory.name)
        self.addCleanup(self._directory.cleanup)

    def _pipeline(self) -> AutoMixAudioPipeline:
        return AutoMixAudioPipeline(self.executable)

    def test_two_track_crossfade_render_has_the_expected_duration(self) -> None:
        a = self.directory / "a.wav"
        b = self.directory / "b.wav"
        _write_tone_wav(a, 440.0, 20.0)
        _write_tone_wav(b, 660.0, 20.0)
        clips = (_clip("a", "a", 0.0, 20.0), _clip("b", "b", 16.0, 20.0))
        transitions = (AudioRenderTransition("a", "b", 16.0, 4.0, TransitionType.EQUAL_POWER),)
        plan = AudioRenderPlan(clips=clips, transitions=transitions)
        result = self._pipeline().render(plan, {"a": str(a), "b": str(b)}, self.directory / "out")
        self.assertTrue(result.path.is_file())
        self.assertAlmostEqual(result.duration_seconds, 36.0, delta=0.2)

    def test_three_track_cumulative_render_duration(self) -> None:
        paths = {}
        for track_id, freq in (("a", 440.0), ("b", 550.0), ("c", 660.0)):
            path = self.directory / f"{track_id}.wav"
            _write_tone_wav(path, freq, 20.0)
            paths[track_id] = str(path)
        clips = (
            _clip("a", "a", 0.0, 20.0),
            _clip("b", "b", 16.0, 20.0),
            _clip("c", "c", 33.0, 20.0),
        )
        transitions = (
            AudioRenderTransition("a", "b", 16.0, 4.0, TransitionType.EQUAL_POWER),
            AudioRenderTransition("b", "c", 33.0, 3.0, TransitionType.CROSSFADE),
        )
        plan = AudioRenderPlan(clips=clips, transitions=transitions)
        result = self._pipeline().render(plan, paths, self.directory / "out")
        self.assertAlmostEqual(result.duration_seconds, 53.0, delta=0.2)

    def test_explicit_gap_produces_matching_total_duration(self) -> None:
        a = self.directory / "a.wav"
        b = self.directory / "b.wav"
        _write_tone_wav(a, 440.0, 10.0)
        _write_tone_wav(b, 440.0, 10.0)
        clips = (_clip("a", "a", 0.0, 10.0), _clip("b", "b", 15.0, 10.0))
        plan = AudioRenderPlan(clips=clips, transitions=())
        result = self._pipeline().render(plan, {"a": str(a), "b": str(b)}, self.directory / "out")
        self.assertAlmostEqual(result.duration_seconds, 25.0, delta=0.2)

    def test_tempo_adjusted_clip_renders_the_expected_shorter_duration(self) -> None:
        a = self.directory / "a.wav"
        _write_tone_wav(a, 440.0, 20.0)
        clip = _clip("a", "a", 0.0, 20.0, rate=1.25)
        plan = AudioRenderPlan(clips=(clip,), transitions=())
        result = self._pipeline().render(plan, {"a": str(a)}, self.directory / "out")
        self.assertAlmostEqual(result.duration_seconds, 16.0, delta=0.2)

    def test_cancellation_removes_incomplete_output(self) -> None:
        a = self.directory / "a.wav"
        b = self.directory / "b.wav"
        _write_tone_wav(a, 440.0, 20.0)
        _write_tone_wav(b, 440.0, 20.0)
        clips = (_clip("a", "a", 0.0, 20.0), _clip("b", "b", 16.0, 20.0))
        transitions = (AudioRenderTransition("a", "b", 16.0, 4.0, TransitionType.EQUAL_POWER),)
        plan = AudioRenderPlan(clips=clips, transitions=transitions)
        cancel_event = threading.Event()
        cancel_event.set()
        output_directory = self.directory / "out"
        with self.assertRaises(AutoMixRenderCancelled):
            self._pipeline().render(plan, {"a": str(a), "b": str(b)}, output_directory, cancel_event=cancel_event)
        self.assertFalse((output_directory / "automix_mix.m4a").exists())

    def test_equal_power_crossfade_has_no_volume_hole_at_the_midpoint(self) -> None:
        a = self.directory / "a.wav"
        b = self.directory / "b.wav"
        _write_tone_wav(a, 440.0, 20.0)
        _write_tone_wav(b, 440.0, 20.0)
        clips = (_clip("a", "a", 0.0, 20.0), _clip("b", "b", 16.0, 20.0))
        transitions = (AudioRenderTransition("a", "b", 16.0, 4.0, TransitionType.EQUAL_POWER),)
        plan = AudioRenderPlan(clips=clips, transitions=transitions)
        result = self._pipeline().render(plan, {"a": str(a), "b": str(b)}, self.directory / "out")

        pcm_process = subprocess.run(
            [str(self.executable), "-hide_banner", "-loglevel", "error", "-i", str(result.path),
             "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"],
            capture_output=True, **hidden_process_kwargs(),
        )
        signal = np.frombuffer(pcm_process.stdout, dtype=np.float32)

        def rms(start_seconds: float, end_seconds: float) -> float:
            segment = signal[int(start_seconds * SAMPLE_RATE):int(end_seconds * SAMPLE_RATE)]
            return float(np.sqrt(np.mean(np.square(segment)))) if len(segment) else 0.0

        steady_state = rms(2.0, 10.0)
        midpoint = rms(17.9, 18.1)
        # A true volume hole would sound like a dip toward silence at the
        # crossfade center; equal-power curves keep it close to full level.
        self.assertGreater(midpoint, steady_state * 0.6)


if __name__ == "__main__":
    unittest.main()
