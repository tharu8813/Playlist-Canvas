from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QItemSelectionModel, QMimeData, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QCloseEvent, QImage, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QGraphicsView, QMessageBox, QScrollArea,
    QWidget,
)

from app.models.project import CanvasSettings, ProjectDocument
from app.models.playlist import PlaylistTrack
from app.models.source import Source, SourceType
from app.dialogs.settings_dialog import SettingsDialog
from app.dialogs.preset_dialog import DesignPresetDialog
from app.dialogs.ai_project_builder_dialog import AIProjectBuilderDialog
from app.dialogs.help_dialog import HelpDialog
from app.dialogs.new_project_dialog import NewProjectDialog
from app.dialogs.track_details_dialog import TrackDetailsDialog
from app.dialogs.export_progress_dialog import ExportProgressDialog
from app.dialogs.ffmpeg_install_progress_dialog import FFmpegInstallProgressDialog
from app.ffmpeg.managed_installer import ManagedFFmpegInstallation
from app.services.autosave_service import RecoverySnapshot
from app.ui.main_window import MainWindow
from app.utils.i18n import Language
from app.preview.canvas_snapshot import CanvasSnapshot
from app.renderer.ffmpeg_renderer import RenderFrame


class MainWindowSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setApplicationName("Playlist Canvas Tests")
        cls.application.setOrganizationName("Playlist Canvas Tests")

    def setUp(self) -> None:
        self.window = MainWindow()
        self.application.processEvents()

    def tearDown(self) -> None:
        self.window._project_dirty = False
        self.window.close()
        self.application.processEvents()

    def test_new_project_cancel_preserves_unsaved_workspace(self) -> None:
        marker = Source(SourceType.TEXT, "UNSAVED_TEST_MARKER")
        self.window.store.add(marker)
        self.window._project_dirty = True
        with patch.object(
            QMessageBox, "warning", return_value=QMessageBox.StandardButton.Cancel,
        ):
            self.window._new_project()
        self.assertIsNotNone(self.window.store.get(marker.id))

    def test_startup_offers_recovery_before_project_choice(self) -> None:
        with patch.object(self.window, "_offer_recovery", return_value=True) as offer:
            with patch("app.ui.main_window.StartupDialog") as startup_dialog:
                self.assertTrue(self.window.show_startup_dialog())
        offer.assert_called_once_with()
        startup_dialog.assert_not_called()

    def test_stale_recovery_is_removed_instead_of_replacing_newer_project(self) -> None:
        with TemporaryDirectory(prefix="pvs-stale-recovery-") as raw_directory:
            project_path = Path(raw_directory) / "newer.pvsproj"
            project_path.write_bytes(b"newer saved project")
            snapshot = RecoverySnapshot(
                path=Path(raw_directory) / "stale.recovery.json",
                document=self.window._project_document(),
                project_path=project_path,
                saved_at=datetime.now(UTC) - timedelta(minutes=5),
            )
            with patch.object(
                self.window.autosave, "recoveries", return_value=[snapshot]
            ), patch.object(
                self.window.autosave, "clear_snapshot"
            ) as clear_snapshot, patch.object(
                QMessageBox, "question"
            ) as recovery_question:
                self.assertFalse(self.window._offer_recovery())
            clear_snapshot.assert_called_once_with(snapshot)
            recovery_question.assert_not_called()

    def test_close_cancel_keeps_unsaved_window_and_workspace_open(self) -> None:
        marker = Source(SourceType.TEXT, "CLOSE_CANCEL_MARKER")
        self.window.store.add(marker)
        self.window._project_dirty = True
        event = QCloseEvent()
        with patch.object(
            QMessageBox, "warning", return_value=QMessageBox.StandardButton.Cancel,
        ):
            self.window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertTrue(self.window._project_dirty)
        self.assertIsNotNone(self.window.store.get(marker.id))
        self.assertTrue(self.window.smooth_scroll._installed)

    def test_design_presets_and_ai_builder_are_separate_tools(self) -> None:
        preset_dialog = DesignPresetDialog(self.window.translator, self.window)
        builder_dialog = AIProjectBuilderDialog(self.window.translator, self.window)
        try:
            self.assertTrue(hasattr(preset_dialog, "selected_preset"))
            self.assertFalse(hasattr(preset_dialog, "prompt_preview"))
            self.assertTrue(hasattr(builder_dialog, "prompt_preview"))
            self.assertFalse(hasattr(builder_dialog, "selected_preset"))
            self.assertIsNot(
                self.window.presets_action, self.window.ai_project_builder_action
            )
        finally:
            preset_dialog.close()
            builder_dialog.close()

    def test_lyrics_inspector_offers_modern_transition_styles(self) -> None:
        values = {
            self.window.inspector.subtitle_animation_combo.itemData(index)
            for index in range(self.window.inspector.subtitle_animation_combo.count())
        }
        self.assertTrue({"apple_music", "spotify", "blur_reveal"}.issubset(values))
        self.assertEqual(Source(SourceType.LYRICS, "Lyrics").subtitle_animation, "apple_music")
        labels = [
            self.window.inspector.subtitle_animation_combo.itemText(index)
            for index in range(self.window.inspector.subtitle_animation_combo.count())
        ]
        self.assertIn("소프트 포커스", labels)
        self.assertIn("스무스 슬라이드", labels)
        self.assertFalse(any("Apple" in label or "Spotify" in label for label in labels))

    def test_menu_bar_is_grouped_and_fully_localized(self) -> None:
        original_language = self.window.translator.language
        try:
            self.window.translator.set_language(Language.KOREAN)
            self.application.processEvents()
            top_titles = [
                action.text() for action in self.window.menuBar().actions()
            ]
            self.assertEqual(
                top_titles,
                ["파일", "프로젝트", "편집", "추가", "보기", "도구", "도움말"],
            )
            self.assertIn(self.window.export_action, self.window.file_menu.actions())
            self.assertNotIn(self.window.preview_action, self.window.file_menu.actions())
            self.assertIn(self.window.presets_action, self.window.project_menu.actions())
            self.assertIn(
                self.window.ai_project_builder_action,
                self.window.project_menu.actions(),
            )
            self.assertIn(self.window.preview_action, self.window.view_menu.actions())
            self.assertIn(self.window.settings_action, self.window.tools_menu.actions())
            self.assertEqual(
                set(self.window.source_insert_actions), set(SourceType)
            )
            self.assertEqual(len(self.window.insert_category_menus), 4)
            for action in self.window.source_insert_actions.values():
                self.assertTrue(action.text())
                self.assertTrue(action.statusTip())

            before = len(self.window.store.sources())
            self.window.source_insert_actions[SourceType.IMAGE].trigger()
            self.assertEqual(len(self.window.store.sources()), before + 1)

            self.window.translator.set_language(Language.ENGLISH)
            self.application.processEvents()
            self.assertEqual(
                [action.text() for action in self.window.menuBar().actions()],
                ["File", "Project", "Edit", "Add", "View", "Tools", "Help"],
            )
            self.assertEqual(self.window.exit_action.text(), "Exit")
            self.assertEqual(
                self.window.clear_selection_action.text(), "Clear selection"
            )
            self.assertEqual(
                self.window.insert_category_menus["audio_effects"].title(),
                "Audio visuals",
            )
        finally:
            self.window.translator.set_language(original_language)
            self.application.processEvents()

    def test_app_theme_and_language_do_not_dirty_or_follow_project_metadata(self) -> None:
        original_language = self.window.translator.language
        original_theme = self.window.theme_service.preference
        self.window._project_dirty = False

        # Saving Settings with the already-selected theme republishes styling,
        # but it is not a project edit.
        self.window.theme_service.set_preference(original_theme)
        self.application.processEvents()
        self.assertFalse(self.window._project_dirty)

        document = self.window._project_document()
        document.language = "en" if original_language.value == "ko" else "ko"
        document.theme = "light" if original_theme.value != "light" else "dark"
        self.window._history_restoring = True
        try:
            self.window._apply_project(document)
        finally:
            self.window._history_restoring = False

        self.assertIs(self.window.translator.language, original_language)
        self.assertIs(self.window.theme_service.preference, original_theme)
        round_trip = self.window._project_document()
        self.assertEqual(round_trip.language, document.language)
        self.assertEqual(round_trip.theme, document.theme)

    def test_source_buttons_show_localized_settings_on_hover(self) -> None:
        original_language = self.window.translator.language
        try:
            self.window.translator.set_language(Language.KOREAN)
            self.application.processEvents()
            self.assertEqual(set(self.window._source_buttons), set(SourceType))
            for button in self.window._source_buttons.values():
                self.assertIn("추가 후 설정", button.toolTip())
                self.assertIn("클릭하면 캔버스에 추가됩니다.", button.toolTip())
                self.assertGreaterEqual(button.toolTipDuration(), 10_000)
                self.assertTrue(button.accessibleDescription())

            self.window.translator.set_language(Language.ENGLISH)
            self.application.processEvents()
            for button in self.window._source_buttons.values():
                self.assertIn("Settings after adding", button.toolTip())
                self.assertIn("Click to add it to the Canvas.", button.toolTip())
        finally:
            self.window.translator.set_language(original_language)
            self.application.processEvents()

    def test_source_sidebar_has_no_footer_tip(self) -> None:
        self.assertFalse(hasattr(self.window, "sidebar_hint"))
        self.assertIs(self.window.source_cards_scroll.parent(), self.window.source_sidebar)

    def test_inspector_properties_show_localized_detailed_hover_help(self) -> None:
        original_language = self.window.translator.language
        inspector = self.window.inspector
        try:
            self.window.translator.set_language(Language.KOREAN)
            self.application.processEvents()
            self.assertGreaterEqual(len(inspector._form_labels), 80)
            for key, label in inspector._form_labels.items():
                widget = inspector._field_widgets[key]
                self.assertTrue(label.toolTip(), key)
                self.assertEqual(label.toolTip(), widget.toolTip(), key)
                self.assertIn(label.text(), label.toolTip(), key)
                self.assertGreaterEqual(widget.toolTipDuration(), 10_000, key)
                self.assertTrue(widget.accessibleDescription(), key)
            self.assertIn("범위", inspector.width_spin.toolTip())
            self.assertIn("조절 단위", inspector.visualizer_sensitivity_spin.toolTip())
            self.assertIn("완전히 투명", inspector.particle_opacity_spin.toolTip())
            self.assertTrue(inspector.visible_check.toolTip())
            self.assertTrue(inspector.locked_check.toolTip())

            self.window.translator.set_language(Language.ENGLISH)
            self.application.processEvents()
            self.assertIn("Range", inspector.width_spin.toolTip())
            self.assertIn("Stacking order", inspector.z_spin.toolTip())
            self.assertIn("current-track data", inspector.text_edit.toolTip())
        finally:
            self.window.translator.set_language(original_language)
            self.application.processEvents()

    def test_empty_inspector_centers_selection_hint(self) -> None:
        self.window.store.select(None)
        self.application.processEvents()
        inspector = self.window.inspector
        self.assertFalse(inspector.empty_state.isHidden())
        self.assertTrue(inspector._content.isHidden())
        self.assertEqual(
            inspector.empty_state.alignment(), Qt.AlignmentFlag.AlignCenter
        )
        self.assertEqual(
            inspector.empty_state.text(), "요소를 선택해 속성을 편집하세요."
        )
        source = self.window.store.sources()[0]
        self.window.store.select(source.id)
        self.application.processEvents()
        self.assertTrue(inspector.empty_state.isHidden())
        self.assertFalse(inspector._content.isHidden())

    def test_undo_and_redo_keep_moved_source_selected(self) -> None:
        source = self.window.store.sources()[0]
        original_x = source.x
        moved_x = original_x + 140
        self.window.store.select(source.id)
        self.window.store.update(source.id, x=moved_x)

        self.window._undo()
        restored = self.window.store.get(source.id)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.x, original_x)
        self.assertEqual(self.window.store.selected.id, source.id)
        self.assertEqual(self.window.inspector._source_id, source.id)
        self.assertTrue(self.window.canvas._items[source.id].isSelected())
        self.assertEqual(self.window.canvas._items[source.id].pos().x(), original_x)

        self.window._redo()
        redone = self.window.store.get(source.id)
        self.assertIsNotNone(redone)
        self.assertEqual(redone.x, moved_x)
        self.assertEqual(self.window.store.selected.id, source.id)
        self.assertTrue(self.window.canvas._items[source.id].isSelected())

    def test_canvas_drag_defers_model_notifications_until_release(self) -> None:
        source = self.window.store.sources()[0]
        item = self.window.canvas._items[source.id]
        scene = self.window.canvas.scene_model
        scene.snap_enabled = False
        item.setSelected(True)
        notifications: list[tuple[float, float]] = []
        self.window.store.source_changed.connect(
            lambda changed: notifications.append((changed.x, changed.y))
        )

        scene.begin_item_interaction(item, include_selection=True)
        for offset in range(1, 31):
            item.setPos(source.x + offset, source.y + offset * 2)
        self.assertEqual(notifications, [])
        self.assertEqual(source.x, item.pos().x())
        self.assertEqual(source.y, item.pos().y())

        scene.finish_item_interaction()
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0], (item.pos().x(), item.pos().y()))
        self.assertEqual(
            self.window.canvas.viewportUpdateMode(),
            QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate,
        )

    def test_canvas_model_update_does_not_echo_a_second_store_signal(self) -> None:
        source = self.window.store.sources()[0]
        notifications: list[float] = []
        self.window.store.source_changed.connect(
            lambda changed: notifications.append(changed.x)
        )
        self.window.store.update(source.id, x=source.x + 50)
        self.assertEqual(len(notifications), 1)

    def test_layer_multi_selection_is_mirrored_on_canvas(self) -> None:
        first = Source(SourceType.TEXT, "MULTI_LAYER_FIRST", x=80, y=80)
        second = Source(SourceType.TEXT, "MULTI_LAYER_SECOND", x=260, y=80)
        self.window.store.add(first)
        self.window.store.add(second)
        self.application.processEvents()
        panel = self.window.layer_panel
        tree_items = {}
        for row in range(panel.tree.topLevelItemCount()):
            root = panel.tree.topLevelItem(row)
            for child_index in range(root.childCount()):
                child = root.child(child_index)
                tree_items[child.data(0, Qt.ItemDataRole.UserRole)] = child

        panel._refreshing = True
        try:
            panel.tree.clearSelection()
            tree_items[first.id].setSelected(True)
            tree_items[second.id].setSelected(True)
            panel.tree.setCurrentItem(
                tree_items[second.id], 0,
                QItemSelectionModel.SelectionFlag.NoUpdate,
            )
        finally:
            panel._refreshing = False
        panel._publish_selection()

        self.assertEqual(set(self.window.store.selected_ids), {first.id, second.id})
        self.assertEqual(self.window.store.selected.id, second.id)
        self.assertTrue(self.window.canvas._items[first.id].isSelected())
        self.assertTrue(self.window.canvas._items[second.id].isSelected())
        self.assertEqual(self.window.inspector._source_id, second.id)

    def test_canvas_multi_selection_is_mirrored_in_layer_panel(self) -> None:
        first = Source(SourceType.TEXT, "MULTI_CANVAS_FIRST", x=80, y=180)
        second = Source(SourceType.TEXT, "MULTI_CANVAS_SECOND", x=260, y=180)
        self.window.store.add(first)
        self.window.store.add(second)
        self.application.processEvents()
        self.window.store.select(None)

        self.window.canvas._items[first.id].setSelected(True)
        self.window.canvas._items[second.id].setSelected(True)

        self.assertEqual(set(self.window.store.selected_ids), {first.id, second.id})
        self.assertEqual(self.window.store.selected.id, second.id)
        self.assertEqual(
            set(self.window.layer_panel.selected_source_ids()),
            {first.id, second.id},
        )

    def test_inspector_multi_selection_shows_and_applies_common_properties(self) -> None:
        first = Source(
            SourceType.TEXT, "First", text="Alpha", x=40, opacity=0.7,
            fill_color="#FF0000", font_size=28, text_alignment="left",
            visible=True,
        )
        second = Source(
            SourceType.TEXT, "Second", text="Beta", x=180, opacity=0.7,
            fill_color="#0000FF", font_size=28, text_alignment="right",
            visible=False,
        )
        second.gradient.enabled = True
        self.window.store.replace([first, second])
        self.window.store.select_many([first.id, second.id], second.id)
        inspector = self.window.inspector

        self.assertEqual(set(inspector._source_ids), {first.id, second.id})
        self.assertEqual(inspector.x_spin.lineEdit().text(), "")
        self.assertNotEqual(inspector.opacity_spin.lineEdit().text(), "")
        self.assertEqual(inspector.fill_color_button.text(), "")
        self.assertEqual(inspector.text_edit.text(), "")
        self.assertEqual(inspector.text_alignment_combo.currentIndex(), -1)
        self.assertEqual(
            inspector.visible_check.checkState(), Qt.CheckState.PartiallyChecked,
        )
        self.assertEqual(
            inspector.gradient_check.checkState(), Qt.CheckState.PartiallyChecked,
        )
        self.assertFalse(inspector._field_widgets["font_family"].isHidden())

        # Leaving a mixed blank line edit untouched must never overwrite values.
        inspector.text_edit.editingFinished.emit()
        self.assertEqual((first.text, second.text), ("Alpha", "Beta"))

        inspector.x_spin.setValue(320)
        self.assertEqual((first.x, second.x), (320, 320))
        inspector.text_alignment_combo.setCurrentIndex(
            inspector.text_alignment_combo.findData("center")
        )
        self.assertEqual(
            (first.text_alignment, second.text_alignment), ("center", "center"),
        )
        inspector.visible_check.click()
        self.assertEqual(first.visible, second.visible)
        inspector.gradient_check.click()
        self.assertEqual(first.gradient.enabled, second.gradient.enabled)

        inspector.text_edit.setFocus()
        QTest.keyClicks(inspector.text_edit, "Unified")
        inspector.text_edit.editingFinished.emit()
        self.assertEqual((first.text, second.text), ("Unified", "Unified"))

        shape = Source(SourceType.SHAPE, "Shape")
        self.window.store.add(shape)
        self.window.store.select_many([first.id, shape.id], shape.id)
        self.assertTrue(inspector._field_widgets["font_family"].isHidden())
        self.assertTrue(inspector._field_widgets["font_size"].isHidden())
        self.assertTrue(inspector._field_widgets["text"].isHidden())

    def test_layer_panel_drag_reorders_canvas_and_supports_group_drop(self) -> None:
        first = Source(SourceType.TEXT, "First", z_index=0)
        second = Source(SourceType.SHAPE, "Second", z_index=1)
        third = Source(SourceType.IMAGE, "Third", z_index=2)
        self.window.store.replace([first, second, third])
        panel = self.window.layer_panel
        panel.refresh()
        self.assertEqual(
            panel.tree.dragDropMode(),
            QAbstractItemView.DragDropMode.InternalMove,
        )

        ungrouped = next(
            panel.tree.topLevelItem(row)
            for row in range(panel.tree.topLevelItemCount())
            if panel.tree.topLevelItem(row).data(0, panel._kind_role) == "root"
        )
        # The tree is front-to-back. Move the back row to the visible top and
        # commit exactly as a completed internal drag would.
        moved = ungrouped.takeChild(ungrouped.childCount() - 1)
        ungrouped.insertChild(0, moved)
        panel._commit_tree_order()
        self.assertEqual(first.z_index, 2)
        self.assertEqual(
            [source.id for source in self.window.store.sources()],
            [second.id, third.id, first.id],
        )

        group = self.window.store.add_group("Artwork", [third.id])
        panel.refresh()
        group_item = next(
            panel.tree.topLevelItem(row)
            for row in range(panel.tree.topLevelItemCount())
            if panel.tree.topLevelItem(row).data(0, panel._group_role) == group.id
        )
        ungrouped = next(
            panel.tree.topLevelItem(row)
            for row in range(panel.tree.topLevelItemCount())
            if panel.tree.topLevelItem(row).data(0, panel._kind_role) == "root"
        )
        first_item = next(
            ungrouped.child(index) for index in range(ungrouped.childCount())
            if ungrouped.child(index).data(0, Qt.ItemDataRole.UserRole) == first.id
        )
        ungrouped.takeChild(ungrouped.indexOfChild(first_item))
        group_item.insertChild(0, first_item)
        panel._commit_tree_order()
        self.assertEqual(first.group_id, group.id)

        group_item.setText(0, "Renamed artwork")
        self.assertEqual(group.name, "Renamed artwork")

    def test_layer_panel_moves_multi_selection_as_a_stable_block(self) -> None:
        first = Source(SourceType.TEXT, "First", z_index=0)
        second = Source(SourceType.SHAPE, "Second", z_index=1)
        third = Source(SourceType.IMAGE, "Third", z_index=2)
        self.window.store.replace([first, second, third])
        self.window.store.select_many([first.id, second.id], second.id)
        panel = self.window.layer_panel
        panel.refresh()
        panel._move_selected(1)
        self.assertEqual(
            [source.id for source in self.window.store.sources()],
            [third.id, first.id, second.id],
        )
        self.assertIn("3", panel.summary_label.text())
        self.assertIn("2", panel.summary_label.text())

    def test_canvas_sources_support_copy_cut_paste_and_standard_shortcuts(self) -> None:
        first = Source(SourceType.TEXT, "First", x=10, y=20, z_index=0)
        second = Source(SourceType.SHAPE, "Second", x=30, y=40, z_index=1)
        self.window.store.replace([first, second])
        group = self.window.store.add_group("Original group", [first.id, second.id])
        self.assertEqual(first.group_id, group.id)
        self.window.store.select_many([first.id, second.id], second.id)

        class FakeClipboard:
            def __init__(self) -> None:
                self.data = QMimeData()

            def setMimeData(self, data: QMimeData) -> None:
                self.data = data

            def mimeData(self) -> QMimeData:
                return self.data

        clipboard = FakeClipboard()
        with patch(
            "app.ui.main_window.QApplication.clipboard", return_value=clipboard,
        ):
            self.assertTrue(self.window._copy_selected_sources())
            self.assertTrue(clipboard.data.hasFormat(
                "application/x-playlist-video-studio-sources+json"
            ))

            self.window._paste_sources()
            pasted_ids = self.window.store.selected_ids
            self.assertEqual(len(pasted_ids), 2)
            pasted = [self.window.store.get(source_id) for source_id in pasted_ids]
            self.assertTrue(all(source is not None for source in pasted))
            self.assertEqual(
                {(source.x, source.y) for source in pasted if source is not None},
                {(34.0, 44.0), (54.0, 64.0)},
            )
            self.assertTrue(all(
                source.group_id is None for source in pasted if source is not None
            ))
            self.assertTrue(all(
                source.id not in {first.id, second.id}
                for source in pasted if source is not None
            ))

            self.window._cut_selected_sources()
            self.assertEqual(
                {source.id for source in self.window.store.sources()},
                {first.id, second.id},
            )
            self.window._paste_sources()
            self.assertEqual(len(self.window.store.sources()), 4)

        self.assertEqual(self.window.cut_action.shortcut().toString(), "Ctrl+X")
        self.assertEqual(self.window.copy_action.shortcut().toString(), "Ctrl+C")
        self.assertEqual(self.window.paste_action.shortcut().toString(), "Ctrl+V")
        initial_zoom = self.window.canvas.transform().m11()
        self.window._adjust_canvas_zoom(1.15)
        self.assertGreater(self.window.canvas.transform().m11(), initial_zoom)

    def test_toolbar_centers_selected_sources_and_tracks_editable_selection(self) -> None:
        first = Source(
            SourceType.TEXT, "Center me", x=35, y=45,
            width=240, height=120, scale=1.25,
        )
        second = Source(
            SourceType.SHAPE, "Also center me", x=410, y=280,
            width=160, height=90, scale=0.75,
        )
        self.window.store.replace([first, second])
        self.window.store.select_many([first.id, second.id], second.id)
        self.application.processEvents()

        toolbar_actions = self.window.toolbar.actions()
        self.assertIn(self.window.center_horizontal_action, toolbar_actions)
        self.assertIn(self.window.center_vertical_action, toolbar_actions)
        self.assertTrue(self.window.center_horizontal_action.isEnabled())
        self.assertTrue(self.window.center_vertical_action.isEnabled())
        self.assertIn("Ctrl+Shift+H", self.window.center_horizontal_action.toolTip())
        self.assertIn("Ctrl+Shift+V", self.window.center_vertical_action.toolTip())

        artboard = self.window.canvas.scene_model.artboard_rect
        self.window.center_horizontal_action.trigger()
        self.window.center_vertical_action.trigger()
        for source in (first, second):
            self.assertAlmostEqual(
                source.x, artboard.center().x() - source.width * source.scale / 2
            )
            self.assertAlmostEqual(
                source.y, artboard.center().y() - source.height * source.scale / 2
            )

        self.window.store.update(first.id, locked=True)
        self.window.store.update(second.id, locked=True)
        self.assertFalse(self.window.center_horizontal_action.isEnabled())
        self.assertFalse(self.window.center_vertical_action.isEnabled())
        self.window.store.select(None)
        self.assertFalse(self.window.center_horizontal_action.isEnabled())

    def test_canvas_context_menu_exposes_multi_source_editing_commands(self) -> None:
        first = Source(SourceType.TEXT, "First", x=40, y=80, z_index=0)
        second = Source(SourceType.SHAPE, "Second", x=260, y=180, z_index=1)
        third = Source(SourceType.IMAGE, "Third", x=500, y=240, z_index=2)
        self.window.store.replace([first, second, third])
        self.window.store.select_many([first.id, second.id], second.id)

        def actions_by_command(menu: object) -> dict[str, object]:
            result: dict[str, object] = {}
            for action in menu.actions():
                submenu = action.menu()
                if submenu is not None:
                    result.update(actions_by_command(submenu))
                elif action.data() is not None:
                    result[str(action.data())] = action
            return result

        menu = self.window.canvas._create_context_menu(
            self.window.canvas._items[first.id]
        )
        actions = actions_by_command(menu)
        self.assertTrue({
            "cut", "copy", "paste", "duplicate", "delete",
            "move_forward", "move_backward", "bring_front", "send_back",
            "center_horizontal", "center_vertical", "align_left",
            "align_hcenter", "align_right", "align_top", "align_vcenter",
            "align_bottom", "group", "ungroup", "toggle_visible",
            "toggle_lock", "select_all",
        }.issubset(actions))
        self.assertTrue(actions["align_left"].isEnabled())
        self.assertFalse(actions["ungroup"].isEnabled())

        actions["align_left"].trigger()
        self.assertEqual(first.x, second.x)
        actions["move_forward"].trigger()
        ordered_ids = [
            source.id for source in sorted(
                self.window.store.sources(), key=lambda source: source.z_index
            )
        ]
        self.assertEqual(ordered_ids, [third.id, first.id, second.id])
        actions["group"].trigger()
        self.assertIsNotNone(first.group_id)
        self.assertEqual(first.group_id, second.group_id)
        actions["toggle_lock"].trigger()
        self.assertTrue(first.locked)
        self.assertTrue(second.locked)

        empty_actions = actions_by_command(
            self.window.canvas._create_context_menu(None)
        )
        self.assertEqual(
            set(empty_actions), {"paste", "select_all", "fit_canvas"},
        )

    def test_canvas_animation_preview_locks_editing_and_restores_source(self) -> None:
        source = self.window.store.sources()[0]
        self.window.store.update(
            source.id,
            animation_in="slide_left",
            animation_out="zoom",
            animation_duration=0.1,
        )
        self.window.store.select(source.id)
        source = self.window.store.get(source.id)
        item = self.window.canvas._items[source.id]
        original_model = source.to_dict()
        original_position = QPointF(item.pos())
        original_scale = item.scale()
        original_opacity = item.opacity()

        self.window._preview_source_animation(source.id)
        self.assertTrue(self.window._animation_preview_active)
        self.assertTrue(self.window.animation_preview_controller.active)
        self.assertFalse(self.window.isEnabled())
        self.assertNotEqual(item.pos(), original_position)
        self.assertEqual(source.to_dict(), original_model)

        close_event = QCloseEvent()
        self.window.closeEvent(close_event)
        self.assertFalse(close_event.isAccepted())

        QTest.qWait(700)
        self.assertFalse(self.window._animation_preview_active)
        self.assertFalse(self.window.animation_preview_controller.active)
        self.assertTrue(self.window.isEnabled())
        self.assertEqual(item.pos(), original_position)
        self.assertEqual(item.scale(), original_scale)
        self.assertEqual(item.opacity(), original_opacity)
        self.assertTrue(item.isSelected())
        self.assertEqual(self.window.store.selected.id, source.id)
        self.assertEqual(source.to_dict(), original_model)

    def test_animation_preview_button_requires_configured_animation(self) -> None:
        source = self.window.store.sources()[0]
        self.window.store.update(
            source.id, animation_in="none", animation_out="none"
        )
        self.window.store.select(source.id)
        self.assertFalse(self.window.inspector.animation_preview_button.isEnabled())
        self.window.store.update(source.id, animation_in="fade")
        self.assertTrue(self.window.inspector.animation_preview_button.isEnabled())

    def test_new_project_requires_explicit_discard(self) -> None:
        marker = Source(SourceType.TEXT, "UNSAVED_TEST_MARKER")
        self.window.store.add(marker)
        self.window._project_dirty = True
        with (
            patch.object(
                QMessageBox, "warning", return_value=QMessageBox.StandardButton.Discard,
            ),
            patch.object(
                NewProjectDialog, "exec", return_value=QDialog.DialogCode.Accepted,
            ),
        ):
            self.window._new_project()
        self.assertIsNone(self.window.store.get(marker.id))

    def test_new_project_applies_creation_only_aspect_ratio(self) -> None:
        with patch.object(
            NewProjectDialog, "exec", return_value=QDialog.DialogCode.Accepted,
        ), patch.object(
            NewProjectDialog, "canvas_size",
            new_callable=lambda: property(lambda _dialog: (720, 1280)),
        ):
            created = self.window._new_project()

        self.assertTrue(created)
        artboard = self.window.canvas.scene_model.artboard_rect
        self.assertEqual((artboard.width(), artboard.height()), (720, 1280))
        self.assertEqual(
            (self.window._project_document().canvas.width,
             self.window._project_document().canvas.height),
            (720, 1280),
        )
        background = next(
            source for source in self.window.store.sources()
            if source.source_type is SourceType.BACKGROUND
        )
        self.assertEqual((background.width, background.height), (720, 1280))
        for source in self.window.store.sources():
            self.assertLessEqual(source.x + source.width, 720)
            self.assertLessEqual(source.y + source.height, 1280)

    def test_new_project_dialog_supports_presets_and_custom_size(self) -> None:
        dialog = NewProjectDialog(self.window.translator, self.window)
        try:
            self.assertEqual(dialog.canvas_size, (1280, 720))
            dialog.preset_combo.setCurrentIndex(1)
            self.assertEqual(dialog.canvas_size, (720, 1280))
            self.assertFalse(dialog.width_spin.isEnabled())
            dialog.preset_combo.setCurrentIndex(dialog.preset_combo.count() - 1)
            dialog.width_spin.setValue(1000)
            dialog.height_spin.setValue(1250)
            self.assertTrue(dialog.width_spin.isEnabled())
            self.assertEqual(dialog.canvas_size, (1000, 1250))
            self.assertIn("4:5", dialog.summary.text())
        finally:
            dialog.close()

    def test_track_lyrics_dialog_adjusts_only_selected_track_timing(self) -> None:
        original_language = self.window.translator.language
        self.window.translator.set_language(Language.KOREAN)
        track = PlaylistTrack(
            "track.wav", "Track", duration_seconds=20.0,
            lyrics_path="track.lrc",
            lyrics=[{"start": 5.0, "end": 7.0, "text": "첫 줄"}],
            lyrics_timing_offset_seconds=1.0,
        )
        dialog = TrackDetailsDialog(track, self.window.translator, self.window)
        try:
            self.assertIn("00:04.000", dialog.preview.toPlainText())
            dialog.timing_offset_spin.setValue(2.0)
            self.assertIn("00:03.000", dialog.preview.toPlainText())
            self.assertEqual(track.lyrics_timing_offset_seconds, 1.0)
            dialog._accept()
            self.assertEqual(dialog.selected_timing_offset, 2.0)
            self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        finally:
            dialog.close()
            self.window.translator.set_language(original_language)
            self.application.processEvents()

    def test_track_lyrics_dialog_previews_audio_with_synchronized_lyrics(self) -> None:
        saved_volumes: list[int] = []
        volume_reader = patch(
            "app.dialogs.track_details_dialog.preview_volume", return_value=37,
        )
        volume_writer = patch(
            "app.dialogs.track_details_dialog.save_preview_volume",
            side_effect=lambda value: saved_volumes.append(value) or value,
        )
        volume_reader.start()
        volume_writer.start()
        self.addCleanup(volume_writer.stop)
        self.addCleanup(volume_reader.stop)
        with TemporaryDirectory(prefix="playlist-track-preview-") as raw_directory:
            audio_path = Path(raw_directory) / "preview.wav"
            audio_path.touch()
            track = PlaylistTrack(
                str(audio_path), "Preview", duration_seconds=12.0,
                lyrics=[
                    {"start": 2.0, "end": 4.0, "text": "First lyric"},
                    {"start": 6.0, "end": 8.0, "text": "Second lyric"},
                ],
            )
            dialog = TrackDetailsDialog(track, self.window.translator, self.window)
            try:
                self.assertLessEqual(dialog.width(), 900)
                self.assertLessEqual(dialog.height(), 640)
                self.assertEqual((dialog.minimumWidth(), dialog.minimumHeight()), (780, 540))
                self.assertTrue(dialog.play_button.isEnabled())
                self.assertTrue(dialog.playback_slider.isEnabled())
                self.assertGreaterEqual(dialog.playback_slider.minimumWidth(), 220)
                self.assertGreaterEqual(dialog.playback_slider.minimumHeight(), 30)
                self.assertEqual(dialog.lyrics_preview_layout.spacing(), 3)
                self.assertEqual(
                    dialog.lyrics_preview_layout.indexOf(dialog.current_lyric), 1,
                )
                self.assertEqual(dialog.volume_slider.value(), 37)
                self.assertAlmostEqual(dialog.audio_output.volume(), 0.37, places=2)
                self.assertFalse(dialog.lyrics_group.isAncestorOf(dialog.playback_group))
                self.assertEqual(
                    Path(dialog.media_player.source().toLocalFile()), audio_path.resolve(),
                )

                dialog.volume_slider.setValue(64)
                self.assertEqual(saved_volumes, [64])
                self.assertAlmostEqual(dialog.audio_output.volume(), 0.64, places=2)
                self.assertEqual(dialog.volume_value.text(), "64%")

                korean = self.window.translator.language is Language.KOREAN
                dialog._playback_state_changed(
                    dialog.media_player.PlaybackState.PlayingState
                )
                self.assertEqual(dialog.play_button.text(), "일시정지" if korean else "Pause")
                dialog._playback_state_changed(
                    dialog.media_player.PlaybackState.PausedState
                )
                self.assertEqual(dialog.play_button.text(), "재생" if korean else "Play")

                dialog._playback_position_changed(0)
                self.assertEqual(dialog.current_lyric.text(), "First lyric")
                self.assertEqual(dialog.next_lyric.text(), "Second lyric")

                dialog._playback_position_changed(6_500)
                self.assertEqual(dialog.previous_lyric.text(), "First lyric")
                self.assertEqual(dialog.current_lyric.text(), "Second lyric")
                self.assertEqual(dialog.playback_slider.value(), 6_500)
                self.assertIn("00:06", dialog.playback_time.text())
            finally:
                dialog.reject()
                self.application.processEvents()
            self.assertEqual(
                dialog.media_player.playbackState(),
                dialog.media_player.PlaybackState.StoppedState,
            )

    def test_playlist_exposes_track_lyrics_settings_button(self) -> None:
        track = PlaylistTrack("track.wav", "Track", duration_seconds=20.0)
        self.window.playlist_service.replace([track])
        self.application.processEvents()
        item = self.window.playlist_editor.list_widget.item(0)
        item.setSelected(True)
        self.window.playlist_editor.list_widget.setCurrentItem(item)
        self.application.processEvents()
        requested: list[str] = []
        signal = self.window.playlist_editor.track_double_clicked
        signal.disconnect(self.window._show_track_details)
        try:
            signal.connect(requested.append)
            self.window.playlist_editor.details_button.click()
        finally:
            signal.disconnect(requested.append)
            signal.connect(self.window._show_track_details)
        self.assertEqual(requested, [track.id])

    def test_snap_setting_round_trip(self) -> None:
        self.window.canvas.scene_model.snap_enabled = False
        self.assertFalse(self.window._project_document().canvas.snap_enabled)
        self.window._apply_project(ProjectDocument(
            canvas=CanvasSettings(snap_enabled=False)
        ))
        self.assertFalse(self.window.canvas.scene_model.snap_enabled)
        self.assertFalse(self.window.snap_action.isChecked())

    def test_grid_covers_workspace_but_not_export_snapshot(self) -> None:
        scene = self.window.canvas.scene_model
        x_positions, y_positions = scene.grid_positions(scene.sceneRect())
        self.assertLess(min(x_positions), scene.artboard_rect.left())
        self.assertGreater(max(x_positions), scene.artboard_rect.right())
        self.assertLess(min(y_positions), scene.artboard_rect.top())
        self.assertGreater(max(y_positions), scene.artboard_rect.bottom())
        self.assertTrue(all(position % 40 == 0 for position in x_positions))
        self.assertTrue(all(position % 40 == 0 for position in y_positions))

        scene.show_grid = True
        snapshot_with_editor_grid = CanvasSnapshot.capture(scene, output_scale=0.25)
        self.assertTrue(scene.show_grid)
        scene.show_grid = False
        snapshot_without_editor_grid = CanvasSnapshot.capture(scene, output_scale=0.25)
        self.assertEqual(snapshot_with_editor_grid, snapshot_without_editor_grid)

    def test_ffmpeg_install_progress_belongs_to_modal_settings_dialog(self) -> None:
        dialog = SettingsDialog(
            self.window.settings_service.current,
            self.window.translator.language,
            self.window.theme_service.preference,
            self.window.translator,
            self.window,
        )
        self.window._settings_dialog = dialog
        dialog.download_requested.connect(
            lambda: self.window._start_ffmpeg_install(dialog)
        )
        with (
            patch.object(
                QMessageBox, "question",
                # Real PySide calls may return a value-equivalent wrapper.
                # This catches incorrect identity (`is`) comparisons.
                return_value=QMessageBox.StandardButton.Yes.value,
            ),
            patch("app.ui.main_window.FFmpegInstallWorker.start") as start,
        ):
            QTest.mouseClick(
                dialog.ffmpeg_download_button, Qt.MouseButton.LeftButton
            )
        self.assertTrue(dialog._ffmpeg_installing)
        self.assertFalse(dialog.ffmpeg_download_button.isEnabled())
        self.assertIsNotNone(self.window._ffmpeg_install_dialog)
        self.assertIsInstance(
            self.window._ffmpeg_install_dialog, FFmpegInstallProgressDialog
        )
        self.assertIs(self.window._ffmpeg_install_dialog.parent(), dialog)
        self.assertTrue(self.window._ffmpeg_install_dialog.isVisible())
        self.assertTrue(self.window._ffmpeg_install_dialog.isModal())
        self.assertEqual(
            self.window._ffmpeg_install_dialog.windowModality(),
            Qt.WindowModality.ApplicationModal,
        )
        self.assertIn("GitHub", self.window._ffmpeg_install_dialog.detail_label.text())
        start.assert_called_once()
        self.window._ffmpeg_install_dialog.complete(False)
        self.window._ffmpeg_install_dialog = None
        self.window._ffmpeg_install_worker = None
        self.window._settings_dialog = None
        dialog.deleteLater()

    def test_ffmpeg_install_success_is_applied_and_persisted_automatically(self) -> None:
        original = self.window.settings_service.current
        dialog = SettingsDialog(
            original,
            self.window.translator.language,
            self.window.theme_service.preference,
            self.window.translator,
            self.window,
        )
        self.window._settings_dialog = dialog
        try:
            with TemporaryDirectory() as directory:
                executable = Path(directory) / "ffmpeg.exe"
                executable.touch()
                installation = ManagedFFmpegInstallation(executable, "latest-test")
                with patch.object(QMessageBox, "information"):
                    self.window._ffmpeg_install_succeeded(installation)
                self.assertEqual(
                    self.window.settings_service.current.ffmpeg_path,
                    str(executable),
                )
                self.assertEqual(dialog.ffmpeg_edit.text(), str(executable))
                self.assertFalse(dialog._ffmpeg_installing)
        finally:
            self.window.settings_service.save(original)
            self.window._settings_dialog = None
            dialog.deleteLater()

    def test_smooth_scroll_settings_are_exposed_by_dialog(self) -> None:
        dialog = SettingsDialog(
            self.window.settings_service.current,
            self.window.translator.language,
            self.window.theme_service.preference,
            self.window.translator,
            self.window,
        )
        try:
            dialog.smooth_scroll_check.setChecked(False)
            dialog.smooth_scroll_duration_slider.setValue(320)
            settings = dialog.app_settings
            self.assertFalse(settings.smooth_scrolling)
            self.assertEqual(settings.smooth_scroll_duration_ms, 320)
            self.assertFalse(dialog.smooth_scroll_duration_slider.isEnabled())
        finally:
            dialog.close()

    def test_smooth_scroll_animates_wheel_but_preserves_ctrl_gestures(self) -> None:
        area = QScrollArea(self.window)
        content = QWidget()
        content.setFixedSize(200, 1200)
        area.setWidget(content)
        area.resize(220, 240)
        area.show()
        self.application.processEvents()
        service = self.window.smooth_scroll
        service.configure(True, 180)
        wheel = QWheelEvent(
            QPointF(20, 20), QPointF(20, 20), QPoint(), QPoint(0, -120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollUpdate, False,
        )
        self.assertTrue(service.eventFilter(area.viewport(), wheel))
        self.assertGreater(service._targets[area.verticalScrollBar()], 0)

        ctrl_wheel = QWheelEvent(
            QPointF(20, 20), QPointF(20, 20), QPoint(), QPoint(0, -120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
            Qt.ScrollPhase.ScrollUpdate, False,
        )
        self.assertFalse(service.eventFilter(area.viewport(), ctrl_wheel))
        service._stop_animations()
        area.close()

    def test_project_content_and_other_item_views_scroll_per_pixel(self) -> None:
        service = self.window.smooth_scroll
        content_list = self.window.content_library_panel.list
        content_list.addItems([f"Content {index}" for index in range(40)])
        content_list.resize(220, 180)
        content_list.show()
        self.application.processEvents()
        wheel = QWheelEvent(
            QPointF(20, 20), QPointF(20, 20), QPoint(), QPoint(0, -120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollUpdate, False,
        )
        self.assertTrue(service.eventFilter(content_list.viewport(), wheel))
        self.assertEqual(
            content_list.verticalScrollMode(),
            QAbstractItemView.ScrollMode.ScrollPerPixel,
        )
        self.assertGreater(service._targets[content_list.verticalScrollBar()], 0)

        other_item_views = (
            self.window.layer_panel.tree,
            self.window.playlist_editor.list_widget,
            self.window.timeline_panel.track_table,
            self.window.timeline_panel.source_table,
        )
        for view in other_item_views:
            service._prepare_scroll_area(view)
            self.assertEqual(
                view.verticalScrollMode(),
                QAbstractItemView.ScrollMode.ScrollPerPixel,
            )
            self.assertEqual(
                view.horizontalScrollMode(),
                QAbstractItemView.ScrollMode.ScrollPerPixel,
            )
        service._stop_animations()

    def test_project_content_context_menu_adds_removes_and_shows_information(self) -> None:
        panel = self.window.content_library_panel
        with TemporaryDirectory(prefix="pvs-content-menu-") as raw_directory:
            image_path = Path(raw_directory) / "cover.png"
            image_path.write_bytes(b"test image placeholder")
            self.window.project_content_service.add_paths([image_path])
            item = panel.list.item(0)
            panel.list.setCurrentItem(item)
            menu = panel._create_context_menu(item)
            actions = {
                str(action.data()): action
                for action in menu.actions() if action.data() is not None
            }
            self.assertEqual(
                list(actions), ["add", "remove", "information", "import"],
            )
            self.assertTrue(actions["add"].isEnabled())

            added: list[tuple[str, str]] = []
            panel.add_requested.connect(
                lambda path, media_type: added.append((path, media_type))
            )
            actions["add"].trigger()
            self.assertEqual(added, [(str(image_path.resolve()), "image")])

            with patch.object(QMessageBox, "information") as information:
                actions["information"].trigger()
            self.assertIn(str(image_path.resolve()), information.call_args.args[2])

            actions["remove"].trigger()
            self.assertEqual(panel.list.count(), 0)

        empty_menu = panel._create_context_menu(None)
        self.assertEqual(
            [action.data() for action in empty_menu.actions()], ["import"],
        )

    def test_about_action_opens_program_information(self) -> None:
        with patch("app.ui.main_window.AboutDialog") as about_dialog:
            self.window._show_about()
        about_dialog.assert_called_once()
        about_dialog.return_value.exec.assert_called_once()

    def test_help_action_opens_searchable_offline_guide(self) -> None:
        with patch("app.ui.main_window.HelpDialog") as help_dialog:
            self.window._show_help()
        help_dialog.assert_called_once_with(self.window.translator, self.window)
        help_dialog.return_value.exec.assert_called_once()
        self.assertEqual(self.window.help_action.shortcut().toString(), "F1")

    def test_help_dialog_filters_topics_and_shows_no_result_state(self) -> None:
        dialog = HelpDialog(self.window.translator, self.window)
        try:
            self.assertGreaterEqual(dialog.topic_list.count(), 20)
            self.assertEqual(dialog.current_topic_id, "start")
            all_identifiers = {
                dialog.topic_list.item(row).data(Qt.ItemDataRole.UserRole)
                for row in range(dialog.topic_list.count())
            }
            self.assertTrue({
                "workspace", "sources", "project_content", "lyrics",
                "audio_visuals", "full_preview", "export_process", "performance",
            }.issubset(all_identifiers))
            dialog.search_edit.setText("볼륨")
            volume_identifiers = {
                dialog.topic_list.item(row).data(Qt.ItemDataRole.UserRole)
                for row in range(dialog.topic_list.count())
            }
            self.assertIn("lyrics", volume_identifiers)
            self.assertIn("full_preview", volume_identifiers)
            dialog.search_edit.setText("FFmpeg")
            self.assertGreaterEqual(dialog.topic_list.count(), 1)
            identifiers = {
                dialog.topic_list.item(row).data(Qt.ItemDataRole.UserRole)
                for row in range(dialog.topic_list.count())
            }
            self.assertIn("ffmpeg", identifiers)
            dialog.search_edit.setText("__NO_HELP_RESULT__")
            self.assertEqual(dialog.topic_list.count(), 0)
            self.assertIn("검색 결과 없음", dialog.browser.toPlainText())
        finally:
            dialog.close()

    def test_export_staging_reuses_identical_consecutive_frames(self) -> None:
        self.window._clear_export_frame_staging()
        self.window._export_frame_staging = TemporaryDirectory(
            prefix="playlist-video-test-frames-"
        )
        image = QImage(48, 32, QImage.Format.Format_ARGB32)
        image.fill(QColor("#336699"))
        try:
            first = self.window._stage_export_frame(image, 0.5, "base")
            repeated = self.window._stage_export_frame(image.copy(), 1.0, "base")
            changed = image.copy()
            changed.setPixelColor(0, 0, QColor("#ffffff"))
            third = self.window._stage_export_frame(changed, 0.5, "base")

            self.assertEqual(first.image, repeated.image)
            self.assertNotEqual(first.image, third.image)
            self.assertEqual(self.window._export_capture_count, 3)
            self.assertEqual(self.window._export_frame_index, 2)
            self.assertTrue(first.image.is_file())
            self.assertTrue(third.image.is_file())
        finally:
            self.window._clear_export_frame_staging()

    def test_export_animation_sampling_is_capped_without_changing_output_setting(self) -> None:
        self.assertEqual(self.window._export_animation_sample_rate(60), 30)
        self.assertEqual(self.window._export_animation_sample_rate(24), 24)
        self.assertEqual(self.window._export_animation_sample_rate(10), 15)

    def test_export_static_layers_keep_intro_stable_outro_timeline_order(self) -> None:
        """Transparent Z bands must be captured in the same order as base frames."""
        track = PlaylistTrack(
            file_path="animation-order.mp3", title="Animation order",
            duration_seconds=2.0,
        )
        self.window.playlist_service.replace([track])
        self.window.store.add(Source(
            SourceType.TEXT, "Animated", animation_in="fade",
            animation_out="fade", animation_duration=0.2, z_index=2.0,
        ))
        captured_worker_arguments: list[tuple[object, ...]] = []

        class SignalStub:
            def connect(self, _callback: object) -> None:
                pass

        class WorkerStub:
            def __init__(self, *arguments: object) -> None:
                captured_worker_arguments.append(arguments)
                self.progress = SignalStub()
                self.succeeded = SignalStub()
                self.failed = SignalStub()
                self.cancelled = SignalStub()
                self.finished = SignalStub()

            def start(self) -> None:
                pass

            def cancel(self) -> None:
                pass

            def isRunning(self) -> bool:
                return False

            def deleteLater(self) -> None:
                pass

        phase_colors = {
            "in": QColor("#ff0000"),
            "stable": QColor("#00ff00"),
            "out": QColor("#0000ff"),
        }

        def capture_phase(*_arguments: object, **state: object) -> QImage:
            image = QImage(4, 4, QImage.Format.Format_ARGB32)
            image.fill(phase_colors[str(state.get("animation_phase") or "stable")])
            return image

        def stage_image(image: QImage, duration: float, _key: str) -> RenderFrame:
            return RenderFrame(image.copy(), duration)

        try:
            with (
                patch("app.ui.main_window.FFmpegRenderer"),
                patch("app.ui.main_window.RenderWorker", WorkerStub),
                patch("app.ui.main_window.ExportSettingsDialog.exec",
                      return_value=QDialog.DialogCode.Accepted),
                patch.object(CanvasSnapshot, "z_bands",
                             return_value=[(None, 1.0), (1.0, None)]),
                patch.object(CanvasSnapshot, "capture_track", side_effect=capture_phase),
                patch.object(self.window, "_stage_export_frame", side_effect=stage_image),
                patch.object(self.window, "_export_visualizers", return_value=[]),
            ):
                self.window._export_video()

            self.assertEqual(len(captured_worker_arguments), 1)
            static_layers = captured_worker_arguments[0][6]
            self.assertEqual(len(static_layers), 1)
            colors = [
                frame.image.pixelColor(0, 0).name()
                for frame in static_layers[0].frames
            ]
            self.assertIn("#ff0000", colors)
            self.assertIn("#00ff00", colors)
            self.assertIn("#0000ff", colors)
            self.assertLess(max(i for i, color in enumerate(colors) if color == "#ff0000"),
                            min(i for i, color in enumerate(colors) if color == "#00ff00"))
            self.assertLess(max(i for i, color in enumerate(colors) if color == "#00ff00"),
                            min(i for i, color in enumerate(colors) if color == "#0000ff"))
        finally:
            if self.window._export_dialog is not None:
                self.window._export_dialog.complete(False)
                self.window._export_dialog = None
            self.window._export_finished()
            self.application.processEvents()

    def test_export_locks_and_restores_every_main_form_interaction(self) -> None:
        dialog = ExportProgressDialog(self.window)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.window._export_dialog = dialog
        try:
            self.window._lock_main_form_for_export()

            self.assertFalse(self.window.centralWidget().isEnabled())
            self.assertFalse(self.window.menuBar().isEnabled())
            self.assertFalse(self.window.toolbar.isEnabled())
            self.assertFalse(self.window.export_action.isEnabled())
            self.assertFalse(self.window.acceptDrops())
            self.assertTrue(dialog.isEnabled())
            self.assertEqual(
                dialog.windowModality(), Qt.WindowModality.WindowModal,
            )

            self.window._unlock_main_form_after_export()

            self.assertTrue(self.window.centralWidget().isEnabled())
            self.assertTrue(self.window.menuBar().isEnabled())
            self.assertTrue(self.window.toolbar.isEnabled())
            self.assertTrue(self.window.export_action.isEnabled())
            self.assertTrue(self.window.acceptDrops())
        finally:
            self.window._unlock_main_form_after_export()
            self.window._export_dialog = None
            dialog.complete(False)

    def test_export_cancel_requires_confirmation(self) -> None:
        dialog = ExportProgressDialog(self.window)
        requested: list[bool] = []
        dialog.cancel_requested.connect(lambda: requested.append(True))
        try:
            with patch.object(
                QMessageBox, "question", return_value=QMessageBox.StandardButton.No,
            ):
                self.assertFalse(dialog.request_cancel())
            self.assertEqual(requested, [])
            self.assertTrue(dialog.cancel_button.isEnabled())

            with patch.object(
                QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes,
            ):
                self.assertTrue(dialog.request_cancel())
            self.assertEqual(requested, [True])
            self.assertFalse(dialog.cancel_button.isEnabled())
        finally:
            dialog.complete(False)

    def test_export_progress_translates_renderer_and_installer_messages(self) -> None:
        dialog = ExportProgressDialog(self.window)
        dialog.set_korean(True)
        try:
            translations = {
                "Analyzing audio and rendering Python visualizer frames":
                    "오디오를 분석하고 비주얼라이저 프레임을 생성하는 중",
                "Normalizing track.mp3": "오디오 정규화 중 · track.mp3",
                "Inserted 2.5s of silence": "무음 구간 2.5s 추가",
                "Encoding 12.0s / 60.0s": "영상 인코딩 중 · 12.0s / 60.0s",
                "Downloaded 24.0 MB": "다운로드됨 · 24.0 MB",
                "Checksum verified; extracting archive safely":
                    "체크섬 검증 완료 · 안전하게 압축 해제 중",
            }
            for source, expected in translations.items():
                self.assertEqual(dialog._detail_text(source), expected)
            self.assertEqual(dialog._stage_text("Preparing visualizers"), "비주얼라이저 준비")
            self.assertEqual(dialog._stage_text("Downloading FFmpeg"), "FFmpeg 다운로드")
            dialog.set_busy(
                "Preparing visualizers",
                "Analyzing audio and rendering Python visualizer frames",
            )
            self.assertEqual(dialog.stage_label.text(), "비주얼라이저 준비")
            self.assertEqual(
                dialog.detail_label.text(),
                "오디오를 분석하고 비주얼라이저 프레임을 생성하는 중",
            )
        finally:
            dialog.complete(False)


if __name__ == "__main__":
    unittest.main()
