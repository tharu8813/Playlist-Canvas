"""Opt-in integration test against the REAL beat_this/torch dependency and model.

Skipped by default: normal test runs (and CI) must never download a model
or hit the network. Set PLAYLIST_CANVAS_TEST_BEAT_THIS=1 to run this on a
machine with `pip install beat-this` already done and network access for
the (one-time, cached) model download.
"""

from __future__ import annotations

import os
import struct
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from app.automix.analysis.basic import SAMPLE_RATE
from app.automix.analysis.beat_this import BeatThisAnalysisProvider
from app.models.playlist import PlaylistTrack

_ENABLED = os.environ.get("PLAYLIST_CANVAS_TEST_BEAT_THIS") == "1"


def _write_click_wav(path: Path, bpm: float, duration: float, sample_rate: int = SAMPLE_RATE) -> None:
    interval = 60.0 / bpm
    signal = np.zeros(int(duration * sample_rate), dtype=np.float32)
    click_samples = int(0.01 * sample_rate)
    time = 0.0
    while time < duration:
        start = int(time * sample_rate)
        end = min(len(signal), start + click_samples)
        signal[start:end] = 1.0
        time += interval
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
    def test_inference_produces_beats_downbeats_and_a_valid_track_analysis(self) -> None:
        executable = Path(os.environ["PLAYLIST_CANVAS_TEST_FFMPEG"].strip())
        with TemporaryDirectory(prefix="beat-this-real-") as directory:
            path = Path(directory) / "click.wav"
            _write_click_wav(path, bpm=128.0, duration=20.0)
            track = PlaylistTrack(str(path), "Click", duration_seconds=20.0)
            provider = BeatThisAnalysisProvider(executable)
            import threading
            result = provider.analyze(track, cancel_event=threading.Event())
            self.assertEqual(result.analyzer_id, "beat_this")
            self.assertGreater(len(result.beats), 0)
            self.assertGreater(len(result.downbeats), 0)
            self.assertIsNotNone(result.bpm)
            self.assertGreater(result.bpm_confidence, 0.0)

    def test_compiles_into_a_valid_automix_plan_and_renders(self) -> None:
        executable = Path(os.environ["PLAYLIST_CANVAS_TEST_FFMPEG"].strip())
        from app.automix.planner import compile_automix
        from app.automix.renderer import AutoMixAudioPipeline
        from app.automix.settings import AutoMixTransitionSettings
        from app.timeline.render_plan import validate_compiled_render_plan

        with TemporaryDirectory(prefix="beat-this-real-") as directory:
            directory = Path(directory)
            a_path, b_path = directory / "a.wav", directory / "b.wav"
            _write_click_wav(a_path, bpm=128.0, duration=20.0)
            _write_click_wav(b_path, bpm=128.0, duration=20.0)
            tracks = [
                PlaylistTrack(str(a_path), "A", duration_seconds=20.0),
                PlaylistTrack(str(b_path), "B", duration_seconds=20.0),
            ]
            provider = BeatThisAnalysisProvider(executable)
            import threading
            analyses = {
                track.id: provider.analyze(track, cancel_event=threading.Event())
                for track in tracks
            }
            plan = compile_automix(tracks, analyses, AutoMixTransitionSettings(enabled=True))
            validate_compiled_render_plan(plan)  # raises on any invariant violation
            output_directory = directory / "out"
            prepared = AutoMixAudioPipeline(executable).render(
                plan.audio, {track.id: track.file_path for track in tracks}, output_directory,
            )
            self.assertTrue(prepared.path.is_file())
            self.assertAlmostEqual(prepared.duration_seconds, plan.duration_seconds, delta=0.5)


if __name__ == "__main__":
    unittest.main()
