"""Crossfade/AutoMix Canvas handover: mid-overlap owner change and mix animations."""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.dialogs.export_preview_dialog import ExportPreviewDialog  # noqa: E402
from app.inspector.source_inspector import SourceInspector  # noqa: E402
from app.models.source import Source, SourceType  # noqa: E402
from app.preview.frame_state import MixJunction, resolve_edge_animation  # noqa: E402
from app.renderer.export_timeline import ExportTimelinePlanner  # noqa: E402
from app.services.source_store import SourceStore  # noqa: E402
from app.timeline.models import TransitionType  # noqa: E402
from app.timeline.render_plan import (  # noqa: E402
    AudioRenderClip, AudioRenderPlan, AudioRenderTransition, CompiledRenderPlan,
    build_presentation_and_metadata, visual_segments,
)
from app.utils.i18n import Translator  # noqa: E402
from tests.test_automix_planner import _track  # noqa: E402


def _crossfaded_plan() -> CompiledRenderPlan:
    """a: 0-60, b: 52-112, an 8 s crossfade from 52: the Canvas hands over at 56."""
    clips = (AudioRenderClip("a", "a", 0, 0, 60), AudioRenderClip("b", "b", 52, 0, 60))
    presentation, metadata, duration = build_presentation_and_metadata(clips)
    transitions = (AudioRenderTransition("a", "b", 52.0, 8.0, TransitionType.CROSSFADE),)
    return CompiledRenderPlan(AudioRenderPlan(clips, transitions), presentation, metadata, duration)


def _fading_text() -> Source:
    source = Source(SourceType.TEXT, "Title")
    source.mix_animation_in = "fade"
    source.mix_animation_out = "fade"
    source.mix_animation_duration = 1.0
    return source


class MixVisualTransitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_canvas_hands_over_in_the_middle_of_the_overlap(self) -> None:
        plan = _crossfaded_plan()
        first, second = visual_segments(plan)
        self.assertAlmostEqual(first.end, 56.0)
        self.assertAlmostEqual(second.start, 56.0)
        self.assertTrue(first.mixed_out and second.mixed_in)
        self.assertFalse(first.mixed_in or second.mixed_out)
        # Chapters keep the audible start.
        self.assertAlmostEqual(plan.metadata.chapters[1].start, 52.0)

    def test_mix_animation_uses_its_own_style_and_the_handover_clock(self) -> None:
        source = _fading_text()
        source.animation_in = "slide_up"
        kwargs = dict(elapsed_seconds=4.0, track_duration=60.0, phase_duration=0.45)
        self.assertEqual(
            resolve_edge_animation(source, "in", junction=MixJunction(0.25, None), **kwargs),
            ("in", "fade", 0.25),
        )
        self.assertEqual(
            resolve_edge_animation(source, None, junction=MixJunction(None, 0.25), **kwargs),
            ("out", "fade", 0.75),
        )
        # After the mix entrance, the plain track-start animation never plays there.
        self.assertIsNone(resolve_edge_animation(source, "in", junction=MixJunction(2.0, None), **kwargs))
        source.mix_animation_in = "same"
        self.assertEqual(
            resolve_edge_animation(source, None, junction=MixJunction(0.0, None), **kwargs)[:2],
            ("in", "slide_up"),
        )

    def test_export_and_preview_share_the_handover(self) -> None:
        plan = _crossfaded_plan()
        tracks = [_track("a", 60), _track("b", 60)]
        samples = ExportTimelinePlanner.build(tracks, [_fading_text()], 30, plan)
        self.assertAlmostEqual(sum(sample.duration_seconds for sample in samples), plan.duration_seconds)
        position = 0.0
        for sample in samples:
            self.assertEqual(sample.track.id, "a" if position < 56.0 - 1e-6 else "b")
            position += sample.duration_seconds
        entrance = [s for s in samples if s.track.id == "b" and s.junction
                    and s.junction.visual_elapsed is not None and s.junction.visual_elapsed < 1.0]
        self.assertGreaterEqual(len(entrance), 25)  # dense, about 30 fps over 1 s
        self.assertAlmostEqual(entrance[0].elapsed_seconds, 4.0, places=3)
        exit_frames = [s for s in samples if s.track.id == "a" and s.junction
                       and s.junction.visual_remaining is not None and s.junction.visual_remaining < 1.0]
        self.assertAlmostEqual(min(s.junction.visual_remaining for s in exit_frames), 0.0)

        preview = SimpleNamespace(tracks=tracks, _compiled_plan=plan)
        selected, junction = ExportPreviewDialog._visual_track_at(preview, 55.5)
        self.assertEqual(selected[1].id, "a")
        self.assertAlmostEqual(junction.visual_remaining, 0.5)
        selected, junction = ExportPreviewDialog._visual_track_at(preview, 56.5)
        self.assertEqual(selected[1].id, "b")
        self.assertAlmostEqual(selected[2], 4.5)
        self.assertAlmostEqual(junction.visual_elapsed, 0.5)
        # Audio transport still switches when b starts playing.
        self.assertEqual(ExportPreviewDialog._track_at(preview, 53.0)[1].id, "b")

    def test_inspector_shows_mix_rows_only_while_the_project_mixes(self) -> None:
        store = SourceStore()
        source = _fading_text()
        store.add(source)
        inspector = SourceInspector(store, Translator())
        try:
            inspector.set_source(source)
            self.assertFalse(inspector._field_visibility.get("mix_animation_in", False))
            inspector.set_mix_transitions_active(True)
            self.assertTrue(inspector._field_visibility["mix_animation_in"])
            self.assertTrue(inspector._field_visibility["mix_animation_duration"])
            inspector.set_mix_transitions_active(False)
            self.assertFalse(inspector._field_visibility["mix_animation_out"])
        finally:
            inspector.close()
            inspector.deleteLater()


if __name__ == "__main__":
    unittest.main()
