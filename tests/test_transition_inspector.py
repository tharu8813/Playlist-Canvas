"""Preview's Transition details window: drawn envelopes, junction geometry, sync."""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.automix.planner import compile_automix  # noqa: E402
from app.timeline.models import TransitionType  # noqa: E402
from app.timeline.render_plan import AudioRenderTransition, TransitionDsp  # noqa: E402
from app.utils.i18n import Translator  # noqa: E402
from app.widgets.automix_details_panel import AutoMixDetailsPanel  # noqa: E402
from app.widgets.transition_inspector import (  # noqa: E402
    TransitionInspectorWindow, mix_lanes, plan_junctions,
)
from tests.test_automix_planner import ENABLED, _analysis, _track  # noqa: E402
from tests.test_mix_visual_transitions import _crossfaded_plan  # noqa: E402


class MixLaneTests(unittest.TestCase):
    def test_bass_swap_draws_the_renderer_band_envelopes(self) -> None:
        lanes = {lane.key: lane for lane in mix_lanes(
            AudioRenderTransition("a", "b", 0.0, 8.0, TransitionType.BEAT_MATCH, dsp=TransitionDsp.BASS_SWAP))}
        self.assertEqual(set(lanes), {"low", "mid", "high"})
        low = lanes["low"]  # (0.35, 0.60) on both sides
        self.assertEqual((low.outgoing(0.3), low.incoming(0.3)), (1.0, 0.0))
        self.assertAlmostEqual(low.outgoing(0.7), 0.0)
        self.assertAlmostEqual(low.incoming(0.7), 1.0)

    def test_plain_crossfade_is_one_linear_level_lane(self) -> None:
        (lane,) = mix_lanes(AudioRenderTransition("a", "b", 0.0, 8.0, TransitionType.CROSSFADE))
        self.assertAlmostEqual(lane.outgoing(0.5), 0.5)
        self.assertAlmostEqual(lane.incoming(0.25), 0.25)

    def test_drop_in_starts_the_incoming_track_at_full_level(self) -> None:
        (lane,) = mix_lanes(AudioRenderTransition("a", "b", 0.0, 8.0, TransitionType.EQUAL_POWER,
                                                  dsp=TransitionDsp.DROP_IN))
        self.assertAlmostEqual(lane.incoming(0.01), 1.0)
        self.assertGreater(lane.outgoing(0.5), 0.5)


class TransitionWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def _window(self, plan, tracks):
        panel = AutoMixDetailsPanel(Translator(), automix=True)
        panel.set_plan(plan, tracks, state="final")
        window = TransitionInspectorWindow(panel)
        self.addCleanup(window.deleteLater)
        self.addCleanup(panel.deleteLater)
        return panel, window

    def test_junction_geometry_matches_the_canvas_handover(self) -> None:
        (junction,) = plan_junctions(_crossfaded_plan())
        self.assertEqual((junction.start, junction.end, junction.handover), (52.0, 60.0, 56.0))

    def test_follows_the_playhead_until_a_transition_is_picked_by_hand(self) -> None:
        tracks = [_track("a", 200), _track("b", 180), _track("c", 210)]
        analyses = {t.id: _analysis(t.id, 120, t.duration_seconds) for t in tracks}
        plan = compile_automix(tracks, analyses, ENABLED)
        panel, window = self._window(plan, tracks)
        second = window.junctions[1]
        panel.set_playhead(second.start + 0.5)
        self.assertEqual(window.list.currentRow(), 1)
        window.list.setCurrentRow(0)
        self.assertFalse(window.follow_check.isChecked())
        panel.set_playhead(second.start + 1.0)
        self.assertEqual(window.list.currentRow(), 0)  # a manual choice sticks
        requested = []
        window.play_requested.connect(requested.append)
        window.play_button.click()
        self.assertAlmostEqual(requested[0], window.junctions[0].start - 4.0)

    def test_reselecting_replaces_the_metric_cards(self) -> None:
        tracks = [_track("a", 200), _track("b", 180), _track("c", 210)]
        analyses = {t.id: _analysis(t.id, 120, t.duration_seconds) for t in tracks}
        _panel, window = self._window(compile_automix(tracks, analyses, ENABLED), tracks)
        count = window.metrics_grid.count()
        self.assertGreater(count, 0)
        window._user_select(1)
        window._user_select(0)
        self.assertEqual(window.metrics_grid.count(), count)


if __name__ == "__main__":
    unittest.main()
