from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.canvas.live_canvas import CanvasScene
from app.canvas.source_item import SourceItem
from app.models.playlist import PlaylistTrack
from app.models.source import Source, SourceType
from app.preview.canvas_snapshot import CanvasSnapshot
from app.preview.export_plan import build_export_plan
from app.renderer.ffmpeg_renderer import RenderError, RenderSettings


class _RendererStub:
    def __init__(self, missing: set[str] | None = None) -> None:
        self.missing = missing or set()
        self.checked: list[str] = []

    def ensure_encoder_available(self, encoder: str) -> None:
        self.checked.append(encoder)
        if encoder in self.missing:
            raise RenderError(f"{encoder} unavailable")


class ExportPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _scene(self, *sources: Source) -> CanvasScene:
        scene = CanvasScene()
        for source in sources:
            scene.addItem(SourceItem(source))
        return scene

    def _plan(self, scene, sources, renderer, **overrides):
        tracks = overrides.pop("tracks", [
            PlaylistTrack("a.mp3", "A", duration_seconds=3.0),
        ])
        return build_export_plan(
            scene, tracks, sources,
            overrides.pop("render_settings", RenderSettings()), 3.0,
            overrides.pop("visualizers", []),
            overrides.pop("video_clips", []),
            renderer, overrides.pop("animation_fps", 30),
        )

    def test_single_band_no_overlays_is_a_direct_final_stream(self) -> None:
        source = Source(SourceType.TEXT, "Title", text="Playlist")
        scene = self._scene(source)
        renderer = _RendererStub()
        with patch.object(CanvasSnapshot, "z_bands", return_value=[(None, None)]):
            plan = self._plan(scene, [source], renderer)
        self.assertTrue(plan.direct_final_stream)
        self.assertTrue(plan.use_streamed_visuals)
        self.assertEqual(plan.z_bands, [(None, None)])
        # A direct final stream needs no intermediate lossless encoder.
        self.assertEqual(renderer.checked, [])
        self.assertIn("base", plan.stream_timeline_samples)

    def test_missing_lossless_encoder_falls_back_to_png_staging(self) -> None:
        title = Source(SourceType.TEXT, "Title", text="Playlist", z_index=1.0)
        visualizer = Source(SourceType.AUDIO_VISUALIZER, "Bars", z_index=2.0)
        scene = self._scene(title, visualizer)
        renderer = _RendererStub(missing={"libx264rgb"})
        with (
            patch.object(CanvasSnapshot, "z_bands", return_value=[(None, None)]),
            patch.object(
                CanvasSnapshot, "split_mixed_capture_bands",
                return_value=[(None, None)],
            ),
        ):
            plan = self._plan(
                scene, [title, visualizer], renderer,
                visualizers=[object()],
            )
        self.assertFalse(plan.use_streamed_visuals)
        self.assertFalse(plan.direct_final_stream)
        self.assertIn("libx264rgb", renderer.checked)
        self.assertEqual(plan.dynamic_visualizer_ids, {visualizer.id})

    def test_canvas_render_scale_targets_output_resolution(self) -> None:
        source = Source(SourceType.TEXT, "Title", text="Playlist")
        scene = self._scene(source)
        scene.set_artboard_size(1280, 720)
        with patch.object(CanvasSnapshot, "z_bands", return_value=[(None, None)]):
            fhd = self._plan(
                scene, [source], _RendererStub(),
                render_settings=RenderSettings(output_width=1920, output_height=1080),
            )
            hd = self._plan(
                scene, [source], _RendererStub(),
                render_settings=RenderSettings(output_width=1280, output_height=720),
            )
        self.assertEqual(fhd.canvas_render_scale, 1.5)
        # Never downscale the authored canvas; FFmpeg still handles that case.
        self.assertEqual(hd.canvas_render_scale, 1.0)

    def test_empty_timeline_raises_render_error(self) -> None:
        source = Source(SourceType.TEXT, "Title", text="Playlist")
        scene = self._scene(source)
        with (
            patch.object(CanvasSnapshot, "z_bands", return_value=[(None, None)]),
            patch.object(
                CanvasSnapshot, "split_mixed_capture_bands",
                return_value=[(None, None)],
            ),
            self.assertRaises(RenderError),
        ):
            self._plan(scene, [source], _RendererStub(), tracks=[])


if __name__ == "__main__":
    unittest.main()
