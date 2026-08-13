from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import (QItemSelectionModel, QMimeData, QPoint, QPointF, QRectF,
                            Qt)
from PySide6.QtGui import QColor, QCloseEvent, QImage, QPalette, QPixmap, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QColorDialog, QDialog, QFileDialog,
    QFormLayout, QGraphicsView, QMessageBox, QScrollArea, QStyle,
    QStyleOptionSpinBox,
    QWidget,
)

from app import __version__
from app.models.project import CanvasSettings, ProjectContent, ProjectDocument
from app.models.playlist import PlaylistTrack
from app.models.source import Source, SourceType
from app.dialogs.settings_dialog import SettingsDialog
from app.dialogs.preset_dialog import DesignPresetDialog
from app.dialogs.ai_project_builder_dialog import AIProjectBuilderDialog
from app.dialogs.audio_metadata_dialog import AudioMetadataDialog
from app.dialogs.help_dialog import HelpDialog
from app.dialogs.new_project_dialog import NewProjectDialog
from app.dialogs.startup_dialog import StartupDialog
from app.dialogs.project_settings_dialog import ProjectSettingsDialog
from app.dialogs.export_settings_dialog import ExportSettingsDialog
from app.dialogs.project_crash_report_dialog import ProjectCrashReportDialog
from app.dialogs.lrc_generator_dialog import LrcGeneratorDialog
from app.dialogs.track_details_dialog import TrackDetailsDialog
from app.dialogs.export_progress_dialog import ExportProgressDialog
from app.dialogs.ffmpeg_install_progress_dialog import FFmpegInstallProgressDialog
from app.ffmpeg.managed_installer import ManagedFFmpegInstallation
from app.services.autosave_service import RecoverySnapshot
from app.services.project_service import ProjectService
from app.services.app_settings_service import AppSettings
from app.services.update_service import ReleaseInfo
from app.services.theme_service import Theme
from app.services.playlist_service import AudioImportCandidate, PlaylistService
from app.services.lrc_draft_service import LrcDraftService
from app.ui.main_window import MainWindow
from app.utils.i18n import Language
from app.preview.canvas_snapshot import CanvasSnapshot
from app.renderer.ffmpeg_renderer import FFmpegRenderer, RenderFrame
from app.presets.preset_service import PresetService


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

    def test_new_action_reuses_startup_project_chooser(self) -> None:
        startup_dialog = MagicMock()
        startup_dialog.action = StartupDialog.NEW_PROJECT
        startup_dialog.project_path = None
        startup_dialog.DialogCode = QDialog.DialogCode
        startup_dialog.exec.side_effect = (
            QDialog.DialogCode.Accepted,
            QDialog.DialogCode.Rejected,
        )
        with (
            patch(
                "app.ui.main_window.StartupDialog", return_value=startup_dialog,
            ) as startup_class,
            patch.object(
                NewProjectDialog, "exec", return_value=QDialog.DialogCode.Rejected,
            ) as creation_exec,
        ):
            startup_class.NEW_PROJECT = StartupDialog.NEW_PROJECT
            self.window.new_action.trigger()

        self.assertEqual(startup_class.call_count, 2)
        creation_exec.assert_called_once()

    def test_damaged_project_shows_detailed_report_and_preserves_workspace(self) -> None:
        marker = Source(SourceType.TEXT, "KEEP_CURRENT_PROJECT")
        self.window.store.add(marker)
        self.window.store.select(marker.id)
        self.window._project_dirty = True
        original_path = Path("current-project.pvsproj").resolve()
        self.window.current_project_path = original_path
        before = self.window._project_document().to_dict()

        with TemporaryDirectory() as directory:
            damaged = Path(directory) / "damaged.project.json"
            damaged.write_text('{"version": 2, "sources": [', encoding="utf-8")
            with patch.object(
                ProjectCrashReportDialog, "exec",
                return_value=QDialog.DialogCode.Rejected,
            ) as report:
                loaded = self.window._load_project_path(damaged)

        self.assertFalse(loaded)
        report.assert_called_once()
        self.assertEqual(self.window._project_document().to_dict(), before)
        self.assertEqual(self.window.current_project_path, original_path)
        self.assertTrue(self.window._project_dirty)
        self.assertEqual(self.window.store.selected_ids, (marker.id,))
        self.assertTrue(self.window.isEnabled())

    def test_project_apply_failure_rolls_back_partial_workspace_changes(self) -> None:
        marker = Source(SourceType.TEXT, "ROLLBACK_MARKER")
        self.window.store.add(marker)
        self.window.store.select(marker.id)
        self.window._project_dirty = True
        before = self.window._project_document().to_dict()
        incoming = ProjectDocument.from_dict(before)
        incoming.settings.title = "Incoming project"
        incoming.sources = [Source(SourceType.TEXT, "INCOMING_SOURCE")]

        original_replace = self.window.playlist_service.replace
        calls = 0

        def fail_once(tracks: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("simulated apply failure")
            original_replace(tracks)  # type: ignore[arg-type]

        with (
            patch.object(ProjectService, "load", return_value=incoming),
            patch.object(self.window, "_resolve_project_media", return_value=True),
            patch.object(self.window.playlist_service, "replace", side_effect=fail_once),
            patch.object(
                ProjectCrashReportDialog, "exec",
                return_value=QDialog.DialogCode.Rejected,
            ) as report,
        ):
            loaded = self.window._load_project_path(Path("broken-apply.pvsproj"))

        self.assertFalse(loaded)
        report.assert_called_once()
        self.assertEqual(self.window._project_document().to_dict(), before)
        self.assertTrue(self.window._project_dirty)
        self.assertEqual(self.window.store.selected_ids, (marker.id,))

    def test_loading_project_from_different_app_version_warns_and_continues(self) -> None:
        incoming = ProjectDocument(app_version="9.8.7")
        with (
            patch.object(ProjectService, "load", return_value=incoming),
            patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Ok) as warning,
        ):
            loaded = self.window._load_project_path(Path("different-version.pvsproj"))

        self.assertTrue(loaded)
        warning.assert_called_once()
        message = warning.call_args.args[2]
        self.assertIn("9.8.7", message)
        self.assertIn(__version__, message)

    def test_loading_legacy_or_same_version_project_does_not_warn(self) -> None:
        for app_version in ("", __version__):
            with self.subTest(app_version=app_version), patch.object(
                ProjectService, "load",
                return_value=ProjectDocument(app_version=app_version),
            ), patch.object(QMessageBox, "warning") as warning:
                self.assertTrue(
                    self.window._load_project_path(Path("compatible-version.pvsproj"))
                )
                warning.assert_not_called()

    def test_startup_offers_recovery_before_project_choice(self) -> None:
        with patch.object(self.window, "_offer_recovery", return_value=True) as offer:
            with patch("app.ui.main_window.StartupDialog") as startup_dialog:
                self.assertTrue(self.window.show_startup_dialog())
        offer.assert_called_once_with()
        startup_dialog.assert_not_called()

    def test_help_menu_exposes_localized_manual_update_check(self) -> None:
        original_language = self.window.translator.language
        try:
            self.assertIn(self.window.check_updates_action, self.window.help_menu.actions())
            self.window.translator.set_language(Language.KOREAN)
            self.assertEqual(self.window.check_updates_action.text(), "업데이트 확인")
            self.window.translator.set_language(Language.ENGLISH)
            self.assertEqual(self.window.check_updates_action.text(), "Check for updates")
        finally:
            self.window.translator.set_language(original_language)

    def test_dismissed_release_is_skipped_only_during_automatic_checks(self) -> None:
        release = ReleaseInfo(
            version="9.0.0",
            tag_name="v9.0.0",
            name="Playlist Canvas 9.0.0",
            body="Release notes",
            published_at="2026-08-12T00:00:00Z",
            html_url="https://github.com/tharu8813/Playlist-Canvas/releases/tag/v9.0.0",
        )
        self.window._update_check_manual = False
        with patch("app.ui.main_window.QSettings") as settings_type:
            settings_type.return_value.value.return_value = release.tag_name
            with patch("app.ui.main_window.UpdateAvailableDialog") as dialog:
                self.window._update_release_found(release)
            dialog.assert_not_called()

    def test_newer_installed_version_shows_warning_without_update(self) -> None:
        release = ReleaseInfo(
            version="1.0.0",
            tag_name="v1.0.0",
            name="Playlist Canvas 1.0.0",
            body="Older public release",
            published_at="2026-08-12T00:00:00Z",
            html_url="https://github.com/tharu8813/Playlist-Canvas/releases/tag/v1.0.0",
        )
        self.window._update_check_manual = False
        with patch.object(QMessageBox, "warning") as warning, patch(
            "app.ui.main_window.UpdateAvailableDialog"
        ) as dialog:
            self.window._update_release_found(release)
        warning.assert_called_once()
        self.assertIn("1.0.1", warning.call_args.args[2])
        dialog.assert_not_called()

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
            self.assertIn(self.window.lrc_generator_action, self.window.tools_menu.actions())
            self.assertEqual(self.window.lrc_generator_action.text(), "LRC 파일 생성기")
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
                self.window.lrc_generator_action.text(), "LRC File Generator"
            )
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

    def test_lrc_generator_registers_saved_files_as_project_content(self) -> None:
        saved = Path("generated-test-lyrics.lrc").resolve()
        with (
            patch("app.ui.main_window.LrcGeneratorDialog") as dialog_type,
            patch.object(
                self.window.project_content_service, "add_paths", return_value=1,
            ) as add_paths,
        ):
            dialog = dialog_type.return_value
            dialog.saved_paths = [saved]
            dialog.add_saved_files_to_project = True
            self.window._show_lrc_generator()
        dialog.exec.assert_called_once_with()
        add_paths.assert_called_once_with([saved])
        self.assertEqual(
            dialog_type.call_args.kwargs["playlist_tracks"],
            self.window.playlist_service.tracks,
        )

    def test_missing_audio_metadata_can_be_edited_before_project_import(self) -> None:
        track = PlaylistTrack(
            str(Path("untagged-song.mp3").resolve()),
            "untagged-song",
            artist="Unknown Artist",
            album="Unknown Album",
            duration_seconds=90.0,
        )
        candidate = AudioImportCandidate(track, ("title", "artist", "album"))
        dialog = AudioMetadataDialog([candidate], self.window.translator, self.window)
        try:
            self.assertEqual(dialog.table.item(0, 1).text(), "untagged-song")
            self.assertEqual(dialog.table.item(0, 2).text(), "")
            self.assertEqual(dialog.table.item(0, 3).text(), "")
            dialog.table.item(0, 1).setText("Project title")
            dialog.table.item(0, 2).setText("Project artist")
            dialog.table.item(0, 3).setText("Project album")
            edited = dialog.selected_tracks[0]
            self.assertEqual(edited.title, "Project title")
            self.assertEqual(edited.artist, "Project artist")
            self.assertEqual(edited.album, "Project album")
            self.assertEqual(track.title, "untagged-song")
        finally:
            dialog.close()

    def test_audio_import_prompts_only_for_missing_metadata_and_honors_cancel(self) -> None:
        original_count = len(self.window.playlist_service.tracks)
        track = PlaylistTrack(
            str(Path("missing-tags.mp3").resolve()), "missing-tags",
        )
        candidate = AudioImportCandidate(track, ("artist", "album"))
        edited = PlaylistTrack(
            track.file_path, "missing-tags", artist="Edited artist",
            album="Edited album",
        )
        with (
            patch.object(
                self.window.playlist_service, "inspect_files",
                return_value=[candidate],
            ),
            patch("app.ui.main_window.AudioMetadataDialog") as dialog_type,
        ):
            metadata_dialog = dialog_type.return_value
            metadata_dialog.exec.return_value = QDialog.DialogCode.Rejected
            added, accepted = self.window._import_audio_files([track.file_path])
            self.assertEqual((added, accepted), (0, []))
            self.assertEqual(len(self.window.playlist_service.tracks), original_count)

            metadata_dialog.exec.return_value = QDialog.DialogCode.Accepted
            metadata_dialog.selected_tracks = [edited]
            added, accepted = self.window._import_audio_files([track.file_path])

        self.assertEqual(added, 1)
        self.assertEqual(accepted, [Path(track.file_path)])
        imported = self.window.playlist_service.tracks[-1]
        self.assertEqual(imported.artist, "Edited artist")
        self.assertEqual(imported.album, "Edited album")

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

    def test_explicit_theme_updates_application_palette_menus_and_open_dialogs(self) -> None:
        original = self.window.theme_service.preference

        class ThemeAwareDialog(QDialog):
            def __init__(self, parent: QWidget) -> None:
                super().__init__(parent)
                self.refresh_count = 0

            def refresh_theme(self) -> None:
                self.refresh_count += 1

        dialog = ThemeAwareDialog(self.window)
        dialog.show()
        try:
            self.window.theme_service.set_preference(Theme.DARK)
            self.application.processEvents()
            self.assertLess(
                self.application.palette().window().color().lightness(), 128
            )
            self.assertIn("QMenu::item:selected", self.application.styleSheet())
            self.assertLess(dialog.palette().window().color().lightness(), 128)

            previous_refreshes = dialog.refresh_count
            self.window.theme_service.set_preference(Theme.LIGHT)
            self.application.processEvents()
            self.assertGreater(
                self.application.palette().window().color().lightness(), 128
            )
            self.assertGreater(dialog.palette().window().color().lightness(), 128)
            self.assertGreater(dialog.refresh_count, previous_refreshes)
        finally:
            dialog.close()
            self.window.theme_service.set_preference(original)
            self.application.processEvents()

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

    def test_spin_boxes_have_separate_vertical_step_button_hit_areas(self) -> None:
        spin_boxes = (
            self.window.inspector.z_spin,
            self.window.inspector.opacity_spin,
        )
        for spin in spin_boxes:
            with self.subTest(spin=spin.objectName() or type(spin).__name__):
                spin.setValue(min(
                    spin.maximum() - spin.singleStep(),
                    spin.minimum() + spin.singleStep() * 2,
                ))
                self.application.processEvents()
                option = QStyleOptionSpinBox()
                spin.initStyleOption(option)
                style = spin.style()
                up_rect = style.subControlRect(
                    QStyle.ComplexControl.CC_SpinBox, option,
                    QStyle.SubControl.SC_SpinBoxUp, spin,
                )
                down_rect = style.subControlRect(
                    QStyle.ComplexControl.CC_SpinBox, option,
                    QStyle.SubControl.SC_SpinBoxDown, spin,
                )
                edit_rect = style.subControlRect(
                    QStyle.ComplexControl.CC_SpinBox, option,
                    QStyle.SubControl.SC_SpinBoxEditField, spin,
                )
                self.assertGreater(up_rect.height(), 0)
                self.assertGreater(down_rect.height(), 0)
                self.assertLessEqual(up_rect.bottom(), down_rect.top())
                self.assertFalse(up_rect.intersects(edit_rect))
                self.assertFalse(down_rect.intersects(edit_rect))

                before = spin.value()
                QTest.mouseClick(
                    spin, Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier, up_rect.center(),
                )
                self.assertGreater(spin.value(), before)
                raised = spin.value()
                QTest.mouseClick(
                    spin, Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier, down_rect.center(),
                )
                self.assertLess(spin.value(), raised)

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

    def test_fill_color_alpha_can_make_only_the_background_transparent(self) -> None:
        self.window.translator.set_language(Language.ENGLISH)
        source = Source(
            SourceType.TEXT, "Transparent background", text="Visible text",
            fill_color="#334455", outline_color="#FFFFFF", opacity=0.7,
        )
        self.window.store.add(source)
        self.application.processEvents()

        transparent = QColor("#334455")
        transparent.setAlpha(0)
        with patch.object(QColorDialog, "getColor", return_value=transparent) as picker:
            self.window.inspector._choose_color(
                "fill_color", self.window.inspector.fill_color_button,
            )

        self.assertEqual(source.fill_color, "#00334455")
        self.assertEqual(source.opacity, 0.7)
        self.assertIn(
            QColorDialog.ColorDialogOption.ShowAlphaChannel,
            picker.call_args.args,
        )
        self.assertEqual(
            self.window.inspector.fill_color_button.text(), "Transparent"
        )

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

    def test_inspector_uses_responsive_forms_without_horizontal_scrolling(self) -> None:
        inspector = self.window.inspector
        source = Source(SourceType.LYRICS, "Responsive inspector")
        self.window.store.add(source)
        self.window.store.select(source.id)
        inspector.resize(inspector.minimumWidth(), 520)
        self.application.processEvents()

        self.assertGreaterEqual(inspector.minimumWidth(), 290)
        self.assertEqual(
            inspector.horizontalScrollBarPolicy(),
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        self.assertEqual(inspector.horizontalScrollBar().maximum(), 0)
        forms = inspector._content.findChildren(QFormLayout)
        self.assertTrue(forms)
        for form in forms:
            self.assertEqual(
                form.rowWrapPolicy(), QFormLayout.RowWrapPolicy.WrapLongRows,
            )
            self.assertEqual(
                form.fieldGrowthPolicy(),
                QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow,
            )

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
            animation_in_duration=0.1,
            animation_out_duration=0.1,
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

    def test_preset_change_warns_before_replacing_canvas(self) -> None:
        original_ids = [source.id for source in self.window.store.sources()]
        preset = PresetService.all()[0]
        with (
            patch.object(
                DesignPresetDialog, "exec",
                return_value=QDialog.DialogCode.Accepted,
            ),
            patch.object(
                DesignPresetDialog, "selected_preset",
                new_callable=lambda: property(lambda _dialog: preset),
            ),
            patch.object(
                QMessageBox, "warning",
                return_value=QMessageBox.StandardButton.Cancel,
            ) as warning,
        ):
            self.window._choose_preset()
        warning.assert_called_once()
        self.assertEqual(
            [source.id for source in self.window.store.sources()], original_ids,
        )

        with (
            patch.object(
                DesignPresetDialog, "exec",
                return_value=QDialog.DialogCode.Accepted,
            ),
            patch.object(
                DesignPresetDialog, "selected_preset",
                new_callable=lambda: property(lambda _dialog: preset),
            ),
            patch.object(
                QMessageBox, "warning",
                return_value=QMessageBox.StandardButton.Yes,
            ),
        ):
            self.window._choose_preset()
        self.assertNotEqual(
            [source.id for source in self.window.store.sources()], original_ids,
        )

    def test_preset_preserves_custom_portrait_ratio_and_adapts_layout(self) -> None:
        self.window.canvas.scene_model.set_artboard_size(800, 1900)
        preset = PresetService.all()[0]
        self.window._apply_preset(preset)

        artboard = self.window.canvas.scene_model.artboard_rect
        self.assertEqual((artboard.width(), artboard.height()), (800, 1900))
        sources = self.window.store.sources()
        background = next(
            source for source in sources
            if source.source_type is SourceType.BACKGROUND
        )
        self.assertEqual(
            (background.x, background.y, background.width, background.height),
            (0.0, 0.0, 800, 1900),
        )
        non_background = [
            source for source in sources
            if source.source_type is not SourceType.BACKGROUND
        ]
        self.assertGreater(
            max(source.y for source in non_background)
            - min(source.y for source in non_background),
            900,
        )
        for source in non_background:
            self.assertGreaterEqual(source.x, 0)
            self.assertGreaterEqual(source.y, 0)
            self.assertLessEqual(source.x + source.width, 800.01)
            self.assertLessEqual(source.y + source.height, 1900.01)

    def test_project_settings_can_change_ratio_and_scale_existing_sources(self) -> None:
        self.window.canvas.scene_model.set_artboard_size(800, 1900)
        self.window.store.replace([
            Source(SourceType.BACKGROUND, "Background", width=800, height=1900),
            Source(SourceType.TEXT, "Marker", x=80, y=190, width=240, height=100),
        ])
        marker = next(
            source for source in self.window.store.sources() if source.name == "Marker"
        )
        self.window.store.select(marker.id)

        self.window._resize_project_canvas((800, 1900), (1900, 800), True)

        artboard = self.window.canvas.scene_model.artboard_rect
        self.assertEqual((artboard.width(), artboard.height()), (1900, 800))
        background = next(
            source for source in self.window.store.sources()
            if source.source_type is SourceType.BACKGROUND
        )
        self.assertEqual((background.width, background.height), (1900, 800))
        resized_marker = self.window.store.get(marker.id)
        self.assertIsNotNone(resized_marker)
        assert resized_marker is not None
        self.assertAlmostEqual(resized_marker.width, 240 * (800 / 1900))
        self.assertAlmostEqual(
            resized_marker.x + resized_marker.width / 2,
            (80 + 120) * (1900 / 800),
        )
        self.assertEqual(self.window.store.selected_ids, (marker.id,))

    def test_project_settings_dialog_supports_custom_canvas_ratio(self) -> None:
        dialog = ProjectSettingsDialog(
            self.window.project_settings,
            self.window.translator,
            QPixmap(),
            canvas_size=(800, 1900),
        )
        try:
            self.assertEqual(dialog.selected_canvas_size, (800, 1900))
            dialog.canvas_preset_combo.setCurrentIndex(
                dialog.canvas_preset_combo.count() - 1
            )
            dialog.canvas_width_spin.setValue(840)
            dialog.canvas_height_spin.setValue(1995)
            self.assertEqual(dialog.selected_canvas_size, (840, 1995))
            self.assertIn("8:19", dialog.canvas_summary.text())
            self.assertTrue(dialog.scale_canvas_content)
        finally:
            dialog.close()

    def test_export_resolutions_follow_project_canvas_ratio(self) -> None:
        dialog = ExportSettingsDialog(
            AppSettings(), 1, 60.0, self.window.translator,
            Path("portrait-export.mp4"), canvas_size=(800, 1900),
        )
        try:
            width, height, _base_name = dialog.resolution_combo.currentData()
            self.assertLess(abs(width / height - 800 / 1900), 0.001)
            self.assertLess(width, height)
            render_settings = dialog.app_settings.render_settings()
            self.assertEqual(
                (render_settings.output_width, render_settings.output_height),
                (width, height),
            )
            self.assertIn("프로젝트 비율", dialog.resolution_combo.currentText())
        finally:
            dialog.close()

    def test_4k_export_selection_reaches_ffmpeg_dimensions(self) -> None:
        dialog = ExportSettingsDialog(
            AppSettings(), 1, 60.0, self.window.translator,
            Path("4k-export.mp4"), canvas_size=(1280, 720),
        )
        try:
            four_k_index = next(
                index for index in range(dialog.resolution_combo.count())
                if "4K" in str(dialog.resolution_combo.itemData(index)[2])
            )
            dialog.resolution_combo.setCurrentIndex(four_k_index)
            render_settings = dialog.app_settings.render_settings()
            self.assertEqual(
                (render_settings.output_width, render_settings.output_height),
                (3840, 2160),
            )
            scaling_filter = FFmpegRenderer._output_scaling_filter(
                render_settings.fps,
                render_settings.output_width,
                render_settings.output_height,
            )
            self.assertIn("scale=3840:2160", scaling_filter)
            self.assertIn("pad=3840:2160", scaling_filter)
        finally:
            dialog.close()

    def test_new_project_can_start_from_scaled_design_preset(self) -> None:
        preset = PresetService.all()[0]
        with (
            patch.object(
                NewProjectDialog, "exec",
                return_value=QDialog.DialogCode.Accepted,
            ),
            patch.object(
                NewProjectDialog, "canvas_size",
                new_callable=lambda: property(lambda _dialog: (720, 1280)),
            ),
            patch.object(
                NewProjectDialog, "selected_design_preset",
                new_callable=lambda: property(lambda _dialog: preset),
            ),
        ):
            self.assertTrue(self.window._new_project())

        sources = self.window.store.sources()
        self.assertEqual(len(sources), len(preset.builder()))
        background = next(
            source for source in sources
            if source.source_type is SourceType.BACKGROUND
        )
        self.assertEqual(
            (background.x, background.y, background.width, background.height),
            (0.0, 0.0, 720, 1280),
        )
        for source in sources:
            self.assertGreaterEqual(source.x, 0.0)
            self.assertGreaterEqual(source.y, 0.0)
            self.assertLessEqual(source.x + source.width, 720.001)
            self.assertLessEqual(source.y + source.height, 1280.001)

    def test_new_project_dialog_supports_presets_and_custom_size(self) -> None:
        dialog = NewProjectDialog(self.window.translator, self.window)
        try:
            self.assertEqual(dialog.canvas_size, (1280, 720))
            self.assertEqual(dialog.preset_combo.currentText(), "16:9")
            self.assertNotIn("1280", dialog.preset_combo.currentText())
            self.assertTrue(dialog.width_spin.isHidden())
            dialog.preset_combo.setCurrentIndex(1)
            self.assertEqual(dialog.canvas_size, (720, 1280))
            self.assertFalse(dialog.width_spin.isEnabled())
            dialog.preset_combo.setCurrentIndex(dialog.preset_combo.count() - 1)
            self.assertFalse(dialog.width_spin.isHidden())
            dialog.width_spin.setValue(1000)
            dialog.height_spin.setValue(1250)
            self.assertTrue(dialog.width_spin.isEnabled())
            self.assertEqual(dialog.canvas_size, (1000, 1250))
            self.assertIn("4:5", dialog.summary.text())
            self.assertEqual(dialog.selected_design_preset, None)
            dialog.design_preset_combo.setCurrentIndex(1)
            self.assertEqual(
                dialog.selected_design_preset.identifier,
                PresetService.all()[0].identifier,
            )
            self.assertTrue(dialog.design_description.text())
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

    def test_track_lyrics_dialog_exports_registered_lyrics_as_lrc(self) -> None:
        track = PlaylistTrack(
            "track.wav", "Export Track", artist="Artist", duration_seconds=20.0,
            lyrics_path="captions.vtt",
            lyrics=[{
                "start": 5.0, "end": 8.0,
                "text": "First visual line\nSecond visual line",
            }],
            lyrics_timing_offset_seconds=1.0,
        )
        dialog = TrackDetailsDialog(track, self.window.translator, self.window)
        try:
            self.assertTrue(dialog.export_lrc_button.isEnabled())
            dialog.timing_offset_spin.setValue(2.0)
            with TemporaryDirectory(prefix="track-lyrics-lrc-export-") as raw_directory:
                output = Path(raw_directory) / "converted.lrc"
                with (
                    patch.object(
                        QFileDialog, "getSaveFileName",
                        return_value=(str(output), "LRC lyrics (*.lrc)"),
                    ),
                    patch.object(QMessageBox, "information") as information,
                ):
                    dialog._export_current_lyrics_as_lrc()
                rendered = output.read_text(encoding="utf-8")
            self.assertIn("[ti:Export Track]", rendered)
            self.assertIn("[ar:Artist]", rendered)
            self.assertIn(r"[00:03.00]First visual line\nSecond visual line", rendered)
            self.assertNotIn("First visual line\nSecond visual line", rendered)
            information.assert_called_once()
            self.assertEqual(track.lyrics_timing_offset_seconds, 1.0)
        finally:
            dialog.close()

    def test_lrc_generator_loads_existing_cues_and_preserves_timing_for_text_edits(self) -> None:
        audio_path = Path("generator-existing-song.wav").resolve()
        with patch(
            "app.dialogs.lrc_generator_dialog.Path.is_file", return_value=True,
        ):
            dialog = LrcGeneratorDialog(
                [],
                self.window.translator,
                self.window,
                initial_audio_path=str(audio_path),
                initial_cues=[
                    {"start": 1.25, "end": 3.0, "text": "First\nSecond"},
                    {"start": 4.5, "end": 7.0, "text": "Next"},
                ],
                initial_title="Loaded title",
                initial_artist="Loaded artist",
            )
            try:
                self.assertEqual(dialog.audio_path, str(audio_path))
                self.assertEqual(dialog.lines, ["First\nSecond", "Next"])
                self.assertEqual(dialog.timestamps, [1.25, 4.5])
                self.assertEqual(dialog.input_mode_combo.currentData(), "multiline")
                self.assertEqual(dialog.title_edit.text(), "Loaded title")
                self.assertEqual(dialog.artist_edit.text(), "Loaded artist")

                dialog.lyrics_editor.setPlainText("Edited first\nSecond\n\nEdited next")
                dialog._prepare_lines()
                self.assertEqual(dialog.lines, ["Edited first\nSecond", "Edited next"])
                self.assertEqual(dialog.timestamps, [1.25, 4.5])
            finally:
                dialog.done(QDialog.DialogCode.Rejected)
                self.application.processEvents()

    def test_lrc_generator_uses_four_step_wizard_navigation(self) -> None:
        audio_path = Path("wizard-song.wav").resolve()
        with patch(
            "app.dialogs.lrc_generator_dialog.Path.is_file", return_value=True,
        ):
            dialog = LrcGeneratorDialog(
                [],
                self.window.translator,
                self.window,
                initial_audio_path=str(audio_path),
                initial_cues=[
                    {"start": 1.0, "end": 3.0, "text": "First"},
                    {"start": 4.0, "end": 7.0, "text": "Second"},
                ],
            )
            try:
                self.assertEqual(dialog.pages.count(), 4)
                self.assertEqual(dialog.pages.currentIndex(), 0)
                self.assertTrue(dialog.back_button.isHidden())

                dialog._next_step()
                self.assertEqual(dialog.pages.currentIndex(), 1)
                dialog._next_step()
                self.assertEqual(dialog.pages.currentIndex(), 2)
                self.assertEqual(dialog.timestamps, [1.0, 4.0])
                dialog._next_step()
                self.assertEqual(dialog.pages.currentIndex(), 3)
                self.assertIn("[00:01.00]First", dialog.review_text.toPlainText())
                self.assertTrue(dialog.next_button.isHidden())
                self.assertFalse(dialog.finish_button.isHidden())

                dialog._previous_step()
                self.assertEqual(dialog.pages.currentIndex(), 2)
            finally:
                dialog.close()

    def test_lrc_generator_wizard_blocks_progress_without_required_input(self) -> None:
        dialog = LrcGeneratorDialog([], self.window.translator, self.window)
        try:
            dialog._next_step()
            self.assertEqual(dialog.pages.currentIndex(), 0)
            self.assertTrue(dialog.status_label.property("error"))
        finally:
            dialog.close()

    def test_lrc_generator_track_edit_mode_skips_audio_and_file_save_controls(self) -> None:
        with TemporaryDirectory() as directory:
            audio_path = Path(directory) / "track-edit.wav"
            audio_path.write_bytes(b"test")
            candidate = MagicMock()
            candidate.track.title = "Track edit"
            candidate.track.artist = "Artist"
            with patch.object(PlaylistService, "inspect_files", return_value=[candidate]):
                dialog = LrcGeneratorDialog(
                    [], self.window.translator, self.window,
                    track_edit_mode=True,
                    initial_audio_path=str(audio_path),
                    initial_cues=[
                        {"start": 1.0, "end": 3.0, "text": "First"},
                        {"start": 4.0, "end": 7.0, "text": "Second"},
                    ],
                )
            try:
                self.assertEqual(dialog.pages.currentIndex(), 1)
                self.assertEqual(dialog.step_label.text(), "1 / 3 단계")
                self.assertTrue(dialog.back_button.isHidden())

                dialog._next_step()
                self.assertEqual(dialog.pages.currentIndex(), 2)
                dialog._next_step()
                self.assertEqual(dialog.pages.currentIndex(), 3)
                self.assertTrue(dialog.save_button.isHidden())
                self.assertTrue(dialog.add_to_project_check.isHidden())
                self.assertFalse(dialog.finish_button.isHidden())
                self.assertEqual(dialog.step_title.text(), "확인 및 적용")
                self.assertIn("현재 곡", dialog.review_help.text())
            finally:
                dialog.done(QDialog.DialogCode.Accepted)
                self.application.processEvents()

    def test_lrc_generator_offers_to_load_already_applied_track_lyrics(self) -> None:
        with TemporaryDirectory() as directory:
            audio_path = Path(directory) / "song-with-lyrics.wav"
            audio_path.write_bytes(b"test")
            track = PlaylistTrack(
                str(audio_path),
                "Applied title",
                artist="Applied artist",
                lyrics=[
                    {"start": 1.0, "end": 3.0, "text": "Applied first"},
                    {"start": 4.0, "end": 7.0, "text": "Applied second"},
                ],
            )
            candidate = MagicMock()
            candidate.track.title = track.title
            candidate.track.artist = track.artist
            with patch.object(PlaylistService, "inspect_files", return_value=[candidate]):
                dialog = LrcGeneratorDialog(
                    [], self.window.translator, self.window,
                    playlist_tracks=[track], initial_audio_path=str(audio_path),
                )
            try:
                with patch.object(
                    dialog, "_existing_lyrics_choice", return_value="load",
                ) as choice:
                    dialog._next_step()
                choice.assert_called_once()
                self.assertEqual(dialog.pages.currentIndex(), 2)
                self.assertEqual(dialog.lines, ["Applied first", "Applied second"])
                self.assertEqual(dialog.timestamps, [1.0, 4.0])
                self.assertEqual(
                    dialog.lyrics_editor.toPlainText(), "Applied first\nApplied second",
                )
            finally:
                dialog.done(QDialog.DialogCode.Rejected)
                self.application.processEvents()

    def test_lrc_generator_can_start_over_or_cancel_when_lyrics_exist(self) -> None:
        with TemporaryDirectory() as directory:
            audio_path = Path(directory) / "song-with-existing-lyrics.wav"
            audio_path.write_bytes(b"test")
            track = PlaylistTrack(
                str(audio_path), "Track",
                lyrics=[{"start": 1.0, "end": 3.0, "text": "Existing"}],
            )
            candidate = MagicMock()
            candidate.track.title = track.title
            candidate.track.artist = track.artist
            with patch.object(PlaylistService, "inspect_files", return_value=[candidate]):
                dialog = LrcGeneratorDialog(
                    [], self.window.translator, self.window,
                    playlist_tracks=[track], initial_audio_path=str(audio_path),
                )
            try:
                with patch.object(dialog, "_existing_lyrics_choice", return_value="cancel"):
                    dialog._next_step()
                self.assertEqual(dialog.pages.currentIndex(), 0)

                dialog.lyrics_editor.setPlainText("Old draft")
                dialog.lines = ["Old draft"]
                dialog.timestamps = [2.0]
                with patch.object(dialog, "_existing_lyrics_choice", return_value="new"):
                    dialog._next_step()
                self.assertEqual(dialog.pages.currentIndex(), 1)
                self.assertEqual(dialog.lyrics_editor.toPlainText(), "")
                self.assertEqual(dialog.lines, [])
                self.assertEqual(dialog.timestamps, [])
            finally:
                dialog.done(QDialog.DialogCode.Rejected)
                self.application.processEvents()

    def test_lrc_generator_audio_step_uses_combo_and_read_only_metadata(self) -> None:
        with TemporaryDirectory() as directory:
            audio_path = Path(directory) / "project-song.wav"
            audio_path.write_bytes(b"test")
            candidate = MagicMock()
            candidate.track.title = "Metadata title"
            candidate.track.artist = "Metadata artist"
            with patch.object(PlaylistService, "inspect_files", return_value=[candidate]):
                dialog = LrcGeneratorDialog(
                    [ProjectContent(str(audio_path), "audio", "Project song")],
                    self.window.translator,
                    self.window,
                )
            try:
                self.assertEqual(dialog.audio_path, str(audio_path.resolve()))
                self.assertEqual(dialog.title_edit.text(), "Metadata title")
                self.assertEqual(dialog.artist_edit.text(), "Metadata artist")
                self.assertTrue(dialog.title_edit.isReadOnly())
                self.assertTrue(dialog.artist_edit.isReadOnly())
                self.assertFalse(hasattr(dialog, "use_project_audio_button"))
                local_index = dialog.project_audio_combo.findData("__local__")
                self.assertGreaterEqual(local_index, 0)
                dialog.project_audio_combo.setCurrentIndex(local_index)
                self.assertFalse(dialog.local_audio_row.isHidden())
            finally:
                dialog.done(QDialog.DialogCode.Rejected)
                self.application.processEvents()

    def test_lrc_generator_preprocesses_bracket_lines_and_regex_on_next(self) -> None:
        dialog = LrcGeneratorDialog([], self.window.translator, self.window)
        try:
            dialog.pages.setCurrentIndex(1)
            dialog.lyrics_editor.setPlainText(
                "[Verse 1]\n01. First lyric\n\n02. Second lyric"
            )
            dialog.ignore_line_breaks_check.setChecked(True)
            dialog.ignore_bracketed_lines_check.setChecked(True)
            dialog.advanced_filter_check.setChecked(True)
            dialog.regex_filter_edit.setText(r"^\d+\.\s*")
            dialog._next_step()
            self.assertEqual(dialog.pages.currentIndex(), 2)
            self.assertEqual(dialog.lines, ["First lyric", "Second lyric"])
            self.assertEqual(dialog.timestamps, [None, None])
        finally:
            dialog.close()

    def test_lrc_generator_keeps_selection_separate_from_timing_cursor(self) -> None:
        dialog = LrcGeneratorDialog([], self.window.translator, self.window)
        try:
            dialog.lines = ["First", "Second", "Third"]
            dialog.timestamps = [1.0, 2.0, None]
            dialog.current_index = 0
            dialog._refresh_table(0)
            dialog._seek_to_row(0)
            self.assertEqual(dialog._selected_row(), 0)
            self.assertEqual(dialog.current_index, 1)
            self.assertTrue(dialog.timeline_table.item(0, 0).text().startswith("●"))
            self.assertTrue(dialog.timeline_table.item(1, 0).text().startswith("▶"))
            cursor_color = dialog.timeline_table.item(1, 0).background().color()
            self.assertEqual(cursor_color.red(), 255)
            self.assertLessEqual(cursor_color.alpha(), 32)
            selection_style = dialog.timeline_table.styleSheet()
            self.assertIn("background-color: transparent", selection_style)
            self.assertNotIn("#2563EB", selection_style)

            dialog._seek_to_row(0)
            self.assertEqual(dialog._selected_row(), 0)
            self.assertEqual(dialog.current_index, 0)
            self.assertTrue(dialog.timeline_table.item(0, 0).text().startswith("▶ ●"))
        finally:
            dialog.close()

    def test_lrc_generator_highlights_and_centers_playback_lyric(self) -> None:
        dialog = LrcGeneratorDialog([], self.window.translator, self.window)
        try:
            dialog.lines = ["First", "Second", "Third"]
            dialog.timestamps = [1.0, 4.0, 8.0]
            dialog.current_index = 2
            dialog.pages.setCurrentIndex(2)
            dialog._refresh_table()
            with patch.object(dialog.timeline_table, "scrollToItem") as scroll_to_item:
                dialog._position_changed(4_500)
            self.assertEqual(dialog._playback_highlight_row, 1)
            self.assertTrue(dialog.timeline_table.item(1, 0).text().startswith("♪"))
            self.assertEqual(
                dialog.timeline_table.item(1, 1).background().color(), QColor("#FFD54F"),
            )
            scroll_to_item.assert_not_called()

            dialog.audio_path = str(Path("preview-mode.wav").resolve())
            dialog._update_enabled()
            with patch.object(dialog.timeline_table, "scrollToItem") as preview_scroll:
                dialog.preview_mode_check.setChecked(True)
                dialog._position_changed(4_600)
            preview_scroll.assert_called_with(
                dialog.timeline_table.item(1, 0),
                QAbstractItemView.ScrollHint.PositionAtCenter,
            )
            self.assertFalse(dialog.record_button.isEnabled())
            self.assertFalse(dialog.edit_line_button.isEnabled())
            self.assertFalse(dialog.back_button.isEnabled())
            self.assertNotIn("▶", dialog.timeline_table.item(2, 0).text())
            self.assertIn("♪", dialog.timeline_table.item(1, 0).text())

            palette = dialog.timeline_table.palette()
            palette.setColor(QPalette.ColorRole.Text, QColor("#F1F5F9"))
            dialog.timeline_table.setPalette(palette)
            dialog.preview_mode_check.setChecked(False)
            dialog._apply_timeline_visuals()
            self.assertEqual(
                dialog.timeline_table.item(0, 1).foreground().color(),
                QColor("#F1F5F9"),
            )
            self.assertEqual(
                dialog.timeline_table.item(1, 1).foreground().color(),
                QColor("#202020"),
            )
        finally:
            dialog.close()

    def test_lrc_generator_uses_labeled_role_grouped_timing_tools(self) -> None:
        original_language = self.window.translator.language
        self.window.translator.set_language(Language.KOREAN)
        dialog = LrcGeneratorDialog([], self.window.translator, self.window)
        try:
            tools = (
                dialog.undo_button, dialog.redo_button, dialog.use_selected_button,
                dialog.nudge_back_button, dialog.nudge_forward_button,
                dialog.clear_time_button, dialog.reset_all_button,
                dialog.add_line_button, dialog.edit_line_button,
                dialog.delete_line_button,
            )
            for button in tools:
                self.assertFalse(button.icon().isNull())
                self.assertTrue(button.toolTip())
                self.assertEqual(
                    button.toolButtonStyle(),
                    Qt.ToolButtonStyle.ToolButtonTextBesideIcon,
                )
                self.assertTrue(button.text())
                self.assertGreaterEqual(button.minimumHeight(), 36)
            self.assertEqual(dialog.history_tools_group.title(), "실행 이력")
            self.assertEqual(dialog.timing_tools_group.title(), "선택 시간 조정")
            self.assertEqual(dialog.lyric_tools_group.title(), "가사 편집")
            self.assertFalse(
                dialog.timing_tools_group.isAncestorOf(dialog.use_selected_button)
            )
            self.assertEqual(dialog.record_button.objectName(), "primaryButton")
            self.assertEqual(dialog.play_button.objectName(), "transportPlayButton")
            self.assertFalse(dialog.forward_button.icon().isNull())
            self.assertGreaterEqual(dialog.position_slider.minimumHeight(), 28)
            self.assertEqual(dialog.preview_group.title(), "음악 재생")
        finally:
            dialog.close()
            self.window.translator.set_language(original_language)

    def test_lrc_generator_recording_always_advances_to_immediate_next_line(self) -> None:
        dialog = LrcGeneratorDialog([], self.window.translator, self.window)
        try:
            dialog.lines = ["First", "Already timed", "Third"]
            dialog.timestamps = [None, 5.0, None]
            dialog.current_index = 0
            dialog.audio_path = str(Path("record-next-line.wav").resolve())
            dialog._refresh_table(0)
            with (
                patch.object(dialog.media_player, "position", return_value=1_250),
                patch.object(dialog, "_autosave_after_change"),
                patch.object(dialog.timeline_table, "scrollToItem") as scroll_to_item,
            ):
                dialog._record_timestamp()
            self.assertEqual(dialog.timestamps, [1.25, 5.0, None])
            self.assertEqual(dialog.current_index, 1)
            self.assertTrue(dialog.timeline_table.item(1, 0).text().startswith("▶"))
            scroll_to_item.assert_called_with(
                dialog.timeline_table.item(1, 0),
                QAbstractItemView.ScrollHint.PositionAtCenter,
            )
        finally:
            dialog.close()

    def test_lrc_generator_accepts_value_equivalent_yes_for_untimed_lyrics(self) -> None:
        dialog = LrcGeneratorDialog([], self.window.translator, self.window)
        try:
            dialog.lines = ["Timed", "Untimed"]
            dialog.timestamps = [1.0, None]
            dialog.current_index = 1
            dialog.pages.setCurrentIndex(2)
            with patch.object(
                QMessageBox, "warning",
                # Packaged PySide may return a value-equivalent integer wrapper.
                return_value=QMessageBox.StandardButton.Yes.value,
            ):
                dialog._next_step()
            self.assertEqual(dialog.pages.currentIndex(), 3)
            self.assertIn("Timed", dialog.review_text.toPlainText())
            self.assertNotIn("Untimed", dialog.review_text.toPlainText())
        finally:
            dialog.close()

    def test_lrc_generator_add_edit_delete_and_back_change_policy(self) -> None:
        dialog = LrcGeneratorDialog([], self.window.translator, self.window)
        try:
            dialog.lines = ["First", "Second"]
            dialog.timestamps = [1.0, 2.0]
            dialog._timing_baseline_lines = list(dialog.lines)
            dialog._timing_baseline_timestamps = list(dialog.timestamps)
            dialog.pages.setCurrentIndex(2)
            dialog._refresh_table(0)

            with patch(
                "app.dialogs.lrc_generator_dialog.QInputDialog.getMultiLineText",
                return_value=("Edited", True),
            ):
                dialog._edit_selected_line()
            self.assertEqual(dialog.lines[0], "Edited")
            self.assertEqual(dialog.timestamps[0], 1.0)

            with patch(
                "app.dialogs.lrc_generator_dialog.QInputDialog.getMultiLineText",
                return_value=("Added", True),
            ):
                dialog._add_lyric_line()
            self.assertEqual(dialog.lines, ["Edited", "Added", "Second"])
            self.assertEqual(dialog.timestamps, [1.0, None, 2.0])

            with patch.object(dialog, "_confirm_timing_changes_before_back", return_value="discard"):
                dialog._previous_step()
            self.assertEqual(dialog.pages.currentIndex(), 1)
            self.assertEqual(dialog.lines, ["First", "Second"])
            self.assertEqual(dialog.timestamps, [1.0, 2.0])
            self.assertEqual(dialog.lyrics_editor.toPlainText(), "First\nSecond")

            dialog.pages.setCurrentIndex(2)
            dialog._refresh_table(0)
            with patch.object(
                QMessageBox, "warning", return_value=QMessageBox.StandardButton.Yes,
            ):
                dialog._delete_selected_line()
            self.assertEqual(dialog.lines, ["Second"])
            self.assertEqual(dialog.timestamps, [2.0])
        finally:
            dialog.close()

    def test_lrc_generator_autosaves_and_recovers_lyrics_timing_by_audio(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            audio_path = root / "autosave-song.wav"
            audio_path.write_bytes(b"test")
            candidate = MagicMock()
            candidate.track.title = "Recovered title"
            candidate.track.artist = "Recovered artist"

            first = LrcGeneratorDialog([], self.window.translator, self.window)
            first._draft_service = LrcDraftService(root)
            try:
                with patch.object(PlaylistService, "inspect_files", return_value=[candidate]):
                    first._set_audio(str(audio_path))
                first._mark_draft_dirty()
                first._save_draft_now()
                self.assertIsNone(first._draft_service.load(str(audio_path)))
                first.lyrics_editor.setPlainText("First\nSecond")
                self.assertTrue(first._prepare_lines())
                first.pages.setCurrentIndex(2)
                # Timing-step edits intentionally leave the raw input stale;
                # recovery must rebuild it from the authoritative line list.
                first.lines[0] = "Edited first"
                first.timestamps = [1.25, 4.5]
                first.current_index = 2
                first._autosave_after_change()
                draft = first._draft_service.load(str(audio_path))
                self.assertIsNotNone(draft)
            finally:
                # Simulate a crash: bypass the normal LRC dialog cleanup so
                # the on-disk recovery draft survives for the next launch.
                first._draft_timer.stop()
                first._draft_periodic_timer.stop()
                QDialog.done(first, QDialog.DialogCode.Rejected)
                self.application.processEvents()

            second = LrcGeneratorDialog([], self.window.translator, self.window)
            second._draft_service = LrcDraftService(root)
            try:
                with (
                    patch.object(PlaylistService, "inspect_files", return_value=[candidate]),
                    patch.object(
                        QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes,
                    ) as recovery_question,
                ):
                    second._set_audio(str(audio_path))
                recovery_question.assert_called_once()
                self.assertEqual(second.pages.currentIndex(), 2)
                self.assertEqual(second.lines, ["Edited first", "Second"])
                self.assertEqual(second.timestamps, [1.25, 4.5])
                self.assertEqual(second.current_index, 2)
                self.assertEqual(second.title_edit.text(), "Recovered title")
                recovery_status = second.autosave_label.text()
                self.assertTrue(
                    "복구" in recovery_status or "restored" in recovery_status.lower()
                )
                second._previous_step()
                self.assertEqual(second.pages.currentIndex(), 1)
                self.assertEqual(
                    second.lyrics_editor.toPlainText(), "Edited first\nSecond",
                )
                output = root / "recovered.lrc"
                with patch.object(
                    QFileDialog, "getSaveFileName",
                    return_value=(str(output), "LRC lyrics (*.lrc)"),
                ):
                    second._save_lrc()
                self.assertTrue(output.is_file())
                self.assertIsNone(second._draft_service.load(str(audio_path)))
            finally:
                second.done(QDialog.DialogCode.Rejected)
                self.application.processEvents()

    def test_lrc_generator_normal_close_warns_and_deletes_recovery_draft(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            audio_path = root / "normal-close.wav"
            audio_path.write_bytes(b"test")
            candidate = MagicMock()
            candidate.track.title = "Normal close"
            candidate.track.artist = "Artist"
            dialog = LrcGeneratorDialog([], self.window.translator, self.window)
            dialog._draft_service = LrcDraftService(root)
            with patch.object(PlaylistService, "inspect_files", return_value=[candidate]):
                dialog._set_audio(str(audio_path))
            dialog.pages.setCurrentIndex(1)
            dialog.lyrics_editor.setPlainText("Unsaved lyric")
            dialog._save_draft_now()
            self.assertIsNotNone(dialog._draft_service.load(str(audio_path)))

            with (
                patch.object(
                    QMessageBox, "warning",
                    return_value=QMessageBox.StandardButton.Cancel,
                ) as warning,
                patch.object(dialog, "done") as done,
            ):
                dialog.reject()
            warning.assert_called_once()
            done.assert_not_called()
            self.assertIsNotNone(dialog._draft_service.load(str(audio_path)))

            with patch.object(
                QMessageBox, "warning",
                return_value=QMessageBox.StandardButton.Yes.value,
            ):
                dialog.reject()
            self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
            self.assertIsNone(dialog._draft_service.load(str(audio_path)))

    def test_lrc_generator_declining_crash_recovery_deletes_the_draft(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            audio_path = root / "decline-recovery.wav"
            audio_path.write_bytes(b"test")
            service = LrcDraftService(root)
            service.save(str(audio_path), {
                "lyrics_text": "Recovered lyric",
                "lines": ["Recovered lyric"],
                "timestamps": [None],
                "current_index": 0,
            })
            candidate = MagicMock()
            candidate.track.title = "Recovery"
            candidate.track.artist = "Artist"
            dialog = LrcGeneratorDialog([], self.window.translator, self.window)
            dialog._draft_service = service
            try:
                with (
                    patch.object(PlaylistService, "inspect_files", return_value=[candidate]),
                    patch.object(
                        QMessageBox, "question",
                        return_value=QMessageBox.StandardButton.No,
                    ),
                ):
                    dialog._set_audio(str(audio_path))
                self.assertIsNone(service.load(str(audio_path)))
                deletion_status = dialog.autosave_label.text()
                self.assertTrue(
                    "삭제" in deletion_status or "deleted" in deletion_status.lower()
                )
            finally:
                dialog.done(QDialog.DialogCode.Rejected)

    def test_lrc_generator_does_not_autosave_empty_lyrics_and_clears_stale_draft(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            audio_path = root / "empty-lyrics.wav"
            audio_path.write_bytes(b"test")
            candidate = MagicMock()
            candidate.track.title = "Empty lyrics"
            candidate.track.artist = "Artist"
            dialog = LrcGeneratorDialog([], self.window.translator, self.window)
            dialog._draft_service = LrcDraftService(root)
            try:
                with patch.object(PlaylistService, "inspect_files", return_value=[candidate]):
                    dialog._set_audio(str(audio_path))
                dialog.pages.setCurrentIndex(1)
                dialog.lyrics_editor.setPlainText("Temporary lyric")
                dialog._save_draft_now()
                self.assertIsNotNone(dialog._draft_service.load(str(audio_path)))

                dialog.lines = ["Stale prepared lyric"]
                dialog.lyrics_editor.clear()
                dialog._save_draft_now()
                self.assertIsNone(dialog._draft_service.load(str(audio_path)))
                empty_status = dialog.autosave_label.text()
                self.assertTrue(
                    "가사 없음" in empty_status or "no lyrics" in empty_status.lower()
                )
            finally:
                dialog.done(QDialog.DialogCode.Rejected)
                self.application.processEvents()

    def test_lrc_generator_recovers_legacy_draft_with_empty_raw_input(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            audio_path = root / "legacy-draft.wav"
            audio_path.write_bytes(b"test")
            service = LrcDraftService(root)
            service.save(str(audio_path), {
                "lyrics_text": "",
                "lines": ["Legacy first", "Legacy second"],
                "timestamps": [2.0, None],
                "current_index": 1,
                "title": "Legacy title",
                "artist": "Legacy artist",
                "input_mode": "single",
            })
            candidate = MagicMock()
            candidate.track.title = "Audio title"
            candidate.track.artist = "Audio artist"
            dialog = LrcGeneratorDialog([], self.window.translator, self.window)
            dialog._draft_service = service
            try:
                with (
                    patch.object(PlaylistService, "inspect_files", return_value=[candidate]),
                    patch.object(
                        QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes,
                    ),
                ):
                    dialog._set_audio(str(audio_path))
                self.assertEqual(dialog.pages.currentIndex(), 2)
                self.assertEqual(dialog.lines, ["Legacy first", "Legacy second"])
                self.assertEqual(
                    dialog.lyrics_editor.toPlainText(), "Legacy first\nLegacy second",
                )
            finally:
                dialog.done(QDialog.DialogCode.Rejected)
                self.application.processEvents()

    def test_track_lyrics_dialog_round_trips_edits_from_lrc_generator(self) -> None:
        track = PlaylistTrack(
            "track.wav", "Track title", artist="Track artist",
            lyrics_path="original.lrc",
            lyrics=[{"start": 2.0, "end": 5.0, "text": "Original"}],
        )
        dialog = TrackDetailsDialog(track, self.window.translator, self.window)
        edited = [{"start": 3.0, "end": 8.0, "text": "Edited"}]
        saved = Path("edited-track.lrc").resolve()
        try:
            with patch(
                "app.dialogs.track_details_dialog.LrcGeneratorDialog",
            ) as generator_type:
                generator = generator_type.return_value
                generator.exec.return_value = QDialog.DialogCode.Accepted
                generator.timed_cues.return_value = edited
                generator.saved_paths = [saved]
                dialog._edit_in_lrc_generator()

            kwargs = generator_type.call_args.kwargs
            self.assertTrue(kwargs["track_edit_mode"])
            self.assertEqual(kwargs["initial_audio_path"], track.file_path)
            self.assertEqual(kwargs["initial_cues"], track.lyrics)
            self.assertEqual(kwargs["initial_title"], track.title)
            self.assertEqual(kwargs["initial_artist"], track.artist)
            self.assertEqual(dialog.selected_lyrics, edited)
            self.assertEqual(dialog.selected_lyrics_path, str(saved))
            self.assertEqual(track.lyrics[0]["text"], "Original")
        finally:
            dialog.close()

    def test_track_lyrics_dialog_shows_all_track_information_labels(self) -> None:
        original_language = self.window.translator.language
        self.window.translator.set_language(Language.KOREAN)
        track = PlaylistTrack(
            "C:/Music/long folder/song.m4a", "Visible title",
            artist="Visible artist", album="", duration_seconds=125,
        )
        dialog = TrackDetailsDialog(track, self.window.translator, self.window)
        try:
            self.assertEqual(
                [label.text() for label in dialog.info_name_labels],
                ["제목", "아티스트", "앨범", "파일", "재생 시간"],
            )
            self.assertEqual(
                [label.text() for label in dialog.info_labels],
                [
                    "Visible title", "Visible artist", "—",
                    "C:/Music/long folder/song.m4a", "02:05",
                ],
            )
            for label in (*dialog.info_name_labels, *dialog.info_labels):
                self.assertTrue(label.isVisible() or not dialog.isVisible())
            self.assertTrue(
                dialog.info_labels[3].textInteractionFlags()
                & Qt.TextInteractionFlag.TextSelectableByMouse
            )
            self.assertEqual(
                dialog.info_labels[3].toolTip(), "C:/Music/long folder/song.m4a"
            )
        finally:
            dialog.close()
            self.window.translator.set_language(original_language)

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
            duration_seconds=2.0, start_time_seconds=0.5,
        )
        self.window.playlist_service.replace([track])
        self.window.store.add(Source(
            SourceType.TEXT, "Animated", animation_in="fade",
            animation_out="fade", animation_duration=0.2, z_index=2.0,
        ))
        self.window.store.add(Source(
            SourceType.TIME, "Export clock", text="%current_time%", z_index=3.0,
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
        captured_states: list[dict[str, object]] = []

        def capture_phase(*_arguments: object, **state: object) -> QImage:
            captured_states.append(dict(state))
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
                patch.object(QMessageBox, "critical") as critical_message,
            ):
                self.window._export_video()

            critical_message.assert_not_called()
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
            entrance_states = [
                state for state in captured_states
                if state.get("animation_phase") == "in"
            ]
            exit_states = [
                state for state in captured_states
                if state.get("animation_phase") == "out"
            ]
            self.assertEqual(entrance_states[0]["animation_progress"], 0.0)
            self.assertEqual(entrance_states[0]["elapsed_seconds"], 0.0)
            self.assertEqual(exit_states[0]["animation_progress"], 0.0)
            self.assertAlmostEqual(float(exit_states[0]["elapsed_seconds"]), 1.8)
            stable_elapsed = [
                float(state["elapsed_seconds"])
                for state in captured_states
                if state.get("animation_phase") is None
                and "elapsed_seconds" in state
            ]
            self.assertTrue(any(0.99 < elapsed < 1.01 for elapsed in stable_elapsed))
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
