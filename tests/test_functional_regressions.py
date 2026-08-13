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
from app.renderer.ffmpeg_renderer import (
    FFmpegRenderer, RenderError, RenderFrame, RenderSettings, StaticOverlayLayer,
)
from app.renderer.python_visualizer import PythonVisualizerRenderer
from app.services.playlist_export_service import PlaylistExportError, PlaylistExportService
from app.services.playlist_service import PlaylistService
from app.services.lyrics_service import LyricsService
from app.services.project_service import ProjectService
from app.ffmpeg.install_worker import FFmpegInstallWorker
from app.ffmpeg.managed_installer import FFmpegInstallError, ManagedFFmpegInstaller
from app.dialogs.about_dialog import AboutDialog
from app.dialogs.export_preview_dialog import ExportPreviewDialog, TIMELINE_SCALE
from app.dialogs.lrc_generator_dialog import LrcGeneratorDialog
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

    def test_export_rejects_misaligned_visual_streams(self) -> None:
        base = [(Path("one.png"), 1.0), (Path("two.png"), 1.0)]
        aligned_layer = StaticOverlayLayer(
            1.0, [RenderFrame(Path("layer.png"), 2.0)],
        )
        FFmpegRenderer._validate_visual_timeline(base, [aligned_layer], 2.0, 30)

        with self.assertRaises(RenderError):
            FFmpegRenderer._validate_visual_timeline(base, [], 3.0, 30)
        with self.assertRaises(RenderError):
            FFmpegRenderer._validate_visual_timeline(
                base, [StaticOverlayLayer(1.0, [RenderFrame(Path("layer.png"), 1.5)])],
                2.0, 30,
            )

    def test_audio_normalization_uses_exact_lossless_segments(self) -> None:
        renderer = object.__new__(FFmpegRenderer)
        commands: list[list[str]] = []
        renderer._run = lambda arguments, **_kwargs: commands.append(arguments)  # type: ignore[method-assign]
        track = PlaylistTrack(
            "song.mp3", "Song", duration_seconds=2.75, start_time_seconds=0.4,
        )
        with TemporaryDirectory() as directory:
            segments = renderer._normalize_audio(
                [track], Path(directory), RenderSettings(), None, threading.Event(),
            )
            durations = renderer._insert_silence_for_gaps(
                [track], segments, Path(directory), RenderSettings(), None,
                threading.Event(),
            )
            manifest = Path(directory) / "audio.ffconcat"
            renderer._write_concat_file(manifest, segments, durations)
            manifest_text = manifest.read_text(encoding="utf-8")
        self.assertEqual(segments[0].suffix, ".nut")
        self.assertEqual(segments[1].suffix, ".nut")
        self.assertEqual(durations, [0.4, 2.75])
        self.assertIn("apad=whole_dur=2.750000", commands[0])
        self.assertIn("2.750000", commands[0])
        self.assertIn("pcm_s16le", commands[0])
        self.assertIn("duration 0.400000", manifest_text)
        self.assertIn("duration 2.750000", manifest_text)

    def test_static_source_with_same_z_as_visualizer_is_not_dropped(self) -> None:
        scene = CanvasScene()
        dynamic = Source(SourceType.AUDIO_VISUALIZER, "Dynamic", z_index=4.0)
        static = Source(SourceType.TEXT, "Static", z_index=4.0)
        scene.addItem(SourceItem(dynamic))
        scene.addItem(SourceItem(static))
        bands = CanvasSnapshot.z_bands(scene, {dynamic.id})
        self.assertEqual(len(bands), 2)
        lower, upper = bands[1]
        self.assertLessEqual(lower or 0.0, static.z_index)
        self.assertTrue(upper is None or static.z_index <= upper)

    def test_reactive_overlay_uses_track_animation_windows(self) -> None:
        overlay = SimpleNamespace(
            animation_in="fade", animation_out="zoom",
            animation_in_duration=0.5, animation_out_duration=1.0,
        )
        windows = [(1.0, 3.0), (5.0, 2.0)]
        self.assertEqual(
            PythonVisualizerRenderer._animation_state(0.5, windows, overlay),
            ("fade", 0.0, True),
        )
        self.assertEqual(
            PythonVisualizerRenderer._animation_state(1.25, windows, overlay),
            ("fade", 0.5, True),
        )
        self.assertEqual(
            PythonVisualizerRenderer._animation_state(3.5, windows, overlay),
            ("zoom", 0.5, False),
        )
        self.assertEqual(
            PythonVisualizerRenderer._animation_state(4.5, windows, overlay),
            ("zoom", 1.0, False),
        )

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

    def test_entrance_and_exit_animation_durations_are_independent(self) -> None:
        legacy = Source.from_dict({
            "source_type": "shape", "name": "Legacy",
            "animation_duration": 0.7,
        })
        self.assertEqual(legacy.animation_in_duration, 0.7)
        self.assertEqual(legacy.animation_out_duration, 0.7)

        scene = CanvasScene()
        source = Source(
            SourceType.SHAPE, "Split timing", animation_in="fade",
            animation_out="fade", animation_in_duration=0.2,
            animation_out_duration=0.8,
        )
        scene.addItem(SourceItem(source))
        track = PlaylistTrack("split.wav", "Split", duration_seconds=4.0)
        preview = SimpleNamespace(scene=scene)

        self.assertEqual(
            ExportPreviewDialog._animation_state(preview, track, 0.1),
            ("in", 0.5, 0.2),
        )
        phase, progress, duration = ExportPreviewDialog._animation_state(
            preview, track, 3.6,
        )
        self.assertEqual(phase, "out")
        self.assertAlmostEqual(progress, 0.5)
        self.assertAlmostEqual(duration, 0.8)

        restored = Source.from_dict(source.to_dict())
        self.assertEqual(restored.animation_in_duration, 0.2)
        self.assertEqual(restored.animation_out_duration, 0.8)

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
            {"start": 5.0, "end": 6.0, "text": "First line\ncontinued line"},
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
        displayed: list[tuple[str, float, int, int]] = []
        original_capture = CanvasSnapshot.capture

        def observe_capture(*arguments: object, **keywords: object):
            displayed.append((
                source.text, item.opacity(), source.subtitle_current_line,
                source.subtitle_current_line_count,
            ))
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
        self.assertIn("First line\ncontinued line", displayed[0][0])
        self.assertNotIn("Configured placeholder", displayed[0][0])
        self.assertAlmostEqual(displayed[0][1], source.opacity)
        self.assertEqual(displayed[0][2], -1)
        self.assertEqual(displayed[1][2], 0)
        self.assertEqual(displayed[1][3], 2)
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

    def test_audio_import_detection_preserves_missing_tag_fields(self) -> None:
        service = PlaylistService()
        with TemporaryDirectory(prefix="audio-metadata-detection-") as raw_directory:
            path = Path(raw_directory) / "untagged.mp3"
            path.touch()
            audio = SimpleNamespace(
                tags={"title": ["Tagged title"]},
                info=SimpleNamespace(length=42.5),
            )
            with patch(
                "app.services.playlist_service.MutagenFile", return_value=audio,
            ):
                candidates = service.inspect_files([path])

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.track.title, "Tagged title")
        self.assertEqual(candidate.track.artist, "Unknown Artist")
        self.assertEqual(candidate.track.album, "Unknown Album")
        self.assertEqual(candidate.missing_fields, ("artist", "album"))
        self.assertAlmostEqual(candidate.track.duration_seconds, 42.5)

    def test_lrc_generator_service_writes_metadata_and_round_trips_cues(self) -> None:
        cues = [
            {"start": 12.34, "end": 20.0, "text": "Second"},
            {"start": 1.25, "end": 12.34, "text": "First\ncontinued"},
        ]
        rendered = LyricsService.format_lrc(
            cues, title="Example", artist="Artist"
        )
        self.assertIn("[ti:Example]", rendered)
        self.assertIn("[ar:Artist]", rendered)
        self.assertIn(r"[00:01.25]First\ncontinued", rendered)
        self.assertNotIn("[00:01.25]First\ncontinued", rendered)
        self.assertLess(rendered.index("[00:01.25]First"), rendered.index("[00:12.34]Second"))
        with TemporaryDirectory(prefix="pvs-lrc-writer-") as raw_directory:
            saved = LyricsService.save_lrc(
                Path(raw_directory) / "generated", cues,
                title="Example", artist="Artist",
            )
            restored = LyricsService.load(saved)
        self.assertEqual(saved.suffix, ".lrc")
        self.assertEqual([cue["text"] for cue in restored], ["First\ncontinued", "Second"])
        self.assertAlmostEqual(float(restored[0]["start"]), 1.25)

    def test_lrc_generator_supports_blank_line_separated_multiline_units(self) -> None:
        dialog = LrcGeneratorDialog([], Translator())
        try:
            multiline_index = dialog.input_mode_combo.findData("multiline")
            dialog.input_mode_combo.setCurrentIndex(multiline_index)
            dialog.lyrics_editor.setPlainText(
                "First visual line\nSecond visual line\n\nNext timed lyric"
            )
            dialog._prepare_lines()
            self.assertEqual(
                dialog.lines,
                ["First visual line\nSecond visual line", "Next timed lyric"],
            )
            self.assertEqual(dialog.timeline_table.rowCount(), 2)
            self.assertIn("\n", dialog.timeline_table.item(0, 2).text())
            dialog.timestamps = [1.0, 4.0]
            rendered = LyricsService.format_lrc(dialog.timed_cues())
            self.assertIn(r"[00:01.00]First visual line\nSecond visual line", rendered)
            self.assertEqual(
                LyricsService._parse_lrc(rendered)[0]["text"],
                "First visual line\nSecond visual line",
            )
        finally:
            dialog.close()

    def test_lrc_generator_records_undoes_and_previews_partial_timing(self) -> None:
        dialog = LrcGeneratorDialog([], Translator())
        dialog.lyrics_editor.setPlainText("First line\nSecond line\nThird line")
        dialog._prepare_lines()
        dialog.audio_path = str(Path("test-audio.wav").resolve())
        dialog.calibration_spin.setValue(-40)
        with patch.object(dialog.media_player, "position", return_value=12_340):
            dialog._record_timestamp()
        self.assertAlmostEqual(float(dialog.timestamps[0] or 0.0), 12.30)
        self.assertEqual(dialog.current_index, 1)
        self.assertIn("First line", dialog.timeline_table.item(0, 2).text())
        dialog._undo_record()
        self.assertIsNone(dialog.timestamps[0])
        self.assertEqual(dialog.current_index, 0)
        dialog._redo_record()
        self.assertAlmostEqual(float(dialog.timestamps[0] or 0.0), 12.30)
        cues = dialog.timed_cues()
        self.assertEqual(len(cues), 1)
        dialog.pages.setCurrentIndex(2)
        dialog.preview_mode_check.setChecked(True)
        dialog._position_changed(12_500)
        self.assertEqual(dialog._playback_highlight_row, 0)
        self.assertTrue(dialog.timeline_table.item(0, 0).text().startswith("♪"))
        self.assertFalse(dialog.record_button.isEnabled())
        self.assertFalse(dialog.timeline_table.item(0, 0).text().startswith("▶"))
        dialog._open_shortcuts()
        self.assertIsNotNone(dialog._shortcuts_dialog)
        assert dialog._shortcuts_dialog is not None
        self.assertGreaterEqual(dialog._shortcuts_dialog.table.rowCount(), 6)
        self.assertIn("F1", dialog._shortcuts_dialog.table.item(4, 0).text())
        dialog.close()

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
            self.assertIn("https://github.com/tharu8813/Playlist-Canvas", diagnostics)
            self.assertTrue(dialog.repository_link.openExternalLinks())
            self.assertIn("github.com/tharu8813/Playlist-Canvas", dialog.repository_link.text())
            dialog._copy_diagnostics()
            self.assertEqual(QApplication.clipboard().text(), diagnostics)
            dialog.close()


if __name__ == "__main__":
    unittest.main()
