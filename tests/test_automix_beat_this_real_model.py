"""Opt-in integration test against the REAL beat_this/torch dependency and model.

Skipped by default: normal test runs (and CI) must never download a model
or hit the network. Set PLAYLIST_CANVAS_TEST_BEAT_THIS=1 to run this on a
machine with `pip install beat-this` already done and network access for
the (one-time, cached) model download.

_write_accented_click_wav below (P1.5) replaces an earlier, fully
homogeneous click fixture: every beat was an identical click with no bar
accent at all, so there was no signal in the audio for a downbeat model to
distinguish bar-starts from other beats -- the model then reported every
beat as also a downbeat, and BeatThisAnalysisProvider (before P1.5)
uncritically accepted that as a "reliable" 4/4 meter. This fixture gives
beat 1 of each bar a distinct low-frequency accent so there is an actual
downbeat to detect, letting these tests validate real meter estimation
rather than just "beats/downbeats are non-empty".
"""

from __future__ import annotations

import os
import struct
import threading
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from app.automix.analysis.basic import SAMPLE_RATE
from app.automix.analysis.beat_this import BeatThisAnalysisProvider
from app.automix.models import RELIABLE_METER_CONFIDENCE
from app.models.playlist import PlaylistTrack

_ENABLED = os.environ.get("PLAYLIST_CANVAS_TEST_BEAT_THIS") == "1"


def _write_accented_click_wav(
    path: Path, bpm: float, duration: float, beats_per_bar: int = 4,
    sample_rate: int = SAMPLE_RATE,
) -> None:
    """A click track with a distinct accent on beat 1 of every bar.

    Beat 1: louder, low-frequency thump. Beats 2..N: quieter, higher-pitched
    click. A quiet continuous tone underneath keeps the track from reading
    as silent between clicks.
    """
    interval = 60.0 / bpm
    signal = np.zeros(int(duration * sample_rate), dtype=np.float32)
    click_duration = 0.09
    click_samples = int(click_duration * sample_rate)
    envelope = np.exp(-30.0 * np.arange(click_samples) / sample_rate)
    time = 0.0
    beat_index = 0
    while time < duration:
        start = int(time * sample_rate)
        end = min(len(signal), start + click_samples)
        n = end - start
        if n > 0:
            is_downbeat = beat_index % beats_per_bar == 0
            amplitude = 0.9 if is_downbeat else 0.3
            frequency = 130.0 if is_downbeat else 1400.0
            click_t = np.arange(n) / sample_rate
            click = amplitude * envelope[:n] * np.sin(2 * np.pi * frequency * click_t)
            signal[start:end] += click.astype(np.float32)
        time += interval
        beat_index += 1
    bed = 0.04 * np.sin(2 * np.pi * 220.0 * np.arange(len(signal)) / sample_rate).astype(np.float32)
    signal = np.clip(signal + bed, -1.0, 1.0)
    pcm16 = (signal * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(struct.pack(f"<{len(pcm16)}h", *pcm16.tolist()))


@unittest.skipUnless(
    _ENABLED, "Set PLAYLIST_CANVAS_TEST_BEAT_THIS=1 to run against the real beat_this/torch model.",
)
class RealBeatThisModelTests(unittest.TestCase):
    def test_accented_four_four_track_is_detected_as_a_reliable_meter(self) -> None:
        """Strong, specific assertions (not just "beats/downbeats are
        non-empty"): BPM near the target, a real bar structure (fewer
        downbeats than beats, roughly 4 beats/bar), and "reliable" quality
        -- on a fixture that actually gives the model bar-accent signal to
        work with."""
        executable = Path(os.environ["PLAYLIST_CANVAS_TEST_FFMPEG"].strip())
        with TemporaryDirectory(prefix="beat-this-real-") as directory:
            path = Path(directory) / "accented.wav"
            target_bpm = 128.0
            duration = 60.0
            _write_accented_click_wav(path, bpm=target_bpm, duration=duration)
            track = PlaylistTrack(str(path), "Accented", duration_seconds=duration)
            provider = BeatThisAnalysisProvider(executable)
            result = provider.analyze(track, cancel_event=threading.Event())

            self.assertEqual(result.analyzer_id, "beat_this")
            self.assertGreater(len(result.beats), 0)
            self.assertGreater(len(result.downbeats), 0)
            self.assertLess(
                len(result.downbeats), len(result.beats),
                "every beat was reported as a downbeat -- the exact P1 regression",
            )
            self.assertIsNotNone(result.bpm)
            self.assertAlmostEqual(result.bpm, target_bpm, delta=4.0)
            beats_per_bar = len(result.beats) / len(result.downbeats)
            self.assertAlmostEqual(beats_per_bar, 4.0, delta=0.75)
            self.assertEqual(result.meter_numerator, 4)
            self.assertGreaterEqual(result.meter_confidence, RELIABLE_METER_CONFIDENCE)
            self.assertEqual(result.beat_alignment_quality(), "reliable")

    def test_two_tracks_at_different_bpm_are_both_measured_accurately(self) -> None:
        """The earlier (homogeneous-fixture) manual check had both a 128
        and a 132 BPM synthetic track converge to the same reported BPM.
        Re-checked here with the accented fixture and a real tolerance
        assertion per track, instead of being waved off as "synthetic"."""
        executable = Path(os.environ["PLAYLIST_CANVAS_TEST_FFMPEG"].strip())
        with TemporaryDirectory(prefix="beat-this-real-") as directory:
            directory = Path(directory)
            duration = 45.0
            for name, target_bpm in (("a.wav", 128.0), ("b.wav", 132.0)):
                path = directory / name
                _write_accented_click_wav(path, bpm=target_bpm, duration=duration)
                track = PlaylistTrack(str(path), name, duration_seconds=duration)
                provider = BeatThisAnalysisProvider(executable)
                result = provider.analyze(track, cancel_event=threading.Event())
                with self.subTest(track=name, target_bpm=target_bpm):
                    self.assertIsNotNone(result.bpm)
                    self.assertAlmostEqual(result.bpm, target_bpm, delta=4.0)

    def test_compiles_into_a_valid_automix_plan_and_selects_beat_match(self) -> None:
        executable = Path(os.environ["PLAYLIST_CANVAS_TEST_FFMPEG"].strip())
        from app.automix.planner import compile_automix
        from app.automix.renderer import AutoMixAudioPipeline
        from app.automix.settings import AutoMixTransitionSettings
        from app.timeline.models import TransitionType
        from app.timeline.render_plan import validate_compiled_render_plan

        with TemporaryDirectory(prefix="beat-this-real-") as directory:
            directory = Path(directory)
            duration = 45.0
            a_path, b_path = directory / "a.wav", directory / "b.wav"
            _write_accented_click_wav(a_path, bpm=128.0, duration=duration)
            _write_accented_click_wav(b_path, bpm=128.0, duration=duration)
            tracks = [
                PlaylistTrack(str(a_path), "A", duration_seconds=duration),
                PlaylistTrack(str(b_path), "B", duration_seconds=duration),
            ]
            provider = BeatThisAnalysisProvider(executable)
            analyses = {
                track.id: provider.analyze(track, cancel_event=threading.Event())
                for track in tracks
            }
            for track in tracks:
                self.assertEqual(analyses[track.id].beat_alignment_quality(), "reliable")
            plan = compile_automix(tracks, analyses, AutoMixTransitionSettings(enabled=True))
            validate_compiled_render_plan(plan)  # raises on any invariant violation
            self.assertTrue(plan.audio.transitions)
            self.assertEqual(plan.audio.transitions[0].type, TransitionType.BEAT_MATCH)
            output_directory = directory / "out"
            prepared = AutoMixAudioPipeline(executable).render(
                plan.audio, {track.id: track.file_path for track in tracks}, output_directory,
            )
            self.assertTrue(prepared.path.is_file())
            self.assertAlmostEqual(prepared.duration_seconds, plan.duration_seconds, delta=0.5)


if __name__ == "__main__":
    unittest.main()
