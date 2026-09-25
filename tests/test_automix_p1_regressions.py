"""Regressions for shared mix timing, source anchors and worker lifetime."""
from __future__ import annotations

import threading
import time
import unittest
import os
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QColor

from app.automix.candidates import generate_candidates, select_best_candidate
from app.automix.compatibility import evaluate_compatibility
from app.automix.planner import compile_automix
from app.automix.renderer import AutoMixRenderError, PreparedAudio, build_filter_graph
from app.controllers.automix_analysis_controller import AutoMixAnalysisController
from app.controllers.preview_audio_controller import PreviewAudioController
from app.dialogs.export_preview_dialog import ExportPreviewDialog
from app.models.source import Source, SourceType
from app.renderer.export_timeline import ExportTimelinePlanner
from app.renderer.ffmpeg_renderer import FFmpegRenderer, ExportMetadata, RenderSettings, RenderFrame
from app.services.playlist_export_service import PlaylistExportService, TimestampFormat
from app.timeline.render_plan import AudioRenderClip, AudioRenderPlan, CompiledRenderPlan, build_presentation_and_metadata
from tests.test_automix_planner import _track, _analysis, ENABLED


class MixTimingTests(unittest.TestCase):
    def test_selected_outgoing_beat_is_used_even_when_track_end_is_off_grid(self):
        """C.1 scores/renders the same duration, including the off-grid tail."""
        tracks = [_track('a', 60.3), _track('b', 60)]
        analyses = {t.id: _analysis(t.id, 120, t.duration_seconds, meter_confidence=.3) for t in tracks}
        a, b = analyses.values()
        candidate = select_best_candidate(generate_candidates(a, b, evaluate_compatibility(a, b, ENABLED), ENABLED))
        plan = compile_automix(tracks, analyses, ENABLED)
        # Bar phase unknown (meter .3): the 8-bar preference is halved to 4 bars (8 s).
        self.assertAlmostEqual(candidate.outgoing_source_time, 52.0)
        self.assertAlmostEqual(plan.audio.clips[1].timeline_start, candidate.outgoing_source_time)
        self.assertAlmostEqual(plan.audio.transitions[0].duration, candidate.duration_seconds)
        self.assertAlmostEqual(plan.audio.transitions[0].duration, 8.3)

    def test_frames_preview_and_chapters_use_the_same_source_time_and_duration(self):
        tracks = [_track('a', 60), _track('b', 60)]
        tracks[1].lyrics = [{'start': 12.0, 'end': 14.0, 'text': 'cue'}]
        clips = (AudioRenderClip('a', 'a', 0, 0, 60), AudioRenderClip('b', 'b', 44, 2, 60, 1.25))
        presentation, metadata, duration = build_presentation_and_metadata(clips)
        plan = CompiledRenderPlan(AudioRenderPlan(clips), presentation, metadata, duration)
        lyrics = Source(source_type=SourceType.LYRICS, name='Lyrics')
        samples = ExportTimelinePlanner.build(tracks, [lyrics], 30, plan)
        self.assertAlmostEqual(sum(s.duration_seconds for s in samples), duration)
        elapsed = 0.0
        starts = {}
        for sample in samples:
            starts.setdefault(sample.track.id, elapsed)
            self.assertAlmostEqual(sample.elapsed_seconds, presentation.local_time(sample.timeline_seconds))
            elapsed += sample.duration_seconds
        self.assertAlmostEqual(starts['b'], 44)
        self.assertTrue(any(s.track.id == 'b' and abs(s.timeline_seconds - 52) < .001 for s in samples))
        preview = SimpleNamespace(tracks=tracks, _compiled_plan=plan)
        self.assertAlmostEqual(ExportPreviewDialog._track_at(preview, 52)[2], 12)
        self.assertEqual(metadata.chapters[1].start, starts['b'])
        self.assertIn('00:44', PlaylistExportService().description_text(tracks, TimestampFormat.STANDARD, plan))
        renderer = FFmpegRenderer.__new__(FFmpegRenderer)
        with TemporaryDirectory() as directory:
            metadata_path = renderer._write_export_ffmetadata(Path(directory), tracks, ExportMetadata(), Path('out.mp4'), plan)
            self.assertIn('START=44000', metadata_path.read_text(encoding='utf-8'))

    def test_preview_does_not_mute_at_the_old_sequential_gap(self):
        tracks = [_track('a', 60), _track('b', 60), _track('c', 60, start=125)]
        analyses = {t.id: _analysis(t.id, 120, 60, meter_confidence=.3) for t in tracks}
        plan = compile_automix(tracks, analyses, ENABLED)
        preview = SimpleNamespace(tracks=tracks, _compiled_plan=plan)
        selected = ExportPreviewDialog._track_at(preview, 122)
        self.assertEqual(selected[1].id, 'c')
        self.assertTrue(ExportPreviewDialog._has_audio(preview, selected, 122))
        b_end, c_start = plan.audio.clips[1].timeline_end, plan.audio.clips[2].timeline_start
        self.assertGreater(c_start, b_end)  # the explicit gap survives the overlap
        self.assertFalse(ExportPreviewDialog._has_audio(preview, selected, (b_end + c_start) / 2))
        samples = ExportTimelinePlanner.build(tracks, [], 30, plan)
        self.assertAlmostEqual(sum(s.duration_seconds for s in samples), plan.duration_seconds)

    def test_leading_silence_is_present_in_the_audio_graph(self):
        plan = compile_automix([_track('a', 10, start=5)], {}, ENABLED)
        graph, output = build_filter_graph(plan.audio.clips, ())
        self.assertIn('anullsrc=r=48000:cl=stereo:d=5.000000', graph)
        self.assertEqual(output, 'leading')

    def test_prepared_mix_reports_shortened_plan_and_fallback_reports_sequential(self):
        tracks = [_track('a', 10), _track('b', 10)]
        renderer = FFmpegRenderer.__new__(FFmpegRenderer)
        renderer.executable = Path('ffmpeg')
        with TemporaryDirectory() as directory, patch.object(renderer, '_run'), patch.object(renderer, '_measure_loudness_filter', return_value=None):
            directory = Path(directory)
            plans = []
            with patch('app.automix.renderer.AutoMixAudioPipeline.render', return_value=PreparedAudio(directory/'mix.m4a', 17)):
                renderer.prepare_playlist_audio(tracks, directory, RenderSettings(), 'crossfade', plan_callback=plans.append)
            self.assertEqual(plans[-1].duration_seconds, 17)
            self.assertEqual(plans[-1].metadata.chapters[1].start, 7)
            with patch('app.automix.renderer.AutoMixAudioPipeline.render', side_effect=AutoMixRenderError('bad mix')), patch.object(renderer, '_normalize_audio', return_value=[directory/'a', directory/'b']), patch.object(renderer, '_insert_silence_for_gaps', return_value=[10, 10]):
                renderer.prepare_playlist_audio(tracks, directory, RenderSettings(), 'crossfade', plan_callback=plans.append)
            self.assertEqual(plans[-1].duration_seconds, 20)
            self.assertEqual(plans[-1].metadata.chapters[1].start, 10)

    def test_crossfade_export_never_double_encodes_to_aac(self):
        """The intermediate AutoMix/crossfade mix must be lossless (PCM),
        not AAC: prepare_playlist_audio()'s own combine step already
        re-encodes to AAC exactly once, after loudness normalization. AAC
        at the intermediate step too would mean every AutoMix/crossfade
        export was lossy-to-lossy double-encoded regardless of the final
        bitrate the user picked."""
        from app.automix.renderer import AutoMixAudioPipeline

        renderer = FFmpegRenderer.__new__(FFmpegRenderer)
        renderer.executable = Path('ffmpeg')
        outer_commands: list[list[str]] = []
        renderer._run = lambda arguments, **_kwargs: outer_commands.append(arguments)
        inner_commands: list[list[str]] = []

        def fake_pipeline_run(_self, arguments, _cancel_event, _on_progress_seconds):
            inner_commands.append(arguments)

        with TemporaryDirectory() as directory:
            directory = Path(directory)
            a, b = directory / 'a.wav', directory / 'b.wav'
            a.write_bytes(b'\x00')
            b.write_bytes(b'\x00')
            tracks = [_track('a', 10), _track('b', 10)]
            tracks[0].file_path, tracks[1].file_path = str(a), str(b)
            with (
                patch.object(AutoMixAudioPipeline, '_run', fake_pipeline_run),
                patch.object(AutoMixAudioPipeline, '_probe_duration', return_value=17.0),
            ):
                renderer.prepare_playlist_audio(tracks, directory, RenderSettings(), 'crossfade')

        self.assertEqual(len(inner_commands), 1)
        self.assertNotIn('aac', inner_commands[0])
        self.assertIn('pcm_s16le', inner_commands[0])
        aac_encodes = [command for command in outer_commands if 'aac' in command]
        self.assertEqual(len(aac_encodes), 1)


class WorkerLifetimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_cancel_retains_worker_and_shutdown_waits_without_stopping_qt_events(self):
        class SlowWorker(QThread):
            def __init__(self, parent):
                super().__init__(parent)
                self.release = threading.Event()
                self._cancel_event = threading.Event()
            def run(self):
                self.release.wait(5)
            def cancel(self):
                self._cancel_event.set()

        renderer = FFmpegRenderer.__new__(FFmpegRenderer)
        for controller in (AutoMixAnalysisController(), PreviewAudioController(renderer)):
            worker = SlowWorker(controller)
            controller._worker = worker
            worker.finished.connect(lambda w=worker, c=controller: c._forget(w))
            worker.start()
            start = time.monotonic()
            controller.cancel()
            self.assertLess(time.monotonic() - start, .2)
            self.assertIs(controller._worker, worker)
            self.assertTrue(worker.isRunning())
            heartbeat = []
            QTimer.singleShot(30, lambda: heartbeat.append(True))
            QTimer.singleShot(60, worker.release.set)
            controller.shutdown()
            self.assertTrue(heartbeat)
            self.assertIsNone(controller._worker)

    def test_sequential_shutdown_of_multiple_controllers_does_not_crash(self):
        """Reproduces MainWindow's real close sequence: several of these
        controllers each call shutdown() one after another. A plain
        deleteLater() only *schedules* deletion for whenever some later,
        unrelated event loop happens to process it -- which was often a
        *different* controller's own shutdown() nested loop, and processing
        one controller's leftover QThread deletion interleaved with
        another's still-live thread completion inside the same loop pass
        reproduced a Windows 0xC0000409 crash deterministically before
        shutdown() was changed to flush its own worker's deletion
        immediately instead of leaving it pending for whichever event loop
        runs next.
        """
        class SlowWorker(QThread):
            def __init__(self, parent):
                super().__init__(parent)
                self.release = threading.Event()
            def run(self):
                self.release.wait(5)
            def cancel(self):
                pass

        renderer = FFmpegRenderer.__new__(FFmpegRenderer)
        first = AutoMixAnalysisController()
        second = PreviewAudioController(renderer)
        first_worker = SlowWorker(first)
        second_worker = SlowWorker(second)
        first._worker = first_worker
        second._worker = second_worker
        first_worker.finished.connect(lambda: first._forget(first_worker))
        second_worker.finished.connect(lambda: second._forget(second_worker))
        first_worker.start()
        second_worker.start()
        # first finishes almost immediately, so its deleteLater() would
        # otherwise still be pending when second's shutdown() opens its own
        # nested loop below.
        first_worker.release.set()
        first.shutdown()
        self.assertIsNone(first._worker)

        QTimer.singleShot(30, second_worker.release.set)
        second.shutdown()
        self.assertIsNone(second._worker)

    def test_replacement_waits_for_cancelled_analysis_to_finish(self):
        from app.controllers.automix_analysis_controller import _AutoMixAnalysisWorker
        release = threading.Event()
        controller = AutoMixAnalysisController()
        def slow_run(worker):
            release.wait(5)
        with patch.object(_AutoMixAnalysisWorker, 'run', slow_run):
            controller.start([_track('old', 10)], Path('ffmpeg'))
            old = controller._worker
            controller.start([_track('new', 10)], Path('ffmpeg'))
            self.assertIs(controller._worker, old)
            self.assertEqual(controller._pending[0][0].id, 'new')
            QTimer.singleShot(10, release.set)
            controller.shutdown()
            self.assertIsNone(controller._pending)


@unittest.skipUnless(os.environ.get('PLAYLIST_CANVAS_TEST_FFMPEG'), 'Real FFmpeg is opt-in')
class RealSharedPlanTests(unittest.TestCase):
    def test_prepared_mix_canvas_switch_chapters_and_video_duration_agree(self):
        from tests.test_automix_ffmpeg_integration import _write_tone_wav
        from app.automix.analysis.service import AnalysisBatchResult
        executable = Path(os.environ['PLAYLIST_CANVAS_TEST_FFMPEG'])
        with TemporaryDirectory() as directory:
            directory = Path(directory)
            tracks = [_track('a', 20.3), _track('b', 20.3)]
            for index, track in enumerate(tracks):
                track.file_path = str(directory / f'{track.id}.wav')
                _write_tone_wav(Path(track.file_path), 220 + index * 220, track.duration_seconds)
            analyses = {t.id: _analysis(t.id, 120, t.duration_seconds, meter_confidence=.3) for t in tracks}
            renderer = FFmpegRenderer(executable)
            settings = RenderSettings(fps=10, video_codec='libx264', crf=18, preset='ultrafast', output_width=32, output_height=32)
            plans = []
            with patch('app.automix.workflow.AutoMixWorkflow.analyze', return_value=AnalysisBatchResult(analyses, {})):
                audio = renderer.prepare_playlist_audio(tracks, directory, settings, 'automix', plan_callback=plans.append)
            plan = plans[0]
            # The compiled plan is the authority for where the switch lands
            # (C.1 exact geometry); audio, chapters, and canvas must all agree on it.
            switch_time = plan.metadata.chapters[1].start
            self.assertAlmostEqual(plan.audio.clips[1].timeline_start, switch_time)
            self.assertGreater(switch_time, 0.3)
            frames = []
            for sample in ExportTimelinePlanner.build(tracks, [], 10, plan):
                image = QImage(32, 32, QImage.Format.Format_RGB32)
                image.fill(QColor('red' if sample.track.id == 'a' else 'blue'))
                frames.append(RenderFrame(image, sample.duration_seconds))
            result = renderer.render(frames, tracks, directory/'out.mp4', settings,
                                     compiled_plan=plan, prepared_audio_path=audio)
            self.assertAlmostEqual(result.validation.duration_seconds, plan.duration_seconds, delta=.2)
            info = subprocess.run([str(executable.with_name('ffprobe.exe')), '-v', 'error', '-show_chapters', '-of', 'json', str(result.output_path)], capture_output=True, check=True, text=True)
            self.assertAlmostEqual(float(json.loads(info.stdout)['chapters'][1]['start_time']), switch_time)
            for second, channel in [(switch_time - 0.3, 0), (switch_time + 0.3, 2)]:
                pixels = subprocess.run([str(executable), '-v', 'error', '-ss', str(second), '-i', str(result.output_path), '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], capture_output=True, check=True).stdout
                self.assertGreater(pixels[channel], 200)
                self.assertLess(pixels[2 if channel == 0 else 0], 40)


    def test_repeated_preparation_plans_and_renders_the_same_dsp(self):
        # Preview (PreviewAudioController) and Export both call
        # prepare_playlist_audio; the style must be a pure function of the inputs.
        from tests.test_automix_ffmpeg_integration import _write_tone_wav
        from app.automix.analysis.service import AnalysisBatchResult
        executable = Path(os.environ['PLAYLIST_CANVAS_TEST_FFMPEG'])
        with TemporaryDirectory() as directory:
            directory = Path(directory)
            tracks = [_track('a', 20.3), _track('b', 20.3)]
            for index, track in enumerate(tracks):
                track.file_path = str(directory / f'{track.id}.wav')
                _write_tone_wav(Path(track.file_path), 220 + index * 220, track.duration_seconds)
            analyses = {t.id: _analysis(t.id, 120, t.duration_seconds) for t in tracks}
            renderer = FFmpegRenderer(executable)
            settings = RenderSettings(fps=10, video_codec='libx264', crf=18, preset='ultrafast', output_width=32, output_height=32)
            plans, outputs = [], []
            for run in ('preview', 'export'):
                with patch('app.automix.workflow.AutoMixWorkflow.analyze', return_value=AnalysisBatchResult(analyses, {})):
                    audio = renderer.prepare_playlist_audio(tracks, directory / run, settings, 'automix', plan_callback=plans.append)
                outputs.append(Path(audio).read_bytes())
            self.assertEqual(plans[0], plans[1])
            (transition,) = plans[0].audio.transitions
            self.assertIsNotNone(transition.dsp)
            self.assertTrue(transition.dsp_reasons[0].startswith(f'* {transition.dsp.value}'))
            self.assertEqual(outputs[0], outputs[1])


if __name__ == '__main__':
    unittest.main()
