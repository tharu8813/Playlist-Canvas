from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage

from app.canvas.live_canvas import CanvasScene
from app.canvas.source_item import SourceItem
from app.models.playlist import PlaylistTrack
from app.models.source import Source, SourceType
from app.preview.canvas_snapshot import CanvasSnapshot
from app.renderer.ffmpeg_renderer import FFmpegRenderer
from app.renderer.python_visualizer import PythonVisualizerRenderer
from app.services.playlist_export_service import PlaylistExportError, PlaylistExportService
from app.services.playlist_service import PlaylistService
from app.services.lyrics_service import LyricsService
from app.services.project_service import ProjectService
from app.ffmpeg.install_worker import FFmpegInstallWorker
from app.ffmpeg.managed_installer import FFmpegInstallError, ManagedFFmpegInstaller
from app.dialogs.about_dialog import AboutDialog
from app.dialogs.export_preview_dialog import ExportPreviewDialog, TIMELINE_SCALE
from app.utils.i18n import Translator
from app import __version__


class FunctionalRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_source_timing_controls_snapshot_visibility(self) -> None:
        scene = CanvasScene()
        source = Source(
            SourceType.SHAPE, "Timed", x=20, y=20, width=120, height=80,
            fill_color="#FF0000", timeline_start=5.0, timeline_duration=2.0,
        )
        item = SourceItem(source)
        scene.addItem(item)
        track = PlaylistTrack("track.wav", "Track", duration_seconds=10.0)

        before = CanvasSnapshot.capture_track(
            scene, track, 1, 1, 0.0, timeline_seconds=4.0,
        )
        during = CanvasSnapshot.capture_track(
            scene, track, 1, 1, 0.0, timeline_seconds=6.0,
        )
        after = CanvasSnapshot.capture_track(
            scene, track, 1, 1, 0.0, timeline_seconds=7.0,
        )

        self.assertNotEqual(before.pixelColor(60, 50), during.pixelColor(60, 50))
        self.assertEqual(before.pixelColor(60, 50), after.pixelColor(60, 50))
        self.assertTrue(item.isVisible())

    def test_playlist_preview_keeps_audio_stopped_during_leading_gap(self) -> None:
        track = PlaylistTrack(
            "delayed.wav", "Delayed", duration_seconds=20.0,
            start_time_seconds=10.0,
        )
        selection = ExportPreviewDialog._track_at(
            SimpleNamespace(tracks=[track]), 5.0
        )
        self.assertIsNotNone(selection)
        self.assertFalse(ExportPreviewDialog._selection_has_audio(selection, 5.0))
        self.assertTrue(ExportPreviewDialog._selection_has_audio(selection, 10.0))

        class FakePlayer:
            def __init__(self) -> None:
                self.stopped = False

            def stop(self) -> None:
                self.stopped = True

        player = FakePlayer()
        preview = SimpleNamespace(
            tracks=[track],
            timeline=SimpleNamespace(value=lambda: round(5.0 * TIMELINE_SCALE)),
            media_player=player,
            _active_track_index=0,
            _last_media_position_ms=5000,
        )
        preview._track_at = lambda seconds: ExportPreviewDialog._track_at(
            preview, seconds
        )
        preview._selection_has_audio = ExportPreviewDialog._selection_has_audio
        ExportPreviewDialog._start_audio_at_playhead(preview)
        self.assertTrue(player.stopped)
        self.assertEqual(preview._active_track_index, -1)
        self.assertEqual(preview._last_media_position_ms, 0)

    def test_overlapping_requested_starts_are_sequenced(self) -> None:
        tracks = [
            PlaylistTrack("one.wav", "One", duration_seconds=10.0),
            PlaylistTrack(
                "two.wav", "Two", duration_seconds=10.0, start_time_seconds=5.0,
            ),
        ]
        self.assertEqual(FFmpegRenderer._timeline_duration(tracks), 20.0)

    def test_preview_and_export_share_bounded_per_source_animation_timing(self) -> None:
        scene = CanvasScene()
        short = Source(
            SourceType.SHAPE, "Short", animation_in="fade",
            animation_out="fade", animation_duration=0.2, opacity=0.8,
        )
        long = Source(
            SourceType.SHAPE, "Long", animation_in="fade",
            animation_out="fade", animation_duration=1.0, opacity=0.8,
            z_index=1,
        )
        scene.addItem(SourceItem(short))
        scene.addItem(SourceItem(long))
        track = PlaylistTrack("short.wav", "Short track", duration_seconds=1.0)

        preview = SimpleNamespace(scene=scene)
        self.assertEqual(
            ExportPreviewDialog._animation_state(preview, track, 0.6),
            (None, 1.0, 0.0),
        )
        phase, progress, duration = ExportPreviewDialog._animation_state(
            preview, track, 0.8
        )
        self.assertEqual(phase, "out")
        self.assertAlmostEqual(progress, 0.2)
        self.assertAlmostEqual(duration, 0.25)

        observed: dict[str, float] = {}

        def inspect_opacity(capture_scene: CanvasScene, *_args: object, **_kwargs: object) -> QImage:
            observed.update({
                item.source.name: item.opacity()
                for item in capture_scene.items() if isinstance(item, SourceItem)
            })
            return QImage(1, 1, QImage.Format.Format_ARGB32)

        with patch.object(CanvasSnapshot, "capture", side_effect=inspect_opacity):
            CanvasSnapshot.capture_track(
                scene, track, 1, 1, 0.0,
                animation_phase="in", animation_progress=0.5,
                elapsed_seconds=0.5, animation_phase_duration=1.0,
            )
        self.assertAlmostEqual(observed["Short"], short.opacity)
        self.assertLess(observed["Long"], long.opacity)

    def test_legacy_time_source_is_expanded_dynamically(self) -> None:
        scene = CanvasScene()
        source = Source(SourceType.TIME, "Time", text="12:34")
        scene.addItem(SourceItem(source))
        track = PlaylistTrack("track.wav", "Track", duration_seconds=90.0)
        with patch(
            "app.preview.canvas_snapshot.expand_track_template",
            return_value="01:05",
        ) as expand:
            CanvasSnapshot.capture_track(
                scene, track, 1, 1, 0.0,
                elapsed_seconds=65.0, timeline_seconds=65.0,
            )
        expand.assert_called_once()
        self.assertEqual(source.text, "12:34")

    def test_lyrics_lookup_uses_sorted_cues_and_track_plus_source_offset(self) -> None:
        cues = [
            {"start": float(index), "end": float(index + 1), "text": str(index)}
            for index in range(10_000)
        ]
        self.assertEqual(LyricsService.current_cue_index(cues, 8_765.25), 8_765)
        self.assertIsNone(LyricsService.current_cue_index(cues, 10_001.0))

        scene = CanvasScene()
        source = Source(
            SourceType.LYRICS, "Lyrics", width=500, height=160,
            subtitle_timing_offset=0.25,
        )
        scene.addItem(SourceItem(source))
        track = PlaylistTrack(
            "track.wav", "Track", duration_seconds=10.0,
            lyrics=[{"start": 1.0, "end": 3.0, "text": "Line"}],
            lyrics_timing_offset_seconds=0.5,
        )
        with patch.object(
            LyricsService, "current_cue_index", return_value=0,
        ) as lookup:
            CanvasSnapshot.capture_track(
                scene, track, 1, 1, 0.0, elapsed_seconds=1.0,
            )
        self.assertAlmostEqual(lookup.call_args.args[1], 1.75)

    def test_track_with_lyrics_displays_first_line_from_playback_start(self) -> None:
        cues = [
            {"start": 5.0, "end": 6.0, "text": "First line"},
            {"start": 10.0, "end": 11.0, "text": "Second line"},
        ]
        self.assertEqual(LyricsService.display_cue_index(cues, 0.0), 0)
        self.assertEqual(LyricsService.display_cue_index(cues, 8.0), 0)
        self.assertEqual(LyricsService.display_cue_index(cues, 12.0), 1)
        self.assertIsNone(LyricsService.display_cue_index([], 0.0))

        scene = CanvasScene()
        source = Source(
            SourceType.LYRICS, "Lyrics", text="Configured placeholder",
            subtitle_fallback="No lyrics", subtitle_animation="fade",
        )
        item = SourceItem(source)
        scene.addItem(item)
        track = PlaylistTrack(
            "track.wav", "Track", duration_seconds=20.0, lyrics=cues,
        )
        displayed: list[tuple[str, float, int]] = []
        original_capture = CanvasSnapshot.capture

        def observe_capture(*arguments: object, **keywords: object):
            displayed.append((source.text, item.opacity(), source.subtitle_current_line))
            return original_capture(*arguments, **keywords)

        with patch.object(CanvasSnapshot, "capture", side_effect=observe_capture):
            CanvasSnapshot.capture_track(
                scene, track, 1, 1, 0.0, elapsed_seconds=0.0,
            )
            CanvasSnapshot.capture_track(
                scene, track, 1, 1, 0.0, elapsed_seconds=5.5,
            )
            CanvasSnapshot.capture_track(
                scene, track, 1, 1, 0.0, elapsed_seconds=7.0,
            )

        self.assertTrue(displayed[0][0].startswith("First line"))
        self.assertNotIn("Configured placeholder", displayed[0][0])
        self.assertAlmostEqual(displayed[0][1], source.opacity)
        self.assertEqual(displayed[0][2], -1)
        self.assertEqual(displayed[1][2], 0)
        self.assertEqual(displayed[2][2], -1)
        self.assertEqual(source.text, "Configured placeholder")

    def test_modern_lyric_transitions_animate_and_restore_render_state(self) -> None:
        scene = CanvasScene()
        source = Source(
            SourceType.LYRICS, "Lyrics", width=560, height=220,
            subtitle_animation="apple_music", subtitle_animation_duration=0.4,
        )
        item = SourceItem(source)
        scene.addItem(item)
        track = PlaylistTrack(
            "track.wav", "Track", duration_seconds=12.0,
            lyrics=[{"start": 5.0, "end": 8.0, "text": "A softer lyric line"}],
        )
        observed: list[tuple[float, float, float, float]] = []
        original_capture = CanvasSnapshot.capture

        def observe_capture(*arguments: object, **keywords: object):
            observed.append((
                item.opacity(), item.scale(), source.subtitle_scroll_offset,
                item._subtitle_transition_progress,
            ))
            return original_capture(*arguments, **keywords)

        with patch.object(CanvasSnapshot, "capture", side_effect=observe_capture):
            CanvasSnapshot.capture_track(
                scene, track, 1, 1, 0.0, elapsed_seconds=5.1,
            )
            source.subtitle_animation = "spotify"
            CanvasSnapshot.capture_track(
                scene, track, 1, 1, 0.0, elapsed_seconds=5.1,
            )

        apple, spotify = observed
        self.assertLess(apple[0], source.opacity)
        self.assertLess(apple[1], source.scale)
        self.assertGreater(apple[2], 0.0)
        self.assertGreater(apple[3], 0.0)
        self.assertLess(apple[3], 1.0)
        self.assertGreater(apple[2], spotify[2])
        self.assertAlmostEqual(item.opacity(), source.opacity)
        self.assertAlmostEqual(item.scale(), source.scale)
        self.assertAlmostEqual(source.subtitle_scroll_offset, 0.0)
        self.assertAlmostEqual(item._subtitle_transition_progress, 1.0)

    def test_lyric_font_and_blur_cache_survives_unrelated_source_edits(self) -> None:
        source = Source(SourceType.LYRICS, "Lyrics")
        item = SourceItem(source)
        item._lyric_ghost_cache[("sentinel",)] = object()  # type: ignore[assignment]
        source.x += 20
        item.apply_source()
        self.assertIn(("sentinel",), item._lyric_ghost_cache)
        source.font_size += 1
        item.apply_source()
        self.assertNotIn(("sentinel",), item._lyric_ghost_cache)

    def test_webvtt_lyrics_are_supported_and_sorted(self) -> None:
        with TemporaryDirectory(prefix="pvs-vtt-test-") as raw_directory:
            path = Path(raw_directory) / "lyrics.vtt"
            path.write_text(
                "WEBVTT\n\n00:00:02.000 --> 00:00:03.000\nSecond\n\n"
                "00:00:00.500 --> 00:00:01.500\nFirst\n",
                encoding="utf-8",
            )
            cues = LyricsService.load(path)
        self.assertEqual([cue["text"] for cue in cues], ["First", "Second"])

    def test_ffprobe_is_used_as_duration_fallback(self) -> None:
        with TemporaryDirectory(prefix="pvs-probe-test-") as raw_directory:
            directory = Path(raw_directory)
            ffmpeg = directory / "ffmpeg.exe"
            ffprobe = directory / "ffprobe.exe"
            ffmpeg.touch()
            ffprobe.touch()
            with (
                patch(
                    "app.services.playlist_service.FFmpegRenderer.find_executable",
                    return_value=ffmpeg,
                ),
                patch(
                    "app.services.playlist_service.subprocess.run",
                    return_value=SimpleNamespace(returncode=0, stdout="123.45\n"),
                ),
            ):
                self.assertEqual(
                    PlaylistService._probe_duration(directory / "track.wav"), 123.45
                )

    def test_playlist_files_require_explicit_overwrite(self) -> None:
        service = PlaylistExportService()
        tracks = [PlaylistTrack("one.wav", "One", duration_seconds=10.0)]
        with TemporaryDirectory(prefix="pvs-playlist-export-") as raw_directory:
            directory = Path(raw_directory)
            service.export(tracks, directory)
            with self.assertRaises(PlaylistExportError):
                service.export(tracks, directory)
            service.export(tracks, directory, overwrite=True)

    def test_stale_project_cache_is_removed_but_current_cache_is_kept(self) -> None:
        with TemporaryDirectory(prefix="pvs-cache-test-") as raw_directory:
            roots = [
                Path(raw_directory) / name / "project-cache"
                for name in ("PlaylistCanvas", "PlaylistVideoStudio")
            ]
            old = time.time() - 60 * 86_400
            for root in roots:
                stale = root / "stale"
                current = root / "current"
                stale.mkdir(parents=True)
                current.mkdir()
                os.utime(stale, (old, old))
                os.utime(current, (old, old))
            with patch("app.services.project_service.gettempdir", return_value=raw_directory):
                ProjectService.cleanup_cache(max_age_days=30, keep={"current"})
            for root in roots:
                self.assertFalse((root / "stale").exists())
                self.assertTrue((root / "current").exists())

    def test_ffmpeg_worker_reports_unexpected_errors(self) -> None:
        class BrokenInstaller:
            def install_latest(self, *_args: object) -> object:
                raise PermissionError("install folder denied")

        worker = FFmpegInstallWorker(BrokenInstaller())  # type: ignore[arg-type]
        messages: list[str] = []
        worker.failed.connect(messages.append)
        worker.run()
        self.assertEqual(len(messages), 1)
        self.assertIn("install folder denied", messages[0])

    def test_ffmpeg_release_api_failure_uses_official_latest_links(self) -> None:
        installer = ManagedFFmpegInstaller(Path("unused-test-install-root"))
        updates: list[tuple[str, float, str]] = []
        with patch.object(
            installer, "_read_json",
            side_effect=FFmpegInstallError("GitHub API rate limited"),
        ):
            tag, archive_url, checksum_url = installer._release_assets(
                threading.Event(), lambda *update: updates.append(update),
            )
        self.assertEqual(tag, "latest")
        self.assertTrue(archive_url.endswith(installer.archive_name))
        self.assertTrue(checksum_url.endswith(installer.checksum_name))
        self.assertIn("/releases/download/latest/", archive_url)
        self.assertTrue(any("official latest" in message for _, _, message in updates))

    def test_visualizer_progress_reports_frames_percent_and_eta(self) -> None:
        message = PythonVisualizerRenderer._frame_progress_message(
            layer_number=1,
            layer_count=2,
            frame_number=120,
            layer_frames=300,
            completed_frames=120,
            total_frames=600,
            elapsed_seconds=10.0,
        )
        self.assertIn("Visualizer 1/2", message)
        self.assertIn("frame 120/300", message)
        self.assertIn("20.0%", message)
        self.assertIn("00:40 remaining", message)

    def test_about_dialog_provides_copyable_diagnostics(self) -> None:
        with TemporaryDirectory(prefix="pvs-about-test-") as raw_directory:
            ffmpeg = Path(raw_directory) / "ffmpeg.exe"
            dialog = AboutDialog(
                Translator(), ffmpeg, Path(raw_directory) / "logs"
            )
            diagnostics = dialog.diagnostic_text()
            self.assertIn(f"App version: {__version__}", diagnostics)
            self.assertIn(str(ffmpeg), diagnostics)
            dialog._copy_diagnostics()
            self.assertEqual(QApplication.clipboard().text(), diagnostics)
            dialog.close()


if __name__ == "__main__":
    unittest.main()
