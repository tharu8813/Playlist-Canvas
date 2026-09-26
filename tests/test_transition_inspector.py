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
        window.listen_button.click()
        self.assertAlmostEqual(requested[0], window.junctions[0].start - 4.0)

    def test_transport_drives_and_follows_preview_playback(self) -> None:
        tracks = [_track("a", 200), _track("b", 180), _track("c", 210)]
        analyses = {t.id: _analysis(t.id, 120, t.duration_seconds) for t in tracks}
        panel, window = self._window(compile_automix(tracks, analyses, ENABLED), tracks)
        toggled, seeks, volumes = [], [], []
        window.playing_toggled.connect(toggled.append)
        window.seek_requested.connect(seeks.append)
        window.volume_changed.connect(volumes.append)

        window.set_playing(True)  # Preview started: mirror it, do not echo it back
        self.assertEqual(toggled, [])
        self.assertTrue(window.transport_play_button.isChecked())
        window.transport_play_button.click()
        self.assertEqual(toggled, [False])

        panel.set_playhead(30.0)  # following the playhead never seeks
        self.assertEqual(window.position_slider.value(), 30000)
        self.assertIn("0:30.0", window.time_label.text())
        window.position_slider.setValue(45000)
        window.forward_button.click()
        self.assertEqual(seeks, [45.0, 35.0])

        window.set_volume(40)
        self.assertEqual(volumes, [])
        window.volume_slider.setValue(55)
        self.assertEqual(volumes, [55])

        seeks.clear()
        window.next_button.click()  # like a track skip: next transition, from its run-up
        self.assertEqual(window.list.currentRow(), 1)
        self.assertAlmostEqual(seeks[-1], window.junctions[1].start - 4.0)

    def test_details_fold_away_and_the_playing_card_is_marked(self) -> None:
        tracks = [_track("a", 200), _track("b", 180), _track("c", 210)]
        analyses = {t.id: _analysis(t.id, 120, t.duration_seconds) for t in tracks}
        panel, window = self._window(compile_automix(tracks, analyses, ENABLED), tracks)
        self.assertTrue(window.details_box.isHidden())  # planner internals only on request
        window.details_button.setChecked(True)
        self.assertFalse(window.details_box.isHidden())
        self.assertIn("vocal_safe_eq", window.reasons_label.text())
        role = window.list.itemDelegate().ROLE
        panel.set_playhead(window.junctions[1].start + 0.5)
        self.assertTrue(window.list.item(1).data(role)["playing"])
        self.assertFalse(window.list.item(0).data(role)["playing"])
        panel.set_playhead(window.junctions[1].end + 5.0)
        self.assertFalse(window.list.item(1).data(role)["playing"])

    def test_loop_replays_the_selected_transition(self) -> None:
        tracks = [_track("a", 200), _track("b", 180), _track("c", 210)]
        analyses = {t.id: _analysis(t.id, 120, t.duration_seconds) for t in tracks}
        panel, window = self._window(compile_automix(tracks, analyses, ENABLED), tracks)
        seeks = []
        window.seek_requested.connect(seeks.append)
        window._user_select(0)
        window.loop_check.setChecked(True)
        window.set_playing(True)
        junction = window.junctions[0]
        panel.set_playhead(junction.end + 1.0)
        self.assertEqual(seeks, [])
        panel.set_playhead(junction.end + 2.1)
        self.assertAlmostEqual(seeks[0], junction.start - 4.0)

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
