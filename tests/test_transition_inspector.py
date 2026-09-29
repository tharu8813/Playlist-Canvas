"""Preview's Transition details window: drawn envelopes, junction geometry, sync."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

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

    def test_detail_tabs_preserve_selection_and_do_not_control_playback(self) -> None:
        panel, window = self._window(_crossfaded_plan(), [])
        selected = window.junction
        with patch.object(panel, "set_plan") as replan:
            window.detail_tabs.setCurrentIndex(1)
            window.details_button.setChecked(True)
            self.assertFalse(window.details_box.isHidden())
            window.detail_tabs.setCurrentIndex(0)
        self.assertIs(window.junction, selected)
        self.assertFalse(window._playing)
        replan.assert_not_called()

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
        self.assertEqual(seeks, [])  # wait until Preview's frame update has returned
        self.application.processEvents()
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

    def test_scrubbing_seeks_once_on_release_and_ignores_playback_updates(self) -> None:
        panel, window = self._window(_crossfaded_plan(), [])
        slider = window.position_slider
        slider.resize(600, 30)
        seeks = []
        window.seek_requested.connect(seeks.append)
        QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=QPoint(150, 15))
        QTest.mouseMove(slider, QPoint(300, 15))
        preview_position = slider.sliderPosition()
        panel.set_playhead(1.0)
        self.assertEqual(slider.sliderPosition(), preview_position)
        self.assertEqual(seeks, [])
        QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=QPoint(450, 15))
        self.assertEqual(len(seeks), 1)
        self.assertAlmostEqual(seeks[0], slider.value() / 1000)
        self.assertGreater(seeks[0], window._plan.duration_seconds * 0.7)
        self.assertFalse(window.follow_check.isChecked())

    def test_graph_click_seeks_and_right_click_does_not(self) -> None:
        _panel, window = self._window(_crossfaded_plan(), [])
        diagram = window.diagram
        diagram.resize(720, diagram.minimumHeight())
        seeks = []
        diagram.seek_requested.connect(seeks.append)
        point = QPoint(round(diagram._x(56)), round(diagram.MARKER_AREA + 8))
        QTest.mouseClick(diagram, Qt.MouseButton.RightButton, pos=point)
        self.assertEqual(seeks, [])
        QTest.mouseClick(diagram, Qt.MouseButton.LeftButton, pos=point)
        self.assertEqual(len(seeks), 1)
        self.assertAlmostEqual(seeks[0], 56, delta=diagram.seconds_per_pixel())

    def test_details_are_read_only_and_show_what_plays(self) -> None:
        """Opening or browsing the details never edits, re-plans or re-renders anything."""
        from app.automix.overrides import TransitionOverride

        tracks = [_track("a", 200), _track("b", 180), _track("c", 210)]
        analyses = {t.id: _analysis(t.id, 120, t.duration_seconds) for t in tracks}
        plan = compile_automix(tracks, analyses, ENABLED.with_overrides(
            {"a>b": TransitionOverride(150.0, 2.0, 10.0, "vocal_safe_eq", False, 0.4)}))
        panel, window = self._window(plan, tracks)
        for name in ("editor", "manual_button", "enable_editing", "override_changed"):
            self.assertFalse(hasattr(window, name), name)
        self.assertFalse(hasattr(window.diagram, "drag_moved"))
        with patch.object(panel, "set_plan") as replan:
            window._user_select(1)
            window._user_select(0)
            window.details_button.setChecked(True)
        replan.assert_not_called()
        self.assertIs(window._plan, plan)
        self.assertTrue(window.heading_label.text().startswith("✎"))  # set by hand, per the plan itself
        cards = [window.metrics_grid.itemAt(i).widget().accessibleName() for i in range(window.metrics_grid.count())]
        self.assertTrue(any("A 2:30.0" in card and "B 0:02.0" in card for card in cards), cards)
        self.assertTrue(any("40%" in card for card in cards), cards)  # the vocal handoff actually rendered

    def test_playhead_reuses_curves_and_resize_rebuilds_them(self) -> None:
        _panel, window = self._window(_crossfaded_plan(), [])
        diagram = window.diagram
        diagram.resize(720, diagram.minimumHeight())
        from dataclasses import replace
        from unittest.mock import Mock

        gain = Mock(wraps=diagram.lanes[0].outgoing)
        diagram.lanes[0] = replace(diagram.lanes[0], outgoing=gain)
        diagram.render(QPixmap(diagram.size()))
        calls = gain.call_count
        self.assertGreater(calls, 0)
        diagram.playhead = 55.0
        diagram.render(QPixmap(diagram.size()))
        self.assertEqual(gain.call_count, calls)
        diagram.resize(800, diagram.height())
        diagram.render(QPixmap(diagram.size()))
        self.assertGreater(gain.call_count, calls)

    def test_progress_does_not_retranslate_or_rebuild_the_details(self) -> None:
        panel, window = self._window(_crossfaded_plan(), [])
        with patch.object(window, "_retranslate") as translate, \
                patch.object(window, "_show_selected") as rebuild:
            panel.set_progress_message("Rendering 30%")
            panel.set_progress_message("Rendering 40%")
        translate.assert_not_called()
        rebuild.assert_not_called()

    def test_overview_single_click_seeks_and_a_user_seek_can_leave_a_loop(self) -> None:
        _panel, window = self._window(_crossfaded_plan(), [])
        seeks = []
        window.seek_requested.connect(seeks.append)
        window.loop_check.setChecked(True)
        window.overview.resize(600, 78)
        point = QPoint(round(window.overview._x(10)), 20)
        QTest.mouseClick(window.overview, Qt.MouseButton.LeftButton, pos=point)
        self.assertEqual(len(seeks), 1)
        self.assertAlmostEqual(seeks[0], 10, delta=0.2)
        self.assertFalse(window.loop_check.isChecked())

    def test_follow_keeps_the_transition_visible_through_its_tail(self) -> None:
        tracks = [_track(name, 200) for name in "abc"]
        analyses = {t.id: _analysis(t.id, 120, t.duration_seconds) for t in tracks}
        panel, window = self._window(compile_automix(tracks, analyses, ENABLED), tracks)
        first = window.junctions[0]
        panel.set_playhead(first.end + 1)
        self.assertEqual(window._selected, 0)
        window.loop_check.setChecked(True)
        window.set_playing(True)
        seeks = []
        window.seek_requested.connect(seeks.append)
        panel.set_playhead(first.end + 2.1)
        self.application.processEvents()
        self.assertEqual(window._selected, 0)
        self.assertAlmostEqual(seeks[0], first.start - 4)


if __name__ == "__main__":
    unittest.main()
