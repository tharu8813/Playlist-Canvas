from __future__ import annotations

import os
import struct
import sys
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
from PySide6.QtGui import QColor, QImage

from app.automix.renderer import AutoMixRenderError
from app.models.playlist import PlaylistTrack
from app.renderer.ffmpeg_renderer import FFmpegRenderer, RenderSettings


def _write_tone_wav(path: Path, frequency: float, duration: float, sample_rate: int = 48000) -> None:
    t = np.linspace(0.0, duration, int(duration * sample_rate), endpoint=False)
    signal = 0.5 * np.sin(2 * np.pi * frequency * t)
    pcm16 = (signal * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(struct.pack(f"<{len(pcm16)}h", *pcm16.tolist()))


def _renderer() -> FFmpegRenderer:
    renderer = FFmpegRenderer.__new__(FFmpegRenderer)
    renderer.executable = Path("ffmpeg")
    return renderer


class RenderAutomixAudioSegmentsUnitTests(unittest.TestCase):
    """No real FFmpeg needed: exercises the fallback/degradation logic only."""

    def test_missing_dependency_returns_none(self) -> None:
        renderer = _renderer()
        with patch.dict(sys.modules, {"app.automix.analysis.basic": None}):
            result = renderer._render_automix_audio_segments(
                [PlaylistTrack("a.mp3", "A", duration_seconds=30.0)],
                Path("."), 30.0, None, __import__("threading").Event(),
            )
        self.assertIsNone(result)

    def test_render_failure_falls_back_to_none_not_an_exception(self) -> None:
        renderer = _renderer()
        with patch(
            "app.automix.renderer.AutoMixAudioPipeline.render",
            side_effect=AutoMixRenderError("boom"),
        ):
            result = renderer._render_automix_audio_segments(
                [PlaylistTrack("a.mp3", "A", duration_seconds=30.0)],
                Path("."), 30.0, None, __import__("threading").Event(),
            )
        self.assertIsNone(result)

    def test_cancellation_propagates_instead_of_falling_back(self) -> None:
        import threading
        from app.renderer.ffmpeg_renderer import RenderCancelledError

        renderer = _renderer()
        cancel_event = threading.Event()
        cancel_event.set()
        with patch(
            "app.automix.renderer.AutoMixAudioPipeline.render",
            side_effect=AutoMixRenderError("cancelled"),
        ):
            with self.assertRaises(RenderCancelledError):
                renderer._render_automix_audio_segments(
                    [PlaylistTrack("a.mp3", "A", duration_seconds=30.0)],
                    Path("."), 30.0, None, cancel_event,
                )


@unittest.skipUnless(
    os.environ.get("PLAYLIST_CANVAS_TEST_FFMPEG", "").strip(),
    "Set PLAYLIST_CANVAS_TEST_FFMPEG to run real FFmpeg AutoMix export checks.",
)
class RealAutomixExportIntegrationTests(unittest.TestCase):
    def test_automix_export_matches_the_legacy_sequential_duration(self) -> None:
        executable = Path(os.environ["PLAYLIST_CANVAS_TEST_FFMPEG"].strip())
        with TemporaryDirectory(prefix="automix-export-") as raw_directory:
            directory = Path(raw_directory)
            a_path = directory / "a.wav"
            b_path = directory / "b.wav"
            _write_tone_wav(a_path, 440.0, 20.0)
            _write_tone_wav(b_path, 440.0, 20.0)
            tracks = [
                PlaylistTrack(str(a_path), "A", duration_seconds=20.0),
                PlaylistTrack(str(b_path), "B", duration_seconds=20.0),
            ]
            renderer = FFmpegRenderer(executable)
            sequential_duration = renderer._timeline_duration(tracks)
            self.assertEqual(sequential_duration, 40.0)

            segments = renderer._render_automix_audio_segments(
                tracks, directory, sequential_duration, None, __import__("threading").Event(),
            )
            self.assertIsNotNone(segments)
            segment_paths, segment_durations = segments
            self.assertAlmostEqual(sum(segment_durations), sequential_duration, delta=0.05)
            for path in segment_paths:
                self.assertTrue(path.is_file())

    def test_full_render_with_automix_enabled_matches_legacy_duration(self) -> None:
        executable = Path(os.environ["PLAYLIST_CANVAS_TEST_FFMPEG"].strip())
        with TemporaryDirectory(prefix="automix-export-full-") as raw_directory:
            directory = Path(raw_directory)
            a_path = directory / "a.wav"
            b_path = directory / "b.wav"
            _write_tone_wav(a_path, 440.0, 10.0)
            _write_tone_wav(b_path, 440.0, 10.0)
            tracks = [
                PlaylistTrack(str(a_path), "A", duration_seconds=10.0),
                PlaylistTrack(str(b_path), "B", duration_seconds=10.0),
            ]
            image = QImage(32, 32, QImage.Format.Format_RGB32)
            image.fill(QColor(10, 20, 30))
            settings = RenderSettings(
                fps=5, video_codec="libx264", crf=30, preset="ultrafast",
                output_width=32, output_height=32,
            )
            renderer = FFmpegRenderer(executable)
            output_path = directory / "out.mp4"
            result = renderer.render(
                [image, image], tracks, output_path, settings, use_automix=True,
            )
            self.assertTrue(output_path.is_file())
            self.assertAlmostEqual(result.validation.duration_seconds, 20.0, delta=0.5)


if __name__ == "__main__":
    unittest.main()
