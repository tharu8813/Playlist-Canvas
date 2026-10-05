"""Real mouse gestures edit the shared timing models only after release."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from app.controllers.preview_controller import PreviewController
from app.models.playlist import PlaylistTrack
from app.models.source import Source, SourceType
from app.services.playlist_service import PlaylistService
from app.services.source_store import SourceStore
from app.timeline.timeline_panel import TimelinePanel
from app.utils.i18n import Language, Translator
from app.widgets.transition_inspector import _nice_step


class VisualTimelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.playlist = PlaylistService()
        self.tracks = [PlaylistTrack("a.wav", "A", duration_seconds=20),
                       PlaylistTrack("b.wav", "B", duration_seconds=20)]
        self.playlist.replace(self.tracks)
        self.store = SourceStore()
        self.source = Source(SourceType.TEXT, "Title", timeline_start=5, timeline_duration=10)
        self.store.add(self.source)
        self.translator = Translator(); self.translator.set_language(Language.KOREAN)
        self.panel = TimelinePanel(self.playlist, self.store, self.translator)
        self.panel.resize(900, 380); self.panel.show(); self.app.processEvents()
        self.visual = self.panel.visual
        self.visual.snapping = False
        self.addCleanup(self.panel.close)

    def block(self, identifier):
        return next(block for block in self.visual.blocks if block.identifier == identifier)

    def begin(self, block, mode="move"):
        rect = self.visual.block_rect(block)
        point = rect.center().toPoint()
        if mode == "start": point.setX(round(rect.left() + 3))
        if mode == "end": point.setX(round(rect.right() - 3))
        QTest.mousePress(self.visual.viewport(), Qt.MouseButton.LeftButton, pos=point)
        return point

    def finish(self, point):
        QTest.mouseRelease(self.visual.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.app.processEvents()

    def test_move_commits_once_and_escape_does_not_change_model(self):
        changed = QSignalSpy(self.store.source_changed)
        point = self.begin(self.block(self.source.id))
        moved = point + QPoint(60, 0)
        QTest.mouseMove(self.visual.viewport(), moved)
        self.assertEqual(self.source.timeline_start, 5)
        self.assertEqual(changed.count(), 0)
        self.assertIsNotNone(self.visual._draft)
        expected = round(5 + 60 / self.visual.pixels_per_second, 2)
        self.finish(moved)
        self.assertEqual(self.source.timeline_start, expected)
        self.assertEqual(self.source.timeline_duration, 10)
        self.assertEqual(changed.count(), 1)
        self.assertEqual(self.panel.source_table.cellWidget(0, 1).value(), expected)
        point = self.begin(self.block(self.source.id))
        QTest.mouseMove(self.visual.viewport(), point + QPoint(80, 0))
        QTest.keyClick(self.visual, Qt.Key.Key_Escape)
        self.finish(point + QPoint(80, 0))
        self.assertEqual(self.source.timeline_start, expected)
        self.assertEqual(changed.count(), 1)

    def test_both_edges_preserve_opposite_edge_and_full_can_be_restored(self):
        point = self.begin(self.block(self.source.id), "start")
        QTest.mouseMove(self.visual.viewport(), point + QPoint(30, 0))
        self.finish(point + QPoint(30, 0))
        self.assertAlmostEqual(self.source.timeline_start + self.source.timeline_duration, 15, places=2)
        start = self.source.timeline_start
        point = self.begin(self.block(self.source.id), "end")
        QTest.mouseMove(self.visual.viewport(), point + QPoint(50, 0))
        self.finish(point + QPoint(50, 0))
        self.assertEqual(self.source.timeline_start, start)
        self.assertGreater(self.source.timeline_start + self.source.timeline_duration, 15)
        self.panel._reset_visual_timing("source", self.source.id); self.app.processEvents()
        self.assertTrue(self.block(self.source.id).full)
        self.assertEqual(self.source.timeline_duration, 0)
        QTest.keyClick(self.visual, Qt.Key.Key_Right)
        self.app.processEvents()
        self.assertEqual(self.source.timeline_duration, 0)
        point = self.begin(self.block(self.source.id), "end")
        QTest.mouseMove(self.visual.viewport(), point - QPoint(40, 0)); self.finish(point - QPoint(40, 0))
        self.assertGreater(self.source.timeline_duration, 0)

    def test_music_ripples_following_tracks_and_clamps_previous_end(self):
        point = self.begin(self.block(self.tracks[0].id))
        QTest.mouseMove(self.visual.viewport(), point + QPoint(60, 0))
        draft = self.visual._build_blocks()
        a, b = [block for block in draft if block.kind == "track"]
        self.assertGreater(a.start, 0)
        self.assertEqual(a.end, b.start)
        self.assertIsNone(self.tracks[0].start_time_seconds)
        self.finish(point + QPoint(60, 0))
        self.assertEqual(self.playlist.timeline_tracks()[1][1], self.block(self.tracks[0].id).end)
        self.visual.fit_all()
        point = self.begin(self.block(self.tracks[1].id))
        QTest.mouseMove(self.visual.viewport(), point - QPoint(100, 0)); self.finish(point - QPoint(100, 0))
        self.assertEqual(self.playlist.timeline_tracks()[1][1], self.playlist.minimum_start_time(self.tracks[1].id))
        self.assertIsNone(self.tracks[1].start_time_seconds)
        self.assertEqual(self.tracks[1].duration_seconds, 20)

    def test_snap_zoom_keyboard_locked_and_shared_selection(self):
        self.assertGreaterEqual(_nice_step(86400, 720), 10800)
        block = self.block(self.source.id)
        self.visual.snapping = True
        snapped = self.visual._snap(20 + 3 / self.visual.pixels_per_second, block, False)
        self.assertEqual(snapped, 20)
        self.assertEqual(self.visual._snap(20.123, block, True), 20.123)
        anchor = 550
        before = self.visual.time_at(anchor)
        self.visual.zoom(3, anchor)
        self.assertAlmostEqual(self.visual.time_at(anchor), before, delta=1 / self.visual.pixels_per_second)
        self.assertGreater(self.visual.horizontalScrollBar().maximum(), 0)
        self.visual.fit_all()
        point = self.begin(self.block(self.source.id)); self.finish(point)
        self.assertIs(self.store.selected, self.source)
        QTest.keyClick(self.visual, Qt.Key.Key_Up)
        self.assertEqual(self.visual.selected, ("track", self.tracks[1].id))
        QTest.keyClick(self.visual, Qt.Key.Key_Down)
        self.assertEqual(self.visual.selected, ("source", self.source.id))
        QTest.keyClick(self.visual, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier); self.app.processEvents()
        self.assertEqual(self.source.timeline_start, 5.1)
        self.store.update(self.source.id, locked=True); self.app.processEvents()
        point = self.begin(self.block(self.source.id))
        QTest.mouseMove(self.visual.viewport(), point + QPoint(50, 0)); self.finish(point + QPoint(50, 0))
        self.assertEqual(self.source.timeline_start, 5.1)
        QTest.keyClick(self.visual, Qt.Key.Key_Right); self.app.processEvents()
        self.assertEqual(self.source.timeline_start, 5.1)
        for index in range(12): self.store.add(Source(SourceType.TEXT, str(index)))
        self.app.processEvents()
        self.store.select(self.source.id)
        self.assertGreater(self.visual.verticalScrollBar().value(), 0)

    def test_numeric_view_position_preview_and_empty_state(self):
        self.panel._edit_numerically("source", self.source.id)
        self.assertEqual(self.panel.view_stack.currentIndex(), 1)
        self.panel.source_table.cellWidget(0, 1).setValue(7.25); self.app.processEvents()
        self.assertEqual(self.block(self.source.id).start, 7.25)
        self.assertIn(".25", self.panel.source_table.cellWidget(0, 1).text())
        self.panel.view_combo.setCurrentIndex(0)
        self.visual.set_playhead(12.3)
        preview = QSignalSpy(self.panel.preview_requested)
        self.panel.preview_button.click()
        self.assertEqual(preview.at(0)[0], 12.3)
        player = SimpleNamespace(_seek_to_seconds=Mock())
        window = SimpleNamespace(_show_bottom_panel=Mock(), _inline_preview=player)
        PreviewController(window).preview_at(12.3)
        window._show_bottom_panel.assert_called_once_with(2)
        player._seek_to_seconds.assert_called_once_with(12.3)
        self.playlist.replace([]); self.store.remove(self.source.id); self.app.processEvents()
        self.assertFalse(self.panel.preview_button.isEnabled())
        self.assertEqual(self.visual.blocks, [])
        self.assertFalse(self.visual.grab().isNull())
