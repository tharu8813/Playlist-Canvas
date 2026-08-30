from __future__ import annotations

import os
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from app.models.playlist import PlaylistTrack
from app.preview.export_plan import ExportPlan
from app.preview.export_session import ExportSession, PngStaging
from app.renderer.export_timeline import ExportFrameSample
from app.renderer.ffmpeg_renderer import (
    PreparedVideoInput,
    RenderCancelledError,
    RenderFrame,
)
from app.renderer.static_video_stream import StaticVideoStreamResult


def _samples(count: int = 2) -> list[ExportFrameSample]:
    track = PlaylistTrack("a.mp3", "A", duration_seconds=2.0)
    return [
        ExportFrameSample(
            track=track, track_number=1, track_start_seconds=0.0,
            duration_seconds=2.0 / count, elapsed_seconds=index * 0.5,
            timeline_seconds=index * 0.5,
        )
        for index in range(count)
    ]


class _CapturerStub:
    instances: list["_CapturerStub"] = []

    def __init__(self, *_args, **_kwargs) -> None:
        _CapturerStub.instances.append(self)
        self.output_scale = _kwargs.get("output_scale", 1.0)
        self.invariant_stream_keys: set[str] = set()
        self.captured: list[tuple[str, float]] = []
        self.full_frame_source_pixels = 100
        self.scene_render_source_pixels = 100
        self.partial_render_capture_count = 0
        self._stage_frame = _kwargs.get("_stage") or (_args[5] if len(_args) > 5 else None)
        self._after_capture = _args[7] if len(_args) > 7 else _kwargs.get("_after")

    def coalesce_samples(self, samples, _stream_key):
        return list(samples)

    def capture_stream(self, sample, stream_key):
        image = QImage(2, 2, QImage.Format.Format_RGB32)
        rendered = self._stage_frame(image, sample.duration_seconds, stream_key)
        self._after_capture(sample.track_number, stream_key)
        return rendered

    def capture_invariant_stream(self, sample, stream_key, _duration):
        return self.capture_stream(sample, stream_key)

    def stream_origin(self, _stream_key):
        return (0.0, 0.0)

    def static_layers(self):
        return []


class _EncoderStub:
    def __init__(self, _executable, output_path, fps, **_kwargs) -> None:
        self.output_path = Path(output_path)
        self.fps = fps
        self.frame_count = 0
        self.submitted: list[float] = []

    def submit(self, _image, duration) -> None:
        self.submitted.append(duration)
        self.frame_count += 1

    def finish(self) -> StaticVideoStreamResult:
        self.output_path.touch()
        return StaticVideoStreamResult(
            self.output_path, sum(self.submitted) or 2.0, 4, 4, self.fps,
            self.output_path.name != "canvas-base.mkv", self.frame_count, 1,
        )

    def cancel(self) -> None:
        self.output_path.write_text("cancelled")


class _RendererStub:
    executable = Path("ffmpeg")

    def direct_encoding_profile(self, _settings):
        return None


class _StagingRecorder:
    def __init__(self) -> None:
        self.staged: list[str] = []
        self.started = False
        self.finished = False
        self.cancelled = False

    def staging(self) -> PngStaging:
        def stage(image, duration, key) -> RenderFrame:
            self.staged.append(key)
            return RenderFrame(Path(f"{key}.png"), max(0.001, duration))

        return PngStaging(
            stage_frame=stage,
            start_pipeline=lambda _cap: setattr(self, "started", True),
            finish_pipeline=lambda: setattr(self, "finished", True),
            cancel_pipeline=lambda: setattr(self, "cancelled", True),
            pending_frames=lambda: 0,
            queue_capacity=3,
        )


class _Settings:
    fps = 30
    output_width = 4
    output_height = 4
    video_codec = "libx264"


def _plan(*, streamed: bool, direct: bool, render_scale: float = 1.0) -> ExportPlan:
    return ExportPlan(
        z_bands=[(None, None)],
        dynamic_visualizer_ids=set(),
        direct_final_stream=direct,
        use_streamed_visuals=streamed,
        stream_timeline_samples={"base": _samples()},
        animation_fps=30,
        playlist_duration=2.0,
        canvas_render_scale=render_scale,
    )


class ExportSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        _CapturerStub.instances.clear()

    def _session(self, plan: ExportPlan, staging_rec: _StagingRecorder,
                 stream_root: Path | None, cancel: threading.Event) -> ExportSession:
        return ExportSession(
            scene=object(), renderer=_RendererStub(), plan=plan,
            render_settings=_Settings(), active_tracks=[_samples()[0].track],
            stream_root=stream_root, preparation_cancel=cancel, korean=False,
            staging=staging_rec.staging(),
            layer_worker_count=lambda count: 2,
            report_progress=lambda _f, _d: None,
            pump_ui=lambda: None,
        )

    def test_streamed_path_returns_prepared_video_input(self) -> None:
        with (
            TemporaryDirectory() as directory,
            patch("app.preview.export_session.ExportCanvasCapturer", _CapturerStub),
            patch("app.preview.export_session.StaticVideoStreamEncoder", _EncoderStub),
        ):
            recorder = _StagingRecorder()
            session = self._session(
                _plan(streamed=True, direct=True, render_scale=1.5), recorder,
                Path(directory), threading.Event(),
            )
            artifacts = session.run()
        self.assertIsInstance(artifacts.frames, PreparedVideoInput)
        self.assertTrue(artifacts.frames.ready_for_mux)
        self.assertEqual(artifacts.static_layers, [])
        self.assertEqual(session.capture_count, 2)
        # The plan's output/artboard ratio must reach the Canvas capturer.
        self.assertEqual(_CapturerStub.instances[0].output_scale, 1.5)

    def test_png_fallback_path_returns_render_frames(self) -> None:
        with (
            patch("app.preview.export_session.ExportCanvasCapturer", _CapturerStub),
            patch("app.preview.export_session.StaticVideoStreamEncoder", _EncoderStub),
        ):
            recorder = _StagingRecorder()
            session = self._session(
                _plan(streamed=False, direct=False), recorder, None,
                threading.Event(),
            )
            artifacts = session.run()
        self.assertIsInstance(artifacts.frames, list)
        self.assertTrue(all(isinstance(f, RenderFrame) for f in artifacts.frames))
        self.assertTrue(recorder.started and recorder.finished)
        self.assertEqual(recorder.staged, ["base", "base"])

    def test_cancel_during_capture_stops_streams(self) -> None:
        cancel = threading.Event()

        class _CancellingCapturer(_CapturerStub):
            def capture_stream(self, sample, stream_key):
                cancel.set()
                super().capture_stream(sample, stream_key)

        with (
            TemporaryDirectory() as directory,
            patch("app.preview.export_session.ExportCanvasCapturer", _CancellingCapturer),
            patch("app.preview.export_session.StaticVideoStreamEncoder", _EncoderStub),
        ):
            recorder = _StagingRecorder()
            session = self._session(
                _plan(streamed=True, direct=True), recorder,
                Path(directory), cancel,
            )
            with self.assertRaises(RenderCancelledError):
                session.run()
            session.cancel_streams()
        self.assertTrue(recorder.cancelled)


if __name__ == "__main__":
    unittest.main()
