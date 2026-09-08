from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import os
import tempfile
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import (QEvent, QItemSelectionModel, QMimeData, QPoint, QPointF, QRect, QRectF,
                            QSettings, QSize, Qt, QTimer, QUrl)
from PySide6.QtGui import (QColor, QCloseEvent, QContextMenuEvent, QDropEvent, QImage,
                           QMouseEvent, QPalette, QPixmap, QWheelEvent)
from PySide6.QtTest import QTest
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QFileDialog, QFrame,
    QFormLayout, QGraphicsView, QListView, QMessageBox, QScrollArea, QSizePolicy, QStyle,
    QStyleOptionSpinBox,
    QWidget,
)

from app import __version__
from app.models.project import CanvasSettings, ProjectContent, ProjectDocument
from app.models.playlist import PlaylistTrack
from app.models.source import Source, SourceType
from app.canvas.source_item import SourceItem
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
from app.dialogs.text_editor_dialog import TextEditorDialog
from app.dialogs.video_source_dialog import VideoSourceDialog
from app.dialogs.export_progress_dialog import ExportEtaEstimator, ExportProgressDialog
from app.dialogs.export_complete_dialog import ExportCompleteDialog
from app.services.export_storage_service import (
    ExportStorageSnapshot,
    estimate_export_storage,
)
from app.dialogs.export_preview_dialog import (
    GPU_TEXTURE_SURFACE_AVAILABLE, ExportPreviewDialog, OverlayFrameWorker,
    VideoDurationProbeWorker,
)
from app.dialogs.ffmpeg_install_progress_dialog import FFmpegInstallProgressDialog
from app.widgets.source_template_button import (
    SourceTemplateButton,
    read_source_template_mime,
)
from app.ffmpeg.managed_installer import (
    FFmpegReleaseOption,
    ManagedFFmpegInstallation,
)
from app.services.autosave_service import RecoverySnapshot
from app.services.project_service import ProjectError, ProjectService
from app.services.app_settings_service import AppSettings
from app.services.update_service import ReleaseInfo
from app.services.theme_service import Theme
from app.services.playlist_service import AudioImportCandidate, PlaylistService
from app.services.lrc_draft_service import LrcDraftService
from app.ui.main_window import MainWindow
from app.utils.i18n import Language
from app.preview.canvas_snapshot import CanvasSnapshot
from app.preview.gpu_texture_surface import (
    GpuColorFilter, GpuPreviewLayer, GpuTexturePreviewSurface,
)
from app.preview.album_art import (
    create_cached_ambient_background, extract_track_cover,
)
from app.video.preview_proxy import PreviewProxyCache
from app.widgets.token_text_editor import TokenLineEdit, TokenPlainTextEdit
from app.renderer.ffmpeg_renderer import (
    EncoderUnavailableError,
    FFmpegRenderer,
    FFmpegNotFoundError,
    PreparedStaticOverlayLayer,
    PreparedVideoInput,
    RenderError,
    RenderFrame,
    RenderSettings,
    VisualizerOverlay,
    WORK_MODE_AUTO,
    WORK_MODE_MAX_SPEED,
    WORK_MODE_STABLE,
)
from app.services.video_encoder_service import (
    AUTO_VIDEO_ENCODER,
    CPU_H264_ENCODER,
    NVIDIA_H264_ENCODER,
    VideoEncoderAdvisor,
)
from app.renderer.static_video_stream import (
    DirectVideoEncodingProfile,
    StaticVideoStreamResult,
)
from app.presets.preset_service import PresetService


class MainWindowSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setApplicationName("Playlist Canvas Tests")
        cls.application.setOrganizationName("Playlist Canvas Tests")
        # Isolate user presets from the developer's real preset folder.
        cls._preset_dir = tempfile.mkdtemp(prefix="pc-presets-")
        cls._prev_preset_dir = os.environ.get("PLAYLIST_CANVAS_PRESET_DIR")
        os.environ["PLAYLIST_CANVAS_PRESET_DIR"] = cls._preset_dir

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._prev_preset_dir is None:
            os.environ.pop("PLAYLIST_CANVAS_PRESET_DIR", None)
        else:
            os.environ["PLAYLIST_CANVAS_PRESET_DIR"] = cls._prev_preset_dir

    def setUp(self) -> None:
        # Main-window UI assertions use the Korean baseline unless a test opts
        # into another language. Translator changes persist through QSettings,
        # so one English-specific test must not leak into every later test.
        settings = QSettings()
        self._original_language_setting = settings.value("language", None)
        settings.setValue("language", Language.KOREAN.value)
        self.window = MainWindow()
        self.application.processEvents()

    def tearDown(self) -> None:
        self.window._project_dirty = False
        self.window.close()
        settings = QSettings()
        if self._original_language_setting is None:
            settings.remove("language")
        else:
            settings.setValue("language", self._original_language_setting)
        self.application.processEvents()

    def test_status_bar_activity_progress_tracks_multiple_operations(self) -> None:
        progress = self.window.activity_progress
        self.assertTrue(progress.isHidden())

        progress.begin("save", "프로젝트 저장", detail="example.pvsproj")
        self.assertFalse(progress.isHidden())
        self.assertEqual(progress.progress_bar.minimum(), 0)
        self.assertEqual(progress.progress_bar.maximum(), 0)
        self.assertIn("프로젝트 저장", progress.toolTip())
        self.assertIn("example.pvsproj", progress.toolTip())

        progress.begin("update", "업데이트 다운로드", 0.42, "Setup 다운로드 중")
        self.assertEqual(progress.label.text(), "업데이트 다운로드")
        self.assertEqual(progress.progress_bar.value(), 420)
        self.assertIn("42%", progress.toolTip())
        self.assertIn("프로젝트 저장", progress.toolTip())

        progress.finish("update")
        self.assertEqual(progress.label.text(), "프로젝트 저장")
        progress.finish("save")
        self.assertTrue(progress.isHidden())

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
        self.assertNotIn("project_load", self.window.activity_progress.active_keys)

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
        self.assertNotIn("project_load", self.window.activity_progress.active_keys)

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
        self.assertIn(__version__, warning.call_args.args[2])
        dialog.assert_not_called()

    def test_manual_check_on_latest_shows_current_version_release_notes(self) -> None:
        release = ReleaseInfo(
            version=__version__,
            tag_name=__version__,
            name=f"Playlist Canvas {__version__}",
            body="## 이번 버전\n\n- 변경 사항 A\n",
            published_at="2026-09-04T00:00:00Z",
            html_url=(
                "https://github.com/tharu8813/Playlist-Canvas/releases/tag/"
                + __version__
            ),
        )
        self.window._update_check_manual = True
        with patch(
            "app.ui.main_window.UpdateAvailableDialog"
        ) as dialog:
            self.window._update_release_found(release)
        dialog.assert_called_once()
        self.assertTrue(dialog.call_args.kwargs.get("up_to_date"))

    def test_up_to_date_release_dialog_shows_rendered_notes_without_update_button(
        self,
    ) -> None:
        from app.dialogs.update_dialogs import UpdateAvailableDialog

        release = ReleaseInfo(
            version=__version__, tag_name=__version__,
            name=f"Playlist Canvas {__version__}",
            body="> [!NOTE]\n> 임시 공간이 필요합니다.\n\n## 변경\n\n- 항목 하나\n",
            published_at="2026-09-04T00:00:00Z",
            html_url=(
                "https://github.com/tharu8813/Playlist-Canvas/releases/tag/"
                + __version__
            ),
        )
        dialog = UpdateAvailableDialog(
            release, __version__, True, False, self.window, up_to_date=True,
        )
        try:
            self.assertNotIn(
                dialog.update_button,
                dialog.buttons.buttons(),
            )
            html = dialog.notes.toHtml()
            self.assertNotIn("[!NOTE]", html)
            self.assertIn("항목 하나", html)
        finally:
            dialog.close()

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

    def test_autosave_writes_recovery_on_a_background_worker(self) -> None:
        from app.services.autosave_service import AutosaveService

        original = self.window.autosave
        with TemporaryDirectory(prefix="pvs-autosave-worker-") as directory:
            self.window.autosave = AutosaveService(Path(directory))
            try:
                self.window.store.add(Source(SourceType.TEXT, "AUTOSAVE_MARKER"))
                self.window._history_ready = True
                self.window._project_dirty = True
                self.window._autosave_project()
                worker = self.window._autosave_worker
                self.assertIsNotNone(worker)
                self.assertIn("autosave", self.window.activity_progress.active_keys)
                self.assertTrue(worker.wait(5000))
                self.application.processEvents()
                self.assertIsNone(self.window._autosave_worker)
                self.assertNotIn(
                    "autosave", self.window.activity_progress.active_keys
                )
                recovery = self.window.autosave.latest_recovery()
                self.assertIsNotNone(recovery)
                self.assertIn(
                    "AUTOSAVE_MARKER",
                    [source.name for source in recovery.document.sources],
                )
            finally:
                if self.window._autosave_worker is not None:
                    self.window._autosave_worker.wait(5000)
                self.window.autosave = original

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

    def test_close_during_export_preparation_cancels_then_resumes_close(self) -> None:
        preparation_cancel = threading.Event()
        dialog = MagicMock()

        def request_cancel() -> bool:
            preparation_cancel.set()
            return True

        dialog.request_cancel.side_effect = request_cancel
        self.window._export_preparation_cancel = preparation_cancel
        self.window._export_dialog = dialog
        event = QCloseEvent()
        try:
            self.window.closeEvent(event)

            self.assertFalse(event.isAccepted())
            self.assertTrue(preparation_cancel.is_set())
            self.assertTrue(self.window._close_after_export_cancel)
            with patch.object(QTimer, "singleShot") as single_shot:
                self.window._resume_close_after_export_cancel()
            self.assertFalse(self.window._close_after_export_cancel)
            single_shot.assert_called_once()
            self.assertEqual(single_shot.call_args.args[0], 0)
        finally:
            self.window._export_preparation_cancel = None
            self.window._export_dialog = None
            self.window._close_after_export_cancel = False

    def test_close_during_render_requests_cancel_and_defers_shutdown(self) -> None:
        worker = MagicMock()
        worker.isRunning.return_value = True
        self.window._render_worker = worker
        self.window._export_dialog = None
        event = QCloseEvent()
        try:
            self.window.closeEvent(event)

            self.assertFalse(event.isAccepted())
            worker.cancel.assert_called_once()
            self.assertTrue(self.window._close_after_export_cancel)
        finally:
            self.window._render_worker = None
            self.window._close_after_export_cancel = False

    def test_close_while_export_is_already_cancelling_still_defers_shutdown(self) -> None:
        worker = MagicMock()
        worker.isRunning.return_value = True
        dialog = MagicMock()
        dialog.is_cancelling = True
        self.window._render_worker = worker
        self.window._export_dialog = dialog
        event = QCloseEvent()
        try:
            self.window.closeEvent(event)

            self.assertFalse(event.isAccepted())
            dialog.request_cancel.assert_not_called()
            self.assertTrue(self.window._close_after_export_cancel)
        finally:
            self.window._render_worker = None
            self.window._export_dialog = None
            self.window._close_after_export_cancel = False

    def test_project_save_runs_in_background_and_preserves_newer_edits(self) -> None:
        with TemporaryDirectory(prefix="pvs-background-save-") as raw_directory:
            target = Path(raw_directory) / "many-tracks.pvsproj"
            self.window.current_project_path = target
            self.window._project_dirty = True
            started = threading.Event()
            release = threading.Event()

            def delayed_save(path, _document, _thumbnail):
                started.set()
                release.wait(3)
                return Path(path).resolve()

            with patch.object(ProjectService, "save", side_effect=delayed_save):
                self.assertTrue(self.window._save_project())
                self.assertTrue(started.wait(1))
                worker = self.window._project_save_worker
                self.assertIsNotNone(worker)
                self.assertTrue(worker.isRunning())
                self.assertFalse(self.window.save_action.isEnabled())
                self.assertIn("project_save", self.window.activity_progress.active_keys)

                event_loop_responsive: list[bool] = []
                QTimer.singleShot(0, lambda: event_loop_responsive.append(True))
                self.application.processEvents()
                self.assertEqual(event_loop_responsive, [True])

                # This edit was not part of the frozen save snapshot and must not
                # be incorrectly marked as saved when the worker completes.
                self.window._schedule_history()
                release.set()
                self.window._wait_for_project_save(worker)

            self.assertIsNone(self.window._project_save_worker)
            self.assertNotIn("project_save", self.window.activity_progress.active_keys)
            self.assertTrue(self.window._project_dirty)
            self.assertTrue(self.window.save_action.isEnabled())
            save_message = self.window.statusBar().currentMessage()
            self.assertTrue(
                "저장되지" in save_message or "unsaved" in save_message.lower()
            )

    def test_required_background_save_can_wait_without_losing_success_state(self) -> None:
        with TemporaryDirectory(prefix="pvs-required-save-") as raw_directory:
            target = Path(raw_directory) / "close-save.pvsproj"
            self.window.current_project_path = target
            self.window._project_dirty = True
            with patch.object(
                ProjectService, "save", return_value=target.resolve(),
            ):
                saved = self.window._save_project(wait_for_completion=True)

            self.assertTrue(saved)
            self.assertFalse(self.window._project_dirty)
            self.assertIsNone(self.window._project_save_worker)
            self.assertEqual(self.window.current_project_path, target.resolve())

    def test_background_save_failure_keeps_project_dirty_and_reenables_save(self) -> None:
        with TemporaryDirectory(prefix="pvs-failed-save-") as raw_directory:
            self.window.current_project_path = Path(raw_directory) / "failed.pvsproj"
            self.window._project_dirty = True
            with (
                patch.object(
                    ProjectService, "save",
                    side_effect=ProjectError("simulated save failure"),
                ),
                patch.object(self.window, "_show_project_error") as show_error,
            ):
                saved = self.window._save_project(wait_for_completion=True)

            self.assertFalse(saved)
            self.assertTrue(self.window._project_dirty)
            self.assertIsNone(self.window._project_save_worker)
            self.assertTrue(self.window.save_action.isEnabled())
            self.assertTrue(self.window._autosave_debounce_timer.isActive())
            show_error.assert_called_once()

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
        self.assertEqual(values, {"glow", "rise", "none"})
        self.assertEqual(Source(SourceType.LYRICS, "Lyrics").subtitle_animation, "glow")
        labels = [
            self.window.inspector.subtitle_animation_combo.itemText(index)
            for index in range(self.window.inspector.subtitle_animation_combo.count())
        ]
        self.assertIn("글로우", labels)
        self.assertIn("라이즈", labels)
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
            self.assertIn(
                self.window.recent_projects_menu.menuAction(),
                self.window.file_menu.actions(),
            )
            self.assertEqual(self.window.recent_projects_menu.title(), "최근 프로젝트")
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
            self.assertEqual(self.window.recent_projects_menu.title(), "Recent projects")
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

    def test_file_recent_projects_menu_opens_the_selected_entry(self) -> None:
        with TemporaryDirectory(prefix="recent-project-menu-") as directory:
            project_path = Path(directory) / "Recent playlist.pvsproj"
            project_path.write_bytes(b"recent project placeholder")
            with patch.object(
                self.window.recent_projects, "projects",
                return_value=[project_path.resolve()],
            ):
                self.window._rebuild_recent_projects_menu()

            actions = [
                action for action in self.window.recent_projects_menu.actions()
                if action.isEnabled() and not action.isSeparator()
            ]
            self.assertGreaterEqual(len(actions), 2)
            self.assertIn(project_path.name, actions[0].text())
            self.assertEqual(actions[0].toolTip(), str(project_path.resolve()))
            with patch.object(self.window, "_open_recent_project") as open_recent:
                actions[0].trigger()
            open_recent.assert_called_once_with(project_path.resolve())

    def test_recent_project_open_uses_unsaved_change_guard(self) -> None:
        with TemporaryDirectory(prefix="recent-project-open-") as directory:
            project_path = Path(directory) / "Guarded.pvsproj"
            project_path.write_bytes(b"project placeholder")
            with (
                patch.object(
                    self.window, "_confirm_unsaved_changes", return_value=False,
                ) as confirm,
                patch.object(self.window, "_load_project_path") as load,
            ):
                self.assertFalse(self.window._open_recent_project(project_path))
            confirm.assert_called_once()
            load.assert_not_called()

            with (
                patch.object(
                    self.window, "_confirm_unsaved_changes", return_value=True,
                ),
                patch.object(
                    self.window, "_load_project_path", return_value=True,
                ) as load,
            ):
                self.assertTrue(self.window._open_recent_project(project_path))
            load.assert_called_once_with(project_path)

    def test_clear_recent_projects_requires_confirmation(self) -> None:
        with (
            patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.No,
            ) as question,
            patch.object(self.window.recent_projects, "clear") as clear,
        ):
            self.assertFalse(self.window._confirm_clear_recent_projects())
        question.assert_called_once()
        clear.assert_not_called()

        with (
            patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes,
            ),
            patch.object(self.window.recent_projects, "clear") as clear,
        ):
            self.assertTrue(self.window._confirm_clear_recent_projects())
        clear.assert_called_once()

    def test_view_menu_toggles_each_workspace_panel_with_shortcuts(self) -> None:
        settings = QSettings()
        keys = (
            "workspace/left_panel_visible",
            "workspace/right_panel_visible",
            "workspace/bottom_panel_visible",
        )
        original_values = {key: settings.value(key, None) for key in keys}
        actions = (
            self.window.panels_action,
            self.window.inspector_panel_action,
            self.window.bottom_panel_action,
        )
        try:
            for action in actions:
                action.setChecked(True)
            QTest.qWait(230)
            self.assertTrue(all(
                action in self.window.view_menu.actions() for action in actions
            ))
            self.assertEqual(
                [action.shortcut().toString() for action in actions],
                ["Ctrl+Alt+L", "Ctrl+Alt+R", "Ctrl+Alt+B"],
            )

            self.window.panels_action.trigger()
            self.application.processEvents()
            self.assertFalse(self.window.left_workspace.isHidden())
            QTest.qWait(230)
            self.assertTrue(self.window.left_workspace.isHidden())
            self.assertFalse(self.window.panels_action.isChecked())

            self.window.inspector_panel_action.trigger()
            self.application.processEvents()
            self.assertFalse(self.window.inspector_stack.isHidden())
            QTest.qWait(230)
            self.assertTrue(self.window.inspector_stack.isHidden())
            self.assertFalse(self.window.inspector_panel_action.isChecked())

            self.window.bottom_panel_action.trigger()
            self.application.processEvents()
            self.assertFalse(self.window.bottom_workspace_stack.isHidden())
            QTest.qWait(230)
            self.assertTrue(self.window.bottom_workspace_stack.isHidden())
            self.assertFalse(self.window.bottom_panel_action.isChecked())

            self.window._show_bottom_panel(1)
            QTest.qWait(230)
            self.assertFalse(self.window.bottom_workspace_stack.isHidden())
            self.assertTrue(self.window.bottom_panel_action.isChecked())
            self.assertEqual(self.window.bottom_tabs.currentIndex(), 1)

            # Preview can be closed while its sidebar collapse is still moving.
            # Reversing that transition must leave the editable sidebar open.
            self.window._set_sidebar_visible(
                False, persist=False, sync_action=False,
            )
            QTest.qWait(40)
            self.window._set_sidebar_visible(
                True, persist=False, sync_action=False,
            )
            QTest.qWait(230)
            self.assertFalse(self.window.left_workspace.isHidden())
            self.assertGreaterEqual(self.window.left_workspace.minimumWidth(), 180)
        finally:
            for action in actions:
                action.setChecked(True)
            QTest.qWait(230)
            for key, value in original_values.items():
                if value is None:
                    settings.remove(key)
                else:
                    settings.setValue(key, value)

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
            added, accepted, _notes = self.window._import_audio_files(
                [track.file_path]
            )
            self.assertEqual((added, accepted), (0, []))
            self.assertEqual(len(self.window.playlist_service.tracks), original_count)

            metadata_dialog.exec.return_value = QDialog.DialogCode.Accepted
            metadata_dialog.selected_tracks = [edited]
            added, accepted, _notes = self.window._import_audio_files(
                [track.file_path]
            )

        self.assertEqual(added, 1)
        self.assertEqual(accepted, [Path(track.file_path)])
        imported = self.window.playlist_service.tracks[-1]
        self.assertEqual(imported.artist, "Edited artist")
        self.assertEqual(imported.album, "Edited album")

    def _import_with_sidecar(
        self, folder_names: dict[str, str], mode: str, audio_name: str,
    ):
        """Import one audio file from a temp folder and return its playlist track."""
        restore = self.window.settings_service.current
        self.addCleanup(self.window.settings_service.save, restore)
        self.window.settings_service.save(replace(
            restore, lyrics_auto_attach_mode=mode,
        ))
        with TemporaryDirectory(prefix="pc-import-sidecar-") as raw:
            folder = Path(raw)
            for name, content in folder_names.items():
                (folder / name).write_text(content, encoding="utf-8")
            audio = folder / audio_name
            audio.write_bytes(b"ID3 stub")
            track = PlaylistTrack(str(audio.resolve()), Path(audio_name).stem)
            with patch.object(
                self.window.playlist_service, "inspect_files",
                return_value=[AudioImportCandidate(track)],
            ):
                added, _accepted, notes = self.window._import_audio_files(
                    [str(audio)]
                )
        self.assertEqual(added, 1)
        return self.window.playlist_service.tracks[-1], notes

    _LRC = "[00:01.00]First line\n[00:04.00]Second line\n"

    def test_same_name_lyric_file_is_attached_automatically_when_always(self) -> None:
        imported, notes = self._import_with_sidecar(
            {"Nightfall.lrc": self._LRC}, "always", "Nightfall.mp3",
        )
        self.assertTrue(imported.lyrics)
        self.assertEqual(Path(imported.lyrics_path).name, "Nightfall.lrc")
        self.assertEqual(len(notes), 1)

    def test_never_mode_skips_even_an_exact_lyric_match(self) -> None:
        imported, notes = self._import_with_sidecar(
            {"Nightfall.lrc": self._LRC}, "never", "Nightfall.mp3",
        )
        self.assertFalse(imported.lyrics)
        self.assertEqual(imported.lyrics_path, "")
        self.assertEqual(notes, [])

    def test_ask_mode_prompts_before_attaching_an_exact_match(self) -> None:
        with patch.object(
            QMessageBox, "question",
            return_value=QMessageBox.StandardButton.No,
        ) as question:
            imported, notes = self._import_with_sidecar(
                {"Nightfall.lrc": self._LRC}, "ask", "Nightfall.mp3",
            )
        question.assert_called_once()
        self.assertFalse(imported.lyrics)
        self.assertEqual(notes, [])

    def test_similar_name_lyric_always_prompts_even_in_always_mode(self) -> None:
        with patch.object(
            QMessageBox, "question",
            return_value=QMessageBox.StandardButton.Yes,
        ) as question:
            imported, notes = self._import_with_sidecar(
                {"Golden Hour.lrc": self._LRC}, "always", "01 - Golden Hour.mp3",
            )
        question.assert_called_once()
        self.assertTrue(imported.lyrics)
        self.assertEqual(Path(imported.lyrics_path).name, "Golden Hour.lrc")
        self.assertEqual(len(notes), 1)

    def test_settings_dialog_round_trips_the_lyrics_auto_attach_mode(self) -> None:
        dialog = SettingsDialog(
            replace(
                self.window.settings_service.current,
                lyrics_auto_attach_mode="ask",
            ),
            self.window.translator.language,
            self.window.theme_service.preference,
            self.window.translator,
            self.window,
        )
        try:
            self.assertEqual(
                dialog.lyrics_auto_attach_combo.currentData(), "ask",
            )
            dialog.lyrics_auto_attach_combo.setCurrentIndex(
                dialog.lyrics_auto_attach_combo.findData("never")
            )
            self.assertEqual(
                dialog.app_settings.lyrics_auto_attach_mode, "never",
            )
        finally:
            dialog.close()

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

    def test_legacy_theme_choices_keep_studio_and_open_dialogs_dark(self) -> None:
        dialog = QDialog(self.window)
        dialog.show()
        try:
            for preference in Theme:
                self.window.theme_service.set_preference(preference)
                self.application.processEvents()
                self.assertIs(self.window.theme_service.preference, Theme.DARK)
                self.assertLess(self.application.palette().window().color().lightness(), 128)
                self.assertLess(dialog.palette().window().color().lightness(), 128)
            self.assertIn("QMenu::item:selected", self.application.styleSheet())
            self.assertFalse(hasattr(self.window, "theme_menu"))
        finally:
            dialog.close()

    def test_source_buttons_show_localized_settings_on_hover(self) -> None:
        original_language = self.window.translator.language
        try:
            self.window.translator.set_language(Language.KOREAN)
            self.application.processEvents()
            self.assertEqual(set(self.window._source_buttons), set(SourceType))
            for button in self.window._source_buttons.values():
                self.assertIn("추가 후 설정", button.toolTip())
                self.assertIn("캔버스로 드래그하면 추가됩니다.", button.toolTip())
                self.assertGreaterEqual(button.toolTipDuration(), 10_000)
                self.assertTrue(button.accessibleDescription())

            self.window.translator.set_language(Language.ENGLISH)
            self.application.processEvents()
            for button in self.window._source_buttons.values():
                self.assertIn("Settings after adding", button.toolTip())
                self.assertIn("drag it onto the Canvas", button.toolTip())
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

    def test_inspector_continuous_values_pair_sliders_with_precise_spins(self) -> None:
        inspector = self.window.inspector
        linked = inspector._linked_sliders
        for spin in (
            inspector.width_spin, inspector.height_spin, inspector.rotation_spin,
            inspector.scale_spin, inspector.opacity_spin, inspector.radius_spin,
            inspector.font_size_spin, inspector.blur_spin,
            inspector.brightness_spin, inspector.contrast_spin,
            inspector.shadow_opacity_spin, inspector.shadow_blur_spin,
        ):
            with self.subTest(control=spin):
                self.assertIn(spin, linked)
                self.assertIs(inspector._slider_hosts[spin], spin.parentWidget())

        self.assertNotIn(inspector.x_spin, linked)
        opacity_slider = linked[inspector.opacity_spin]
        opacity_slider.setValue(12)
        self.assertAlmostEqual(inspector.opacity_spin.value(), 0.6)
        inspector.opacity_spin.setValue(0.35)
        self.assertEqual(opacity_slider.value(), 7)

        # Exact input still accepts values beyond the ergonomic drag range.
        inspector.width_spin.setValue(3200)
        self.assertEqual(inspector.width_spin.value(), 3200)
        self.assertEqual(linked[inspector.width_spin].value(),
                         linked[inspector.width_spin].maximum())

        first = Source(SourceType.TEXT, "First", opacity=0.25)
        second = Source(SourceType.TEXT, "Second", opacity=0.75)
        self.window.store.replace([first, second])
        inspector.set_sources((first.id, second.id), second)
        self.assertFalse(opacity_slider.isEnabled())
        self.assertEqual(inspector.opacity_spin.lineEdit().text(), "")
        inspector.set_source(first)
        self.assertTrue(opacity_slider.isEnabled())
        self.assertAlmostEqual(inspector.opacity_spin.value(), 0.25)

    def test_source_sidebar_has_no_footer_tip(self) -> None:
        self.assertFalse(hasattr(self.window, "sidebar_hint"))
        self.assertIs(self.window.source_cards_scroll.parent(), self.window.source_sidebar)

    def test_source_sidebar_groups_rich_cards_and_filters_whole_sections(self) -> None:
        self.assertEqual(
            set(self.window._source_category_sections),
            {"basic", "playback", "audio", "scene"},
        )
        image_button = self.window._source_buttons[SourceType.IMAGE]
        self.assertTrue(image_button.title_label.text())
        self.assertTrue(image_button.description_label.text())
        self.assertFalse(image_button.icon_label.pixmap().isNull())
        self.assertGreaterEqual(image_button.minimumHeight(), 50)

        self.window.source_search.setText("audio_visualizer")
        self.application.processEvents()
        self.assertFalse(
            self.window._source_category_sections["audio"].isHidden()
        )
        for category in ("basic", "playback", "scene"):
            self.assertTrue(
                self.window._source_category_sections[category].isHidden(),
                category,
            )
        self.assertIn("1", self.window.source_result_label.text())

        self.window.source_search.clear()
        self.application.processEvents()
        self.assertTrue(all(
            not section.isHidden()
            for section in self.window._source_category_sections.values()
        ))
        self.assertIn("17", self.window.source_result_label.text())

    def test_source_palette_category_tabs_and_search_interplay(self) -> None:
        window = self.window
        tab_bar = window.source_tab_bar
        self.assertEqual(
            [tab_bar.tabText(i) for i in range(tab_bar.count())],
            ["전체", "기본", "재생", "오디오", "장면"],
        )

        # A category tab shows only its own section.
        tab_bar.tabBarClicked.emit(3)  # "audio"
        self.application.processEvents()
        self.assertEqual(window._active_source_category, "audio")
        self.assertFalse(window._source_category_sections["audio"].isHidden())
        for other in ("basic", "playback", "scene"):
            self.assertTrue(window._source_category_sections[other].isHidden(), other)

        # Typing a search pins the bar back to "All" and spans every section.
        window.source_search.setText("progress")
        self.application.processEvents()
        self.assertEqual(tab_bar.currentIndex(), 0)
        self.assertEqual(window._active_source_category, "all")
        self.assertFalse(window._source_category_sections["playback"].isHidden())

        # Clicking a category while searching clears the query and restores it.
        tab_bar.tabBarClicked.emit(1)  # "basic"
        self.application.processEvents()
        self.assertEqual(window.source_search.text(), "")
        self.assertEqual(window._active_source_category, "basic")
        self.assertFalse(window._source_category_sections["basic"].isHidden())
        self.assertTrue(window._source_category_sections["audio"].isHidden())

        tab_bar.tabBarClicked.emit(0)  # back to "All"
        self.application.processEvents()
        self.assertTrue(all(
            not section.isHidden()
            for section in window._source_category_sections.values()
        ))

    def test_new_lyrics_source_has_room_for_preview_context_lines(self) -> None:
        self.window._add_source(SourceType.LYRICS)
        source = self.window.store.selected
        self.assertIsNotNone(source)
        self.assertIs(source.source_type, SourceType.LYRICS)
        self.assertGreaterEqual(source.width, 600)
        self.assertGreaterEqual(source.height, 200)

    def test_new_lyrics_and_track_lists_default_to_automatic_line_counts(self) -> None:
        self.window.translator.set_language(Language.KOREAN)
        self.window._add_source(SourceType.LYRICS)
        lyrics = self.window.store.sources()[-1]
        self.assertEqual(lyrics.subtitle_context_lines, -1)
        self.assertEqual(lyrics.subtitle_next_lines, -1)
        self.assertEqual(self.window.inspector.subtitle_context_lines_spin.text(), "자동")
        self.assertEqual(self.window.inspector.subtitle_next_lines_spin.text(), "자동")

        self.window._add_source(SourceType.TRACK_LIST)
        track_list = self.window.store.sources()[-1]
        self.assertEqual(track_list.track_list_count, 0)
        self.assertEqual(self.window.inspector.track_list_count_spin.text(), "자동")
        self.assertGreater(
            self.window.canvas._items[track_list.id].effective_track_list_count(),
            2,
        )

    def test_lyrics_use_the_shared_text_color_control_without_style_presets(self) -> None:
        self.window.translator.set_language(Language.KOREAN)
        source = Source(
            SourceType.LYRICS, "Editable lyrics color",
            outline_color="#A1B2C3", outline_width=0.0,
        )
        self.window.store.replace([source])
        self.window.store.select(source.id)
        self.window.show()
        self.application.processEvents()

        inspector = self.window.inspector
        self.assertNotIn("subtitle_style", inspector._field_widgets)
        self.assertTrue(inspector._field_visibility["text_color"])
        self.assertFalse(inspector._field_visibility["outline_color"])
        self.assertEqual(inspector._form_labels["text_color"].text(), "텍스트 색상")
        self.assertEqual(inspector.text_color_button.text(), "#A1B2C3")
        self.assertEqual(inspector._field_categories["font_weight"], "text")
        inspector.font_weight_combo.setCurrentIndex(
            inspector.font_weight_combo.findData(700)
        )
        self.assertEqual(source.font_weight, 700)
        special = inspector._tab_indices["special"]
        self.assertEqual(inspector.property_tabs.tabText(special), "자막/가사")
        self.assertTrue(inspector.property_tabs.isTabVisible(special))

    def test_background_defaults_unlocked_and_album_inspector_size_stays_square(self) -> None:
        welcome_background = next(
            source for source in self.window.store.sources()
            if source.source_type is SourceType.BACKGROUND
        )
        self.assertFalse(welcome_background.locked)

        self.window._add_source(SourceType.BACKGROUND)
        self.assertFalse(self.window.store.selected.locked)
        preset_background = next(
            source for source in PresetService.all()[0].builder()
            if source.source_type is SourceType.BACKGROUND
        )
        self.assertFalse(preset_background.locked)

        self.window._add_source(SourceType.ALBUM_COVER)
        cover = self.window.store.selected
        self.window.store.update(cover.id, width=245.0)
        self.assertEqual((cover.width, cover.height), (245.0, 245.0))
        self.window.store.update(cover.id, height=132.0)
        self.assertEqual((cover.width, cover.height), (132.0, 132.0))

    def test_image_variants_expand_under_parent_and_create_parent_sources(self) -> None:
        toggle = self.window._source_variant_toggles[SourceType.IMAGE]
        container = self.window._source_variant_containers[SourceType.IMAGE]
        self.assertFalse(toggle.isChecked())
        self.assertTrue(container.isHidden())

        toggle.click()
        self.application.processEvents()
        self.assertTrue(toggle.isChecked())
        self.assertFalse(container.isHidden())
        self.assertIs(
            self.window._source_variant_parents[SourceType.LOGO], SourceType.IMAGE
        )
        self.assertIs(
            self.window._source_variant_parents[SourceType.TIME], SourceType.TEXT
        )

        before = len(self.window.store.sources())
        self.window._source_buttons[SourceType.WATERMARK].click()
        self.assertEqual(len(self.window.store.sources()), before + 1)
        source = self.window.store.selected
        self.assertIsNotNone(source)
        self.assertIs(source.source_type, SourceType.IMAGE)
        self.assertEqual(source.name, "Watermark")
        self.assertEqual(source.image_fit_mode, "contain")
        self.assertAlmostEqual(source.opacity, 0.45)

        self.window._source_buttons[SourceType.TIME].click()
        source = self.window.store.selected
        self.assertIsNotNone(source)
        self.assertIs(source.source_type, SourceType.TEXT)
        self.assertEqual(source.text, "%current_time% / %total_time%")

    def test_source_template_drag_payload_adds_parent_at_drop_position(self) -> None:
        button = self.window._source_buttons[SourceType.LOGO]
        self.assertIsInstance(button, SourceTemplateButton)
        self.assertEqual(
            read_source_template_mime(button.create_mime_data()),
            (SourceType.LOGO.value, SourceType.IMAGE.value),
        )

        before = len(self.window.store.sources())
        self.window.canvas.source_template_dropped.emit(
            SourceType.LOGO.value, SourceType.IMAGE.value, QPointF(640.0, 360.0)
        )
        self.assertEqual(len(self.window.store.sources()), before + 1)
        source = self.window.store.selected
        self.assertIsNotNone(source)
        self.assertIs(source.source_type, SourceType.IMAGE)
        self.assertEqual((source.x, source.y), (550.0, 270.0))

        self.window.canvas.source_template_dropped.emit(
            SourceType.LOGO.value, SourceType.TEXT.value, QPointF(20.0, 20.0)
        )
        self.assertEqual(len(self.window.store.sources()), before + 1)

    def test_canvas_accepts_source_template_drop_event(self) -> None:
        button = self.window._source_buttons[SourceType.WATERMARK]
        before = len(self.window.store.sources())
        mime_data = button.create_mime_data()
        event = QDropEvent(
            QPointF(160.0, 140.0),
            Qt.DropAction.CopyAction,
            mime_data,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.window.canvas.dropEvent(event)
        self.assertTrue(event.isAccepted())
        self.assertEqual(len(self.window.store.sources()), before + 1)
        source = self.window.store.selected
        self.assertIsNotNone(source)
        self.assertIs(source.source_type, SourceType.IMAGE)
        artboard = self.window.canvas.scene_model.artboard_rect
        self.assertGreaterEqual(source.x, artboard.left())
        self.assertGreaterEqual(source.y, artboard.top())
        self.assertLessEqual(source.x + source.width, artboard.right())
        self.assertLessEqual(source.y + source.height, artboard.bottom())

    def test_project_content_image_and_video_urls_create_matching_canvas_elements(self) -> None:
        with TemporaryDirectory(prefix="playlist-content-canvas-drop-") as directory:
            image_path = Path(directory) / "cover.png"
            image = QImage(96, 54, QImage.Format.Format_ARGB32)
            image.fill(QColor("#336699"))
            self.assertTrue(image.save(str(image_path)))
            video_path = Path(directory) / "clip.mp4"
            video_path.write_bytes(b"project content video placeholder")
            self.window.project_content_service.add_paths([image_path, video_path])
            before = len(self.window.store.sources())

            for path in (image_path, video_path):
                mime = QMimeData()
                mime.setUrls([QUrl.fromLocalFile(str(path))])
                event = QDropEvent(
                    QPointF(240.0, 180.0), Qt.DropAction.CopyAction, mime,
                    Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                )
                self.window.canvas.dropEvent(event)
                self.assertTrue(event.isAccepted())
            added = self.window.store.sources()[before:]
            self.assertEqual(
                [source.source_type for source in added],
                [SourceType.IMAGE, SourceType.VIDEO],
            )
            self.assertEqual(Path(added[0].content_path).name, "cover.png")
            self.assertEqual(Path(added[1].content_path).name, "clip.mp4")
            for source in added:
                self.window.store.remove(source.id)
            self.application.processEvents()
            QTest.qWait(50)

    def test_project_content_items_publish_native_local_file_drag_payloads(self) -> None:
        with TemporaryDirectory(prefix="playlist-content-mime-") as directory:
            image_path = Path(directory) / "drag-cover.png"
            image = QImage(20, 20, QImage.Format.Format_ARGB32)
            image.fill(QColor("#123456"))
            self.assertTrue(image.save(str(image_path)))
            self.window.project_content_service.add_paths([image_path])
            self.application.processEvents()

            content_list = self.window.content_library_panel.list
            item = next(
                content_list.item(index) for index in range(content_list.count())
                if Path(str(content_list.item(index).data(
                    Qt.ItemDataRole.UserRole + 1
                ))).name == image_path.name
            )
            content_list.setCurrentItem(item)
            mime = content_list.mimeData([item])

            self.assertTrue(item.flags() & Qt.ItemFlag.ItemIsDragEnabled)
            self.assertEqual(
                content_list.supportedDropActions(), Qt.DropAction.CopyAction,
            )
            self.assertTrue(mime.hasUrls())
            self.assertEqual(
                Path(mime.urls()[0].toLocalFile()), image_path.resolve(),
            )

    def test_lyrics_drop_targets_the_playlist_row_under_pointer(self) -> None:
        track = PlaylistTrack("song.wav", "Drop target", duration_seconds=10.0)
        self.window.playlist_service.add_tracks([track])
        self.application.processEvents()
        item = self.window.playlist_editor.list_widget.item(0)
        self.assertIsNotNone(item)
        with TemporaryDirectory(prefix="playlist-lyrics-drop-") as directory:
            lyrics_path = Path(directory) / "incoming.lrc"
            lyrics_path.write_text("[00:01.00]Incoming lyric\n", encoding="utf-8")
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(lyrics_path))])
            event = QDropEvent(
                QPointF(self.window.playlist_editor.list_widget.visualItemRect(item).center()),
                Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            self.window.playlist_editor.list_widget.dropEvent(event)

        self.assertTrue(event.isAccepted())
        updated = self.window.playlist_service.tracks[0]
        self.assertEqual(updated.lyrics[0]["text"], "Incoming lyric")
        self.assertTrue(updated.lyrics_path.endswith("incoming.lrc"))

    def test_playlist_accepts_external_drops_for_project_content_dnd(self) -> None:
        from PySide6.QtGui import QDragEnterEvent

        track = PlaylistTrack("song.wav", "Row", duration_seconds=10.0)
        self.window.playlist_service.add_tracks([track])
        self.application.processEvents()
        playlist_list = self.window.playlist_editor.list_widget

        # InternalMove silently rejects external drops before dropEvent runs,
        # which is why lyrics dragged from Project Content never attached.
        self.assertEqual(
            playlist_list.dragDropMode(),
            QAbstractItemView.DragDropMode.DragDrop,
        )

        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QDragMoveEvent

        with TemporaryDirectory(prefix="pl-ext-drop-") as directory:
            lyrics_path = Path(directory) / "song.lrc"
            lyrics_path.write_text("[00:01.00]hi\n", encoding="utf-8")
            item = playlist_list.item(0)
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(lyrics_path))])
            row_center = playlist_list.visualItemRect(item).center()

            def make(kind, pos):
                return kind(
                    pos, Qt.DropAction.CopyAction, mime,
                    Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                )

            # The drag border is crossed over the spacing gap, not a row.  The
            # ENTER must still be accepted or Qt stops delivering move events
            # and the cursor stays forbidden even after reaching the row.
            enter = make(QDragEnterEvent, QPoint(2, 1))
            playlist_list.dragEnterEvent(enter)
            self.assertTrue(enter.isAccepted())

            off_row = make(QDragMoveEvent, QPoint(2, 1))
            playlist_list.dragMoveEvent(off_row)
            self.assertFalse(off_row.isAccepted())

            on_row = make(QDragMoveEvent, row_center)
            playlist_list.dragMoveEvent(on_row)
            self.assertTrue(on_row.isAccepted())

    def test_playlist_drag_reorder_moves_rows_without_duplicating(self) -> None:
        from PySide6.QtGui import QDropEvent

        self.window.playlist_service.add_tracks([
            PlaylistTrack("a.wav", "Alpha", duration_seconds=10.0),
            PlaylistTrack("b.wav", "Bravo", duration_seconds=10.0),
            PlaylistTrack("c.wav", "Charlie", duration_seconds=10.0),
        ])
        self.application.processEvents()
        playlist_list = self.window.playlist_editor.list_widget
        service = self.window.playlist_service

        # Drag "Alpha" (row 0) onto the lower half of "Charlie" (row 2).
        playlist_list.item(0).setSelected(True)
        target = playlist_list.item(2)
        rect = playlist_list.visualItemRect(target)
        drop_point = QPointF(rect.center().x(), rect.bottom() - 1)
        mime = QMimeData()
        drop = QDropEvent(
            drop_point, Qt.DropAction.MoveAction, mime,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        playlist_list._reorder_from_drop(drop)
        self.application.processEvents()

        titles = [track.title for track in service.tracks]
        self.assertEqual(titles, ["Bravo", "Charlie", "Alpha"])
        self.assertEqual(len(titles), 3)  # moved, not copied

    def test_existing_lyrics_are_replaced_only_after_comparison_choice(self) -> None:
        track = PlaylistTrack(
            "song.wav", "Existing lyrics", duration_seconds=10.0,
            lyrics_path="old.lrc",
            lyrics=[{"start": 0.0, "end": 4.0, "text": "Old lyric"}],
        )
        self.window.playlist_service.add_tracks([track])
        with TemporaryDirectory(prefix="playlist-lyrics-compare-") as directory:
            lyrics_path = Path(directory) / "replacement.lrc"
            lyrics_path.write_text("[00:02.00]New lyric\n", encoding="utf-8")
            with patch("app.ui.main_window.LyricsCompareDialog") as dialog_type:
                dialog_type.return_value.exec.return_value = QDialog.DialogCode.Rejected
                dialog_type.return_value.replace_requested = False
                self.assertFalse(
                    self.window._attach_lyrics_to_track(lyrics_path, track.id)
                )
                self.assertEqual(
                    self.window.playlist_service.tracks[0].lyrics[0]["text"],
                    "Old lyric",
                )

                dialog_type.return_value.exec.return_value = QDialog.DialogCode.Accepted
                dialog_type.return_value.replace_requested = True
                self.assertTrue(
                    self.window._attach_lyrics_to_track(lyrics_path, track.id)
                )

        self.assertEqual(
            self.window.playlist_service.tracks[0].lyrics[0]["text"], "New lyric",
        )

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
        fake_dialog = MagicMock()
        fake_dialog.exec.return_value = QDialog.DialogCode.Accepted
        fake_dialog.selected_color = transparent
        fake_dialog.personal_settings.return_value = {
            "personal_color_enabled": False,
            "personal_color_brightness": 0.0,
            "personal_color_saturation": 0.0,
            "personal_color_hue_shift": 0.0,
            "personal_color_strength": 1.0,
        }
        with patch(
            "app.inspector.source_inspector.ColorEditorDialog",
            return_value=fake_dialog,
        ):
            self.window.inspector._choose_color(
                "fill_color", self.window.inspector.fill_color_button,
            )

        self.assertEqual(source.fill_color, "#00334455")
        self.assertEqual(source.opacity, 0.7)
        self.assertEqual(
            self.window.inspector.fill_color_button.text(), "Transparent"
        )

    def test_color_editor_dialog_commits_color_and_personal_policy(self) -> None:
        first = Source(SourceType.TEXT, "First personal color")
        second = Source(SourceType.SHAPE, "Second personal color")
        self.window.store.replace([first, second])
        self.window.store.select_many([first.id, second.id], second.id)
        inspector = self.window.inspector

        fake_dialog = MagicMock()
        fake_dialog.exec.return_value = QDialog.DialogCode.Accepted
        fake_dialog.selected_color = QColor("#123456")
        fake_dialog.personal_settings.return_value = {
            "personal_color_enabled": True,
            "personal_color_brightness": 18.0,
            "personal_color_saturation": -12.0,
            "personal_color_hue_shift": 35.0,
            "personal_color_strength": 0.7,
        }
        with patch(
            "app.inspector.source_inspector.ColorEditorDialog",
            return_value=fake_dialog,
        ):
            inspector._choose_color("fill_color", inspector.fill_color_button)

        for source in (first, second):
            self.assertEqual(source.fill_color, "#123456")
            self.assertTrue(source.personal_color_enabled)
            self.assertEqual(source.personal_color_brightness, 18.0)
            self.assertEqual(source.personal_color_saturation, -12.0)
            self.assertEqual(source.personal_color_hue_shift, 35.0)
            self.assertEqual(source.personal_color_strength, 0.7)

    def test_export_visualizer_receives_each_tracks_personal_color(self) -> None:
        with TemporaryDirectory(prefix="playlist-visualizer-color-") as directory:
            red_path = Path(directory) / "red.png"
            green_path = Path(directory) / "green.png"
            red = QImage(24, 24, QImage.Format.Format_ARGB32)
            green = QImage(24, 24, QImage.Format.Format_ARGB32)
            red.fill(QColor("#E03030"))
            green.fill(QColor("#30D050"))
            self.assertTrue(red.save(str(red_path)))
            self.assertTrue(green.save(str(green_path)))

            source = Source(
                SourceType.AUDIO_VISUALIZER, "Personal visualizer",
                fill_color="#FFFFFF", personal_color_enabled=True,
            )
            self.window.store.replace([source])
            tracks = [
                PlaylistTrack(
                    "missing-a.wav", "A", duration_seconds=2.0,
                    cover_path=str(red_path),
                ),
                PlaylistTrack(
                    "missing-b.wav", "B", duration_seconds=2.0,
                    cover_path=str(green_path),
                ),
            ]

            overlays = self.window._export_visualizers(tracks)

        self.assertEqual(len(overlays), 1)
        self.assertEqual(len(overlays[0].personal_colors), 2)
        first = QColor(overlays[0].personal_colors[0])
        second = QColor(overlays[0].personal_colors[1])
        self.assertGreater(first.red(), first.green())
        self.assertGreater(second.green(), second.red())

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

    def test_window_resize_changes_only_the_central_canvas_extent(self) -> None:
        self.window.resize(1560, 920)
        self.window.show()
        self.application.processEvents()
        horizontal_before = self.window.main_splitter.sizes()
        vertical_before = self.window.workspace_splitter.sizes()

        self.window.resize(1840, 1080)
        self.application.processEvents()
        horizontal_after = self.window.main_splitter.sizes()
        vertical_after = self.window.workspace_splitter.sizes()

        self.assertEqual(horizontal_after[0], horizontal_before[0])
        self.assertEqual(horizontal_after[2], horizontal_before[2])
        self.assertGreater(horizontal_after[1], horizontal_before[1])
        self.assertEqual(vertical_after[1], vertical_before[1])
        self.assertGreater(vertical_after[0], vertical_before[0])
        self.assertEqual(
            self.window.left_workspace.sizePolicy().horizontalPolicy(),
            QSizePolicy.Policy.Preferred,
        )
        self.assertEqual(
            self.window.inspector.sizePolicy().horizontalPolicy(),
            QSizePolicy.Policy.Preferred,
        )
        self.assertEqual(
            self.window.bottom_tabs.sizePolicy().verticalPolicy(),
            QSizePolicy.Policy.Preferred,
        )

        self.window.resize(1560, 920)
        self.application.processEvents()
        self.assertEqual(
            self.window.main_splitter.sizes(), horizontal_before,
        )
        self.assertEqual(
            self.window.workspace_splitter.sizes(), vertical_before,
        )

        # A user-adjusted splitter position becomes the new fixed edge size.
        total_width = sum(self.window.main_splitter.sizes())
        self.window.main_splitter.setSizes([520, max(300, total_width - 860), 340])
        total_height = sum(self.window.workspace_splitter.sizes())
        self.window.workspace_splitter.setSizes([max(300, total_height - 240), 240])
        self.application.processEvents()
        user_horizontal = self.window.main_splitter.sizes()
        user_vertical = self.window.workspace_splitter.sizes()
        self.window.resize(1760, 1020)
        self.application.processEvents()
        resized_horizontal = self.window.main_splitter.sizes()
        resized_vertical = self.window.workspace_splitter.sizes()
        self.assertEqual(resized_horizontal[0], user_horizontal[0])
        self.assertEqual(resized_horizontal[2], user_horizontal[2])
        self.assertEqual(resized_vertical[1], user_vertical[1])

    def test_workspace_panel_sizes_persist_across_program_restarts(self) -> None:
        with TemporaryDirectory(prefix="pvs-panel-settings-") as directory:
            settings_path = Path(directory) / "workspace.ini"

            def isolated_settings() -> QSettings:
                return QSettings(
                    str(settings_path), QSettings.Format.IniFormat,
                )

            first_window = None
            second_window = None
            with patch("app.ui.main_window.QSettings", side_effect=isolated_settings):
                try:
                    first_window = MainWindow()
                    first_window.resize(1600, 980)
                    first_window.show()
                    self.application.processEvents()

                    horizontal_total = sum(first_window.main_splitter.sizes())
                    first_window.main_splitter.setSizes([
                        360, max(300, horizontal_total - 780), 420,
                    ])
                    vertical_total = sum(first_window.workspace_splitter.sizes())
                    first_window.workspace_splitter.setSizes([
                        max(300, vertical_total - 310), 310,
                    ])
                    self.application.processEvents()
                    first_window.main_splitter.splitterMoved.emit(360, 1)
                    first_window.workspace_splitter.splitterMoved.emit(310, 1)
                    QTest.qWait(280)

                    stored = isolated_settings()
                    stored.sync()
                    self.assertEqual(
                        int(stored.value("workspace/left_panel_width")), 360,
                    )
                    self.assertEqual(
                        int(stored.value("workspace/right_panel_width")), 420,
                    )
                    self.assertEqual(
                        int(stored.value("workspace/bottom_panel_height")), 310,
                    )

                    # Hiding a panel must not replace its useful open size with 0.
                    for panel in (
                        first_window.left_workspace,
                        first_window.inspector_stack,
                        first_window.bottom_workspace_stack,
                    ):
                        panel.setVisible(False)
                    first_window._save_workspace_layout()
                    stored.sync()
                    self.assertEqual(
                        int(stored.value("workspace/left_panel_width")), 360,
                    )
                    self.assertEqual(
                        int(stored.value("workspace/right_panel_width")), 420,
                    )
                    self.assertEqual(
                        int(stored.value("workspace/bottom_panel_height")), 310,
                    )
                    for panel in (
                        first_window.left_workspace,
                        first_window.inspector_stack,
                        first_window.bottom_workspace_stack,
                    ):
                        panel.setVisible(True)

                    second_window = MainWindow()
                    second_window.resize(1600, 980)
                    second_window.show()
                    self.application.processEvents()

                    self.assertAlmostEqual(
                        second_window.main_splitter.sizes()[0], 360, delta=2,
                    )
                    self.assertAlmostEqual(
                        second_window.main_splitter.sizes()[2], 420, delta=2,
                    )
                    self.assertAlmostEqual(
                        second_window.workspace_splitter.sizes()[1], 310, delta=2,
                    )
                    self.assertEqual(second_window._sidebar_open_width, 360)
                    self.assertEqual(second_window._inspector_open_width, 420)
                    self.assertEqual(second_window._bottom_open_height, 310)
                finally:
                    for window in (second_window, first_window):
                        if window is not None:
                            window._project_dirty = False
                            window.close()

    def test_canvas_hover_uses_directional_cursor_on_selected_resize_handles(self) -> None:
        source = next(
            source for source in self.window.store.sources() if not source.locked
        )
        self.window.store.select(source.id)
        self.window.show()
        self.window.canvas.fit_artboard()
        self.application.processEvents()
        item = self.window.canvas._items[source.id]
        expected = {
            "e": Qt.CursorShape.SizeHorCursor,
            "n": Qt.CursorShape.SizeVerCursor,
            "se": Qt.CursorShape.SizeFDiagCursor,
        }
        for handle, cursor_shape in expected.items():
            with self.subTest(handle=handle):
                scene_position = item.mapToScene(
                    item.resize_handle_rects()[handle].center()
                )
                viewport_position = self.window.canvas.mapFromScene(scene_position)
                # QTest.mouseMove relies on the process-global pointer and the
                # offscreen backend may coalesce it across test modules. Send
                # an explicit widget-local move to exercise the same event path
                # deterministically.
                global_position = self.window.canvas.viewport().mapToGlobal(
                    viewport_position
                )
                move_event = QMouseEvent(
                    QEvent.Type.MouseMove,
                    QPointF(viewport_position),
                    QPointF(global_position),
                    Qt.MouseButton.NoButton,
                    Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.NoModifier,
                )
                QApplication.sendEvent(self.window.canvas.viewport(), move_event)
                self.application.processEvents()
                self.assertEqual(
                    self.window.canvas.viewport().cursor().shape(), cursor_shape,
                )

    def test_canvas_press_uses_the_same_tolerant_handle_as_its_cursor(self) -> None:
        source = next(
            source for source in self.window.store.sources() if not source.locked
        )
        self.window.store.select(source.id)
        self.window.show()
        self.application.processEvents()
        item = self.window.canvas._items[source.id]
        scene_center = item.mapToScene(item.content_rect().center())
        viewport_center = self.window.canvas.mapFromScene(scene_center)

        with patch.object(
            self.window.canvas, "_edit_handle_at_view_position",
            side_effect=lambda candidate, _position: (
                "e" if candidate is item else None
            ),
        ):
            QTest.mousePress(
                self.window.canvas.viewport(), Qt.MouseButton.LeftButton,
                pos=viewport_center,
            )
            self.assertTrue(item._resizing)
            self.assertEqual(item._resize_handle, "e")
            QTest.mouseRelease(
                self.window.canvas.viewport(), Qt.MouseButton.LeftButton,
                pos=viewport_center,
            )

        self.assertFalse(item._resizing)
        self.assertIsNone(item._resize_handle)

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
        self.assertTrue(inspector._field_visibility["font_family"])

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
        self.assertFalse(inspector._field_visibility["font_family"])
        self.assertFalse(inspector._field_visibility["font_size"])
        self.assertFalse(inspector._field_visibility["text"])

    def test_token_editor_pairs_percent_and_inserts_completion_from_keyboard(self) -> None:
        editor = TokenLineEdit(self.window.translator)
        editor.show()
        editor.setFocus()

        QTest.keyClicks(editor, "%")
        self.application.processEvents()
        self.assertEqual(editor.text(), "%%")
        self.assertEqual(editor.cursorPosition(), 1)
        self.assertTrue(editor.token_popup.isVisible())
        self.assertEqual(editor.token_popup.list.count(), 12)
        self.assertEqual(editor.token_popup.current_token(), "title")
        self.assertIn("%title%", editor.token_popup.description.text())

        QTest.keyClick(editor, Qt.Key.Key_Down)
        self.assertEqual(editor.token_popup.current_token(), "artist")
        self.assertIn("아티스트", editor.token_popup.description.text())
        QTest.keyClick(editor, Qt.Key.Key_Right)
        self.assertEqual(editor.text(), "%artist%")
        self.assertEqual(editor.cursorPosition(), len("%artist%"))
        self.assertFalse(editor.token_popup.isVisible())

        editor.clear()
        QTest.keyClicks(editor, "%")
        QTest.keyClick(editor, Qt.Key.Key_Backspace)
        self.assertEqual(editor.text(), "")
        editor.close()

    def test_token_editor_filters_prefix_and_multiline_editor_wraps_selection(self) -> None:
        editor = TokenPlainTextEdit(self.window.translator)
        editor.show()
        editor.setFocus()
        editor.setPlainText("Track: ")
        editor.moveCursor(editor.textCursor().MoveOperation.End)

        QTest.keyClicks(editor, "%ti")
        self.application.processEvents()
        self.assertEqual(editor.toPlainText(), "Track: %ti%")
        self.assertEqual(editor.token_popup.list.count(), 1)
        self.assertEqual(editor.token_popup.current_token(), "title")
        QTest.keyClick(editor, Qt.Key.Key_Return)
        self.assertEqual(editor.toPlainText(), "Track: %title%")

        editor.selectAll()
        QTest.keyClicks(editor, "%")
        self.assertEqual(editor.toPlainText(), "%Track: %title%%")
        editor.close()

    def test_expanded_text_dialog_and_inspector_apply_long_text(self) -> None:
        source = Source(SourceType.TEXT, "Long text", text="Short")
        self.window.store.add(source)
        self.application.processEvents()
        inspector = self.window.inspector
        self.assertFalse(inspector.expand_text_button.isHidden())

        with patch("app.inspector.source_inspector.TextEditorDialog") as dialog_type:
            dialog = dialog_type.return_value
            dialog.exec.return_value = QDialog.DialogCode.Accepted
            dialog.text.return_value = "First line\n%artist% — second line"
            inspector._open_expanded_text_editor()

        self.assertEqual(source.text, "First line\n%artist% — second line")
        self.assertEqual(inspector.text_edit.text(), source.text)

        expanded = TextEditorDialog(source.text, self.window.translator)
        self.assertEqual(expanded.editor.toPlainText(), source.text)
        self.assertGreaterEqual(expanded.minimumWidth(), 480)
        expanded.close()

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
            "align_bottom", "distribute_horizontal", "distribute_vertical",
            "group", "ungroup", "toggle_visible",
            "toggle_lock", "select_all",
        }.issubset(actions))
        self.assertTrue(actions["align_left"].isEnabled())
        # Distribute needs three sources; only two are selected here.
        self.assertFalse(actions["distribute_horizontal"].isEnabled())
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
            set(empty_actions),
            {"paste", "select_all", "unlock_all_layers", "fit_canvas"},
        )
        # Two sources are locked above, so the unlock-all command is offered.
        self.assertTrue(empty_actions["unlock_all_layers"].isEnabled())

    def test_canvas_right_click_never_changes_source_selection(self) -> None:
        first = Source(SourceType.TEXT, "Selected", x=80, y=90, width=220, height=100)
        second = Source(SourceType.SHAPE, "Not selected", x=420, y=260, width=180, height=120)
        self.window.store.replace([first, second])
        self.window.store.select(first.id)
        self.window.show()
        self.application.processEvents()

        canvas = self.window.canvas
        second_item = canvas._items[second.id]
        position = canvas.mapFromScene(second_item.sceneBoundingRect().center())
        fake_menu = MagicMock()
        with patch.object(canvas, "_create_context_menu", return_value=fake_menu) as create_menu:
            QTest.mousePress(canvas.viewport(), Qt.MouseButton.RightButton, pos=position)
            QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.RightButton, pos=position)
            self.application.processEvents()

            create_menu.reset_mock()
            canvas.contextMenuEvent(QContextMenuEvent(
                QContextMenuEvent.Reason.Mouse,
                position,
                canvas.viewport().mapToGlobal(position),
            ))

        self.assertEqual(self.window.store.selected_ids, (first.id,))
        create_menu.assert_called_once_with(None)

    def test_canvas_animation_preview_is_non_blocking_and_restores_source(self) -> None:
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
        # The preview is non-blocking: the window stays interactive.
        self.assertTrue(self.window.isEnabled())
        self.assertNotEqual(item.pos(), original_position)
        self.assertEqual(source.to_dict(), original_model)

        # Once armed, any further user action stops the preview immediately.
        QTest.qWait(1)
        self.window.store.source_changed.emit(source)
        self.assertFalse(self.window._animation_preview_active)
        self.assertFalse(self.window.animation_preview_controller.active)
        self.assertEqual(item.pos(), original_position)
        self.assertEqual(item.scale(), original_scale)
        self.assertEqual(item.opacity(), original_opacity)

    def test_canvas_animation_preview_completes_when_left_alone(self) -> None:
        source = self.window.store.sources()[0]
        self.window.store.update(
            source.id, animation_in="slide_left", animation_out="zoom",
            animation_in_duration=0.1, animation_out_duration=0.1,
        )
        self.window.store.select(source.id)
        source = self.window.store.get(source.id)
        item = self.window.canvas._items[source.id]
        original_position = QPointF(item.pos())

        self.window._preview_source_animation(source.id)
        QTest.qWait(900)
        self.assertFalse(self.window._animation_preview_active)
        self.assertEqual(item.pos(), original_position)
        self.assertTrue(item.isSelected())
        self.assertEqual(self.window.store.selected.id, source.id)

    def test_text_tokens_render_as_labelled_placeholders_on_canvas(self) -> None:
        self.window._add_source(SourceType.TEXT)
        source = self.window.store.selected
        item = self.window.canvas._items[source.id]

        # Main-window tests run in Korean; surrounding literal text is preserved.
        self.window.store.update(source.id, text="%title%이것은 제목입니다")
        self.assertEqual(item._render_text(), "(제목)이것은 제목입니다")

        self.window.store.update(source.id, text="%title% - %artist%")
        self.assertEqual(item._render_text(), "(제목) - (아티스트)")

        # Unknown tokens are left untouched.
        self.window.store.update(source.id, text="%title% %mystery%")
        self.assertEqual(item._render_text(), "(제목) %mystery%")

        # There is no longer a sample-data toggle.
        self.assertFalse(hasattr(self.window, "sample_data_action"))

    def test_distribute_spacing_evens_gaps_and_keeps_outermost(self) -> None:
        ids: list[str] = []
        for x, width in ((0, 100), (140, 60), (400, 120), (700, 40)):
            self.window._add_source(SourceType.SHAPE)
            source = self.window.store.selected
            self.window.store.update(
                source.id, x=float(x), y=0.0,
                width=float(width), height=50.0, scale=1.0,
            )
            ids.append(source.id)
        self.window.store.select_many(ids, ids[-1])

        self.window._handle_canvas_context_command("distribute_horizontal")
        by_id = {source.id: source for source in self.window.store.sources()}
        ordered = sorted((by_id[i] for i in ids), key=lambda s: s.x)
        gaps = [
            round(ordered[k + 1].x - (ordered[k].x + ordered[k].width), 3)
            for k in range(len(ordered) - 1)
        ]
        self.assertEqual(len(set(gaps)), 1)
        self.assertEqual(ordered[0].x, 0.0)
        self.assertAlmostEqual(ordered[-1].x, 700.0)

        # Fewer than three selected sources leave positions untouched.
        self.window.store.select_many(ids[:2], ids[1])
        before = by_id[ids[0]].x
        self.window._handle_canvas_context_command("distribute_horizontal")
        self.assertEqual(by_id[ids[0]].x, before)

    def test_unlock_all_sources_from_canvas_menu_and_layer_panel(self) -> None:
        self.window._add_source(SourceType.TEXT)
        first = self.window.store.selected
        self.window._add_source(SourceType.SHAPE)
        second = self.window.store.selected
        self.window.store.update(first.id, locked=True)
        self.window.store.update(second.id, locked=True)
        self.application.processEvents()

        panel = self.window.layer_panel
        self.assertFalse(panel.unlock_all_button.isHidden())
        self.assertTrue(panel.unlock_all_button.isEnabled())

        menu = self.window.canvas._create_context_menu(None)
        unlock_action = next(
            action for action in menu.actions()
            if action.data() == "unlock_all_layers"
        )
        self.assertTrue(unlock_action.isEnabled())

        self.window._handle_canvas_context_command("unlock_all_layers")
        self.assertEqual(
            sum(source.locked for source in self.window.store.sources()), 0
        )
        self.application.processEvents()
        self.assertTrue(panel.unlock_all_button.isHidden())

        # The empty-canvas action is disabled again once nothing is locked.
        menu = self.window.canvas._create_context_menu(None)
        unlock_action = next(
            action for action in menu.actions()
            if action.data() == "unlock_all_layers"
        )
        self.assertFalse(unlock_action.isEnabled())

    def test_save_reapply_and_delete_user_preset(self) -> None:
        from app.presets.user_preset_service import UserPresetService, all_presets

        self.window._add_source(SourceType.TEXT)
        self.window._add_source(SourceType.SHAPE)
        original = sorted(s.source_type.value for s in self.window.store.sources())

        with patch(
            "app.ui.main_window.QInputDialog.getText",
            return_value=("My Layout", True),
        ):
            self.window._save_current_as_preset()

        saved = UserPresetService.all()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].name("en"), "My Layout")
        self.assertTrue(saved[0].editable)
        self.assertIn(
            saved[0].identifier, {p.identifier for p in all_presets()}
        )

        self.window.store.replace([])
        self.window._apply_preset(saved[0])
        self.assertEqual(
            sorted(s.source_type.value for s in self.window.store.sources()),
            original,
        )
        # Applied sources get fresh ids so repeated applies never collide.
        self.window._apply_preset(saved[0])
        ids = [s.id for s in self.window.store.sources()]
        self.assertEqual(len(ids), len(set(ids)))

        self.assertTrue(UserPresetService.delete(saved[0].identifier))
        self.assertEqual(UserPresetService.all(), [])

    def test_preset_dialog_lists_and_exports_user_presets(self) -> None:
        from app.presets.user_preset_service import UserPresetService
        from app.dialogs.preset_dialog import DesignPresetDialog

        self.window._add_source(SourceType.TEXT)
        preset = UserPresetService.save(
            "Portable One", self.window.store.sources(), 1280.0, 720.0,
        )
        dialog = DesignPresetDialog(self.window.translator, self.window)
        try:
            labels = [
                dialog.list_widget.item(row).text()
                for row in range(dialog.list_widget.count())
            ]
            self.assertIn("Portable One", labels)
            export_path = Path(self._preset_dir) / "exported.pcpreset.json"
            UserPresetService.export_to(preset.identifier, export_path)
            self.assertTrue(export_path.is_file())
            reimported = UserPresetService.import_from(export_path)
            self.assertEqual(reimported.name("en"), "Portable One")
        finally:
            dialog.close()
            for definition in UserPresetService.all():
                UserPresetService.delete(definition.identifier)

    def test_preview_tab_embeds_canvas_controls_and_restores_editing_tab(self) -> None:
        track = PlaylistTrack(
            "preview.wav", "Preview", duration_seconds=10.0,
        )
        self.window.playlist_service.add_tracks([track])
        total_width = sum(self.window.main_splitter.sizes())
        self.window.main_splitter.setSizes([
            355, max(300, total_width - 735), 380,
        ])
        self.application.processEvents()
        expected_sidebar_width = self.window.main_splitter.sizes()[0]

        class StubPreview(QDialog):
            def __init__(self, *_args, **kwargs) -> None:
                super().__init__(kwargs.get("parent"))
                self.stopped = False
                self.controls_page = QWidget()
                self.track_list_panel = QFrame()
                self.preferred_backend = kwargs.get("preferred_backend")

            def build_embedded_controls_page(self) -> QWidget:
                return self.controls_page

            def _stop_preview(self) -> None:
                self.stopped = True

        with patch(
            "app.ui.main_window.ExportPreviewDialog", StubPreview,
        ):
            self.window.preview_action.trigger()
            self.application.processEvents()
            self.assertFalse(self.window.left_workspace.isHidden())
            QTest.qWait(230)

        preview = self.window._inline_preview
        self.assertIsNotNone(preview)
        self.assertEqual(
            preview.preferred_backend,
            self.window._preview_backend_for_session,
        )
        self.assertIs(self.window.canvas_stack.currentWidget(), preview)
        self.assertEqual(self.window.bottom_tabs.currentIndex(), 2)
        self.assertIs(
            self.window.bottom_workspace_stack.currentWidget(),
            self.window.bottom_tabs,
        )
        self.assertIs(
            preview.controls_page.parentWidget(), self.window.preview_tab_page,
        )
        self.assertIs(
            preview.track_list_panel.parentWidget(),
            self.window.preview_track_inspector,
        )
        self.assertIs(
            self.window.inspector_stack.currentWidget(),
            self.window.preview_track_inspector,
        )
        self.assertFalse(self.window.canvas.isEnabled())
        self.assertTrue(self.window.left_workspace.isHidden())
        self.assertTrue(self.window.inspector_stack.isEnabled())
        self.assertTrue(self.window.bottom_tabs.isEnabled())
        self.assertFalse(self.window.toolbar.isEnabled())
        self.assertFalse(self.window.toolbar.isHidden())
        self.assertFalse(self.window.menuBar().isEnabled())
        self.assertFalse(self.window.acceptDrops())
        self.assertIsNone(
            self.window.toolbar.widgetForAction(self.window.preview_action),
        )

        with patch.object(self.window.canvas, "fit_artboard") as fit_artboard:
            self.window.bottom_tabs.setCurrentIndex(1)
            QTest.qWait(250)
        fit_artboard.assert_called_once_with()

        self.assertIsNone(self.window._inline_preview)
        self.assertEqual(self.window.bottom_tabs.currentIndex(), 1)
        self.assertIs(self.window.canvas_stack.currentWidget(), self.window.canvas)
        self.assertIs(
            self.window.bottom_workspace_stack.currentWidget(),
            self.window.bottom_tabs,
        )
        self.assertTrue(preview.stopped)
        self.assertTrue(self.window.canvas.isEnabled())
        self.assertFalse(self.window.left_workspace.isHidden())
        self.assertTrue(self.window.inspector.isEnabled())
        self.assertIs(
            self.window.inspector_stack.currentWidget(), self.window.inspector,
        )
        self.assertTrue(self.window.bottom_tabs.isEnabled())
        self.assertTrue(self.window.toolbar.isEnabled())
        self.assertTrue(self.window.menuBar().isEnabled())
        self.assertTrue(self.window.acceptDrops())
        self.assertAlmostEqual(
            self.window.main_splitter.sizes()[0], expected_sidebar_width, delta=3,
        )
        self.assertEqual(self.window._sidebar_open_width, expected_sidebar_width)

    def test_major_window_state_changes_schedule_canvas_fit(self) -> None:
        normal = Qt.WindowState.WindowNoState
        maximized = Qt.WindowState.WindowMaximized
        minimized = Qt.WindowState.WindowMinimized

        with patch.object(self.window, "_schedule_canvas_fit") as schedule_fit:
            self.window._handle_canvas_window_state_change(normal, maximized)
            schedule_fit.assert_called_once_with(100)

        self.window._canvas_fit_pending = False
        with patch.object(self.window, "_schedule_canvas_fit") as schedule_fit:
            self.window._handle_canvas_window_state_change(normal, minimized)
            schedule_fit.assert_not_called()
            self.assertTrue(self.window._canvas_fit_pending)
            self.window._handle_canvas_window_state_change(minimized, normal)
            schedule_fit.assert_called_once_with(100)

    def test_ordinary_window_state_event_does_not_reset_canvas_zoom(self) -> None:
        normal = Qt.WindowState.WindowNoState
        with patch.object(self.window, "_schedule_canvas_fit") as schedule_fit:
            self.window._handle_canvas_window_state_change(normal, normal)
        schedule_fit.assert_not_called()

    def test_empty_preview_tab_returns_to_the_last_editing_tab(self) -> None:
        self.window.bottom_tabs.setCurrentIndex(1)
        with patch.object(QMessageBox, "warning") as warning:
            self.window.bottom_tabs.setCurrentIndex(2)

        warning.assert_called_once()
        self.assertEqual(self.window.bottom_tabs.currentIndex(), 1)
        self.assertIsNone(self.window._inline_preview)
        self.assertIs(
            self.window.inspector_stack.currentWidget(), self.window.inspector,
        )

    def test_preview_renderer_setting_is_deferred_until_next_program_start(self) -> None:
        original = self.window.settings_service.current
        session_backend = self.window._preview_backend_for_session
        selected_backend = "cpu" if session_backend == "gpu_layers" else "gpu_layers"
        try:
            self.window.settings_service.save(replace(
                original, preview_backend=selected_backend,
            ))

            self.assertEqual(
                self.window.settings_service.current.preview_backend,
                selected_backend,
            )
            self.assertEqual(
                self.window._preview_backend_for_session, session_backend,
            )
        finally:
            self.window.settings_service.save(original)

    def test_gpu_session_prepares_opengl_before_main_window_is_shown(self) -> None:
        if (
            self.window._preview_backend_for_session == "gpu_layers"
            and GPU_TEXTURE_SURFACE_AVAILABLE
        ):
            self.assertIsNotNone(self.window._preview_gpu_composition_anchor)
            self.assertIs(
                self.window.canvas_stack.currentWidget(), self.window.canvas,
            )
        else:
            self.assertIsNone(self.window._preview_gpu_composition_anchor)

    def test_embedded_preview_keeps_complete_transport_controls(self) -> None:
        preview = ExportPreviewDialog(
            self.window.canvas.scene_model,
            [PlaylistTrack("preview.wav", "Preview", duration_seconds=10.0)],
            self.window.translator,
            parent=self.window,
            source_store=self.window.store,
            embedded=True,
            preferred_backend="cpu",
        )

        self.assertTrue(preview.embedded)
        self.assertEqual(preview.windowType(), Qt.WindowType.Widget)
        self.assertEqual(preview.minimumWidth(), 0)
        self.assertTrue(preview.play_button.isCheckable())
        self.assertIsNotNone(preview.timeline)
        self.assertIsNotNone(preview.previous_button)
        self.assertIsNotNone(preview.next_button)
        self.assertIsNotNone(preview.volume_slider)
        self.assertFalse(hasattr(preview, "quality_combo"))
        self.assertFalse(hasattr(preview, "quality_label"))
        self.assertFalse(hasattr(preview, "gpu_check"))
        self.assertIsNotNone(preview.button_box)
        self.assertTrue(preview.error_banner.isHidden())

        controls_page = preview.build_embedded_controls_page()
        self.assertEqual(preview.objectName(), "embeddedCanvasPreview")
        self.assertEqual(controls_page.objectName(), "embeddedPreviewControls")
        self.assertEqual(preview.dialog_title_label.text(), "캔버스 미리보기")
        self.assertTrue(preview.hint_label.isHidden())
        self.assertIs(preview.now_playing_card.parentWidget(), controls_page)
        self.assertIs(preview.timeline_card.parentWidget(), controls_page)
        self.assertIs(preview.transport_card.parentWidget(), controls_page)
        self.assertTrue(preview.button_box.isHidden())
        self.assertIs(
            preview.preview_close_button.parentWidget(), preview.transport_card,
        )
        self.assertEqual(preview.preview_close_button.text(), "편집으로 돌아가기")
        self.assertEqual(
            preview.preview_close_button.objectName(), "embeddedPreviewCloseButton",
        )
        preview.transport_card.resize(900, 56)
        self.application.processEvents()
        self.assertLessEqual(
            preview.rewind_button.width(), preview.forward_button.width() + 20,
        )
        self.assertEqual(preview.layout().indexOf(preview.now_playing_card), -1)
        self.assertGreaterEqual(controls_page.layout().indexOf(preview.timeline_card), 0)

        preview.gpu_preview_enabled = True
        preview._gpu_backend_failed("simulated context loss")
        self.assertFalse(preview.gpu_preview_enabled)
        self.assertEqual(preview.preview_stack.currentIndex(), 0)
        self.assertIn("simulated context loss", preview.preview_mode_label.toolTip())
        preview._stop_preview()
        controls_page.deleteLater()
        preview.deleteLater()

    def test_preview_errors_are_visible_deduplicated_and_expandable(self) -> None:
        preview = ExportPreviewDialog(
            self.window.canvas.scene_model,
            [PlaylistTrack("preview.wav", "Preview", duration_seconds=10.0)],
            self.window.translator,
            parent=self.window,
            source_store=self.window.store,
            embedded=True,
            preferred_backend="cpu",
        )

        preview._preview_worker_failed("decoder failed at frame 42")
        self.assertFalse(preview.error_banner.isHidden())
        self.assertIn("처리하지 못했습니다", preview.error_title_label.text())
        self.assertEqual(preview.error_message_label.text(), "decoder failed at frame 42")
        self.assertEqual(len(preview._preview_error_signatures), 1)
        preview._preview_worker_failed("decoder failed at frame 42")
        self.assertEqual(len(preview._preview_error_signatures), 1)

        with patch.object(QMessageBox, "warning") as warning:
            preview._show_preview_error_details()
        warning.assert_called_once()
        self.assertIn("decoder failed at frame 42", warning.call_args.args[2])

        preview.error_dismiss_button.click()
        self.assertTrue(preview.error_banner.isHidden())
        preview._stop_preview()
        preview.deleteLater()

    def test_failed_video_probe_shows_path_in_preview_error(self) -> None:
        preview = ExportPreviewDialog(
            self.window.canvas.scene_model,
            [PlaylistTrack("preview.wav", "Preview", duration_seconds=10.0)],
            self.window.translator,
            parent=self.window,
            source_store=self.window.store,
            embedded=True,
            preferred_backend="cpu",
        )

        with patch.object(preview, "_schedule_refresh") as schedule:
            preview._store_video_duration("broken.mp4", 0.0)
        schedule.assert_called_once_with()
        self.assertFalse(preview.error_banner.isHidden())
        self.assertIn("broken.mp4", preview.error_message_label.text())
        preview._stop_preview()
        preview.deleteLater()

    def test_preview_uses_gpu_layers_by_default_when_available(self) -> None:
        preview = ExportPreviewDialog(
            self.window.canvas.scene_model,
            [PlaylistTrack("preview.wav", "Preview", duration_seconds=10.0)],
            self.window.translator,
            parent=self.window,
            source_store=self.window.store,
            embedded=True,
        )

        self.assertEqual(preview.preferred_backend, "gpu_layers")
        self.assertEqual(
            preview.gpu_preview_enabled, GPU_TEXTURE_SURFACE_AVAILABLE,
        )
        self.assertFalse(hasattr(preview, "gpu_check"))
        preview._stop_preview()
        preview.deleteLater()

    def test_preview_track_navigator_highlights_and_jumps_to_track(self) -> None:
        tracks = [
            PlaylistTrack(
                "first.wav", "First", artist="Artist A",
                duration_seconds=10.0,
            ),
            PlaylistTrack(
                "second.wav", "Second", artist="Artist B",
                duration_seconds=8.0, start_time_seconds=20.0,
            ),
        ]
        preview = ExportPreviewDialog(
            self.window.canvas.scene_model, tracks, self.window.translator,
            parent=self.window, source_store=self.window.store, embedded=True,
            preferred_backend="cpu",
        )

        self.assertEqual(preview.track_list.count(), 2)
        self.assertEqual(preview.track_list.currentRow(), 0)
        self.assertIn("First", preview.track_list.item(0).text())
        self.assertFalse(preview.performance_bar.isHidden())
        self.assertEqual(preview.frame_rate_label.toolTip(), "")
        self.assertTrue(preview.performance_scale_label.text())
        # The preview renders below final resolution; tell the user the export
        # will be sharper so a soft preview is not mistaken for a soft export.
        scale_tip = preview.performance_scale_label.toolTip()
        self.assertTrue("또렷" in scale_tip or "sharper" in scale_tip)

        second = preview.track_list.item(1)
        preview.track_list.itemDoubleClicked.emit(second)
        self.application.processEvents()

        self.assertEqual(preview.timeline.value(), 2000)
        self.assertEqual(preview.track_list.currentRow(), 1)
        self.assertEqual(preview._highlighted_track_index, 1)
        preview._stop_preview()
        preview.deleteLater()

    def test_gpu_preview_submits_ordered_layers_without_flattening_frame(self) -> None:
        preview = ExportPreviewDialog(
            self.window.canvas.scene_model,
            [PlaylistTrack("preview.wav", "Preview", duration_seconds=10.0)],
            self.window.translator,
            parent=self.window,
            source_store=self.window.store,
            embedded=True,
            preferred_backend="cpu",
        )
        submissions: list[tuple[QSize, tuple[object, ...]]] = []

        class SurfaceStub:
            def set_layers(self, size: QSize, layers: object) -> None:
                submissions.append((QSize(size), tuple(layers)))

        preview.gpu_surface = SurfaceStub()
        preview.gpu_preview_enabled = True
        with patch.object(preview.preview_label, "set_image") as cpu_present:
            preview.refresh_preview()

        self.assertEqual(len(submissions), 1)
        size, layers = submissions[0]
        self.assertFalse(size.isEmpty())
        self.assertGreaterEqual(len(layers), 1)
        self.assertEqual(layers[0].key[0], "canvas-base")
        cpu_present.assert_not_called()
        preview.gpu_surface = None
        preview.gpu_preview_enabled = False
        preview._stop_preview()
        preview.deleteLater()

    def test_simple_video_color_effects_route_to_gpu_shader(self) -> None:
        video = Source(
            SourceType.VIDEO, "Shader video", video_paths=["missing.mp4"],
            image_fit_mode="stretch", border_radius=0.0, outline_width=0.0,
            brightness=14.0, contrast=8.0, video_saturation=1.4,
        )
        self.window.store.replace([video])
        preview = ExportPreviewDialog(
            self.window.canvas.scene_model,
            [PlaylistTrack("preview.wav", "Preview", duration_seconds=10.0)],
            self.window.translator,
            parent=self.window,
            source_store=self.window.store,
            embedded=True,
            preferred_backend="cpu",
        )
        preview.gpu_surface = MagicMock()
        preview.gpu_preview_enabled = True
        preview._refresh_source_partitions()

        first = preview._configure_gpu_video_color_filters(
            {video.id}, False, 0.0,
        )
        item = next(
            item for item in preview.scene.items()
            if isinstance(item, SourceItem) and item.source.id == video.id
        )
        self.assertTrue(item._video_gpu_color_filter)
        self.assertEqual(first, {})
        item._video_applied_gpu_color_filter = True
        filters = preview._configure_gpu_video_color_filters(
            {video.id}, False, 0.0,
        )

        self.assertEqual(filters[video.id].brightness, 14.0)
        self.assertEqual(filters[video.id].contrast, 8.0)
        self.assertEqual(filters[video.id].saturation, 1.4)
        preview.gpu_surface = None
        preview.gpu_preview_enabled = False
        preview._stop_preview()
        preview.deleteLater()

    def test_styled_video_falls_back_but_z_banded_video_uses_gpu_filter(self) -> None:
        video = Source(
            SourceType.VIDEO, "Styled video", video_paths=["missing.mp4"],
            image_fit_mode="stretch", border_radius=12.0, brightness=10.0,
        )
        self.window.store.replace([video])
        preview = ExportPreviewDialog(
            self.window.canvas.scene_model,
            [PlaylistTrack("preview.wav", "Preview", duration_seconds=10.0)],
            self.window.translator,
            parent=self.window,
            source_store=self.window.store,
            embedded=True,
            preferred_backend="cpu",
        )
        preview.gpu_surface = MagicMock()
        preview.gpu_preview_enabled = True
        preview._refresh_source_partitions()

        self.assertEqual(
            preview._configure_gpu_video_color_filters({video.id}, False, 0.0),
            {},
        )
        item = next(
            item for item in preview.scene.items()
            if isinstance(item, SourceItem) and item.source.id == video.id
        )
        self.assertFalse(item._video_gpu_color_filter)
        video.border_radius = 0.0
        item._video_applied_gpu_color_filter = True
        z_banded_filters = preview._configure_gpu_video_color_filters(
            {video.id}, True, 0.0,
        )
        self.assertEqual(z_banded_filters[video.id].brightness, 10.0)
        self.assertTrue(item._video_gpu_color_filter)
        preview.gpu_surface = None
        preview.gpu_preview_enabled = False
        preview._stop_preview()
        preview.deleteLater()

    def test_simple_video_frame_uses_a_direct_gpu_texture_layer(self) -> None:
        video = Source(
            SourceType.VIDEO, "Direct video", video_paths=["missing.mp4"],
            x=24.0, y=36.0, width=320.0, height=180.0, scale=1.25,
            rotation=7.0, opacity=0.8, z_index=4,
            image_fit_mode="stretch", border_radius=0.0, outline_width=0.0,
        )
        self.window.store.replace([video])
        preview = ExportPreviewDialog(
            self.window.canvas.scene_model,
            [PlaylistTrack("preview.wav", "Preview", duration_seconds=10.0)],
            self.window.translator,
            parent=self.window, source_store=self.window.store, embedded=True,
            preferred_backend="cpu",
        )
        preview.gpu_surface = MagicMock()
        preview.gpu_preview_enabled = True
        preview._refresh_source_partitions()
        item = next(
            item for item in preview.scene.items()
            if isinstance(item, SourceItem) and item.source.id == video.id
        )
        image = QImage(320, 180, QImage.Format.Format_RGBA8888)
        image.fill(QColor(20, 40, 60))
        item._video_presented_frame = image
        item._video_timeline_preview_active = True
        item._video_preview_suppressed = False

        self.assertIn(video.id, {cached.source.id for cached in preview._cached_video_items})
        self.assertTrue(item.video_preview_active)
        self.assertTrue(item.source.visible)
        self.assertEqual(item.source.image_fit_mode, "stretch")
        self.assertFalse(item.source.shadow.enabled)

        direct_ids, layers = preview._direct_gpu_video_layers(
            {video.id}, False, 0.0, {},
        )

        self.assertEqual(direct_ids, frozenset({video.id}))
        self.assertEqual(len(layers), 1)
        z_index, layer = layers[0]
        self.assertEqual(z_index, 4)
        self.assertEqual(layer.key, ("video-direct", video.id))
        self.assertEqual(layer.opacity, 0.8)
        self.assertEqual(layer.rotation, 7.0)
        self.assertEqual(layer.image.pixelColor(1, 1), QColor(20, 40, 60))
        preview.gpu_surface = None
        preview.gpu_preview_enabled = False
        preview._stop_preview()
        preview.deleteLater()

    def test_static_source_above_video_uses_cached_gpu_z_bands(self) -> None:
        video = Source(
            SourceType.VIDEO, "Lower video", video_paths=["missing.mp4"],
            x=20.0, y=20.0, width=160.0, height=90.0, z_index=0,
            image_fit_mode="stretch",
        )
        foreground = Source(
            SourceType.SHAPE, "Upper title plate", x=30.0, y=30.0,
            width=100.0, height=40.0, z_index=1,
        )
        self.window.store.replace([video, foreground])
        preview = ExportPreviewDialog(
            self.window.canvas.scene_model,
            [PlaylistTrack("preview.wav", "Preview", duration_seconds=10.0)],
            self.window.translator,
            parent=self.window, source_store=self.window.store, embedded=True,
            preferred_backend="cpu",
        )
        submissions: list[tuple[object, ...]] = []

        class SurfaceStub:
            frame_pending = False

            def set_layers(self, _size: QSize, layers: object) -> None:
                submissions.append(tuple(layers))

        preview.gpu_surface = SurfaceStub()
        preview.gpu_preview_enabled = True
        preview._refresh_source_partitions()
        item = next(
            item for item in preview.scene.items()
            if isinstance(item, SourceItem) and item.source.id == video.id
        )
        frame = QImage(160, 90, QImage.Format.Format_RGBA8888)
        frame.fill(QColor("#224466"))
        item._video_presented_frame = frame
        item._video_timeline_preview_active = True
        item._video_preview_suppressed = False
        original_capture = CanvasSnapshot.capture_track

        with (
            patch.object(preview, "_sync_video_sources"),
            patch.object(
                CanvasSnapshot, "capture_track", side_effect=original_capture,
            ) as capture,
        ):
            preview.refresh_preview()
            first_capture_count = capture.call_count
            preview.refresh_preview()
            submissions_after_duplicate_tick = len(submissions)
            item._video_presented_revision += 1
            preview.refresh_preview()

        self.assertEqual(first_capture_count, 2)
        self.assertEqual(capture.call_count, first_capture_count)
        self.assertEqual(submissions_after_duplicate_tick, 1)
        self.assertEqual(len(submissions), 2)
        keys = [layer.key for layer in submissions[-1]]
        self.assertEqual(keys[0][0], "canvas-base")
        self.assertEqual(keys[1], ("video-direct", video.id))
        self.assertEqual(keys[2][0], "video-z-foreground")
        preview.gpu_surface = None
        preview.gpu_preview_enabled = False
        preview._stop_preview()
        preview.deleteLater()

    def test_gpu_filter_cpu_fallback_preserves_color_adjustments(self) -> None:
        image = QImage(8, 8, QImage.Format.Format_RGBA8888)
        image.fill(QColor(100, 120, 140, 255))
        layer = GpuPreviewLayer(
            "filtered", image, QRectF(0, 0, 8, 8),
            color_filter=GpuColorFilter(brightness=10.0, contrast=20.0),
        )

        filtered = ExportPreviewDialog._cpu_fallback_layer_image(layer)

        color = filtered.pixelColor(4, 4)
        self.assertAlmostEqual(color.red(), 119, delta=1)
        self.assertAlmostEqual(color.green(), 143, delta=1)
        self.assertAlmostEqual(color.blue(), 167, delta=1)

    @unittest.skipIf(GpuTexturePreviewSurface is None, "OpenGL preview is unavailable")
    def test_gpu_filter_quad_uses_canvas_coordinates_and_top_left_uv(self) -> None:
        vertices = GpuTexturePreviewSurface._filtered_quad_vertices(
            QRectF(0, 0, 100, 50), QRect(0, 0, 200, 100), 0.0,
        )

        self.assertEqual(len(vertices), 16)
        self.assertEqual(vertices[:4], (-1.0, 1.0, 0.0, 1.0))
        self.assertEqual(vertices[-4:], (0.0, 0.0, 1.0, 0.0))

    def test_dynamic_canvas_source_keeps_z_order_below_static_source(self) -> None:
        dynamic = Source(
            SourceType.SHAPE, "Timed lower", x=20, y=20,
            width=120, height=90, fill_color="#EF4444",
            z_index=0, timeline_start=1.0,
        )
        static = Source(
            SourceType.SHAPE, "Static upper", x=20, y=20,
            width=120, height=90, fill_color="#2563EB", z_index=1,
        )
        self.window.store.replace([dynamic, static])
        track = PlaylistTrack("preview.wav", "Preview", duration_seconds=5.0)
        preview = ExportPreviewDialog(
            self.window.canvas.scene_model, [track], self.window.translator,
            parent=self.window, source_store=self.window.store, embedded=True,
            preferred_backend="cpu",
        )

        preview.timeline.setValue(200)
        self.application.processEvents()

        self.assertTrue(
            preview._canvas_dynamic_requires_z_composition({dynamic.id}, 2.0)
        )
        sample = round(60 * preview._active_render_scale)
        self.assertEqual(
            preview._image.pixelColor(sample, sample).name().upper(), "#2563EB",
        )
        submissions: list[tuple[object, ...]] = []

        class SurfaceStub:
            frame_pending = False

            def set_layers(self, _size: QSize, layers: object) -> None:
                submissions.append(tuple(layers))

        preview.gpu_surface = SurfaceStub()
        preview.gpu_preview_enabled = True
        preview.refresh_preview()
        self.assertTrue(submissions)
        self.assertFalse(any(
            isinstance(layer.key, tuple) and layer.key[0] == "canvas-dynamic"
            for layer in submissions[-1]
        ))
        self.assertEqual(
            submissions[-1][0].image.pixelColor(sample, sample).name().upper(),
            "#2563EB",
        )
        preview.gpu_surface = None
        preview.gpu_preview_enabled = False
        preview._stop_preview()
        preview.deleteLater()

    def test_topmost_dynamic_canvas_source_keeps_fast_region_path(self) -> None:
        dynamic = Source(
            SourceType.SHAPE, "Timed upper", timeline_start=1.0, z_index=2,
        )
        static = Source(SourceType.SHAPE, "Static lower", z_index=1)
        self.window.store.replace([dynamic, static])
        preview = ExportPreviewDialog(
            self.window.canvas.scene_model,
            [PlaylistTrack("preview.wav", "Preview", duration_seconds=5.0)],
            self.window.translator, parent=self.window,
            source_store=self.window.store, embedded=True,
            preferred_backend="cpu",
        )
        preview._refresh_source_partitions()

        self.assertFalse(
            preview._canvas_dynamic_requires_z_composition({dynamic.id}, 2.0)
        )
        preview._stop_preview()
        preview.deleteLater()

    def test_paused_video_frame_completion_schedules_preview_refresh(self) -> None:
        video = Source(SourceType.VIDEO, "Paused clip", video_paths=["missing.mp4"])
        self.window.store.replace([video])
        preview = ExportPreviewDialog(
            self.window.canvas.scene_model,
            [PlaylistTrack("preview.wav", "Preview", duration_seconds=10.0)],
            self.window.translator,
            parent=self.window,
            source_store=self.window.store,
            embedded=True,
            preferred_backend="cpu",
        )
        item = next(
            item for item in preview.scene.items()
            if isinstance(item, SourceItem) and item.source.id == video.id
        )

        with patch.object(preview, "_schedule_refresh") as schedule:
            item.video_frame_ready.emit()
            schedule.assert_called_once_with()
            schedule.reset_mock()
            preview._playing = True
            item.video_frame_ready.emit()
            schedule.assert_not_called()

        preview._playing = False
        preview._stop_preview()
        preview.deleteLater()

    def test_video_duration_probe_is_queued_without_blocking_preview(self) -> None:
        with TemporaryDirectory() as directory:
            video_path = Path(directory) / "clip.mp4"
            second_path = Path(directory) / "second.mp4"
            video_path.touch()
            second_path.touch()
            video = Source(
                SourceType.VIDEO, "Async probe",
                video_paths=[str(video_path), str(second_path)],
            )
            self.window.store.replace([video])
            with (
                patch.object(VideoDurationProbeWorker, "start") as start,
                patch.object(PlaylistService, "_probe_duration") as probe,
            ):
                preview = ExportPreviewDialog(
                    self.window.canvas.scene_model,
                    [PlaylistTrack("preview.wav", "Preview", duration_seconds=10.0)],
                    self.window.translator,
                    parent=self.window,
                    source_store=self.window.store,
                    embedded=True,
                    preferred_backend="cpu",
                )

            probe.assert_not_called()
            start.assert_called_once_with()
            self.assertIsNotNone(preview._video_probe_worker)
            assert preview._video_probe_worker is not None
            self.assertEqual(preview._video_probe_worker.path, str(video_path))
            self.assertEqual(preview._video_probe_queue, [str(second_path)])
            self.assertNotIn(str(video_path), preview._video_duration_cache)

            with patch.object(preview, "_schedule_refresh") as schedule:
                preview._store_video_duration(str(video_path), 4.25)
                schedule.assert_called_once_with()
            self.assertEqual(preview._video_duration_cache[str(video_path)], 4.25)

            preview._stop_preview()
            preview.deleteLater()

    def test_video_duration_probe_worker_runs_off_the_ui_thread(self) -> None:
        ui_thread = threading.get_ident()
        probe_threads: list[int] = []

        def probe(_path: Path) -> float:
            probe_threads.append(threading.get_ident())
            return 3.5

        worker = VideoDurationProbeWorker("clip.mp4")
        with patch.object(PlaylistService, "_probe_duration", side_effect=probe):
            worker.start()
            self.assertTrue(worker.wait(3000))

        self.assertEqual(len(probe_threads), 1)
        self.assertNotEqual(probe_threads[0], ui_thread)
        worker.deleteLater()

    def test_stop_preview_detaches_a_slow_worker_without_blocking_the_ui_thread(
        self,
    ) -> None:
        """FFprobe can block a worker's run() for seconds with no way to
        interrupt it early; leaving Preview must never wait that out on the
        UI thread (see the freeze this fixed)."""
        import time

        release = threading.Event()

        def slow_probe(_path: Path) -> float:
            release.wait(timeout=3.0)
            return 3.5

        worker = VideoDurationProbeWorker("clip.mp4")
        try:
            with patch.object(PlaylistService, "_probe_duration", side_effect=slow_probe):
                worker.start()
                time.sleep(0.05)
                self.assertTrue(worker.isRunning(), "worker did not even start")
                started = time.perf_counter()
                ExportPreviewDialog._finish_or_detach_worker(worker)
                elapsed = time.perf_counter() - started
                self.assertTrue(worker.isRunning())
                self.assertLess(
                    elapsed, 0.5,
                    "detaching a still-running worker must not block the caller",
                )
        finally:
            release.set()
            worker.wait(3000)

    def test_preview_proxy_replaces_decoder_path_but_preserves_export_source(self) -> None:
        with TemporaryDirectory(prefix="preview-proxy-routing-") as raw_directory:
            directory = Path(raw_directory)
            original = directory / "original.mp4"
            proxy_path = directory / "proxy.mp4"
            original.write_bytes(b"original")
            proxy_path.write_bytes(b"proxy")
            video = Source(
                SourceType.VIDEO, "Proxy routed video",
                video_paths=[str(original)], video_repeat_mode="loop_one",
            )
            self.window.store.replace([video])
            preview = ExportPreviewDialog(
                self.window.canvas.scene_model,
                [PlaylistTrack(
                    "preview.wav", "Preview", duration_seconds=10.0,
                )],
                self.window.translator,
                parent=self.window, source_store=self.window.store,
                embedded=True, preferred_backend="cpu",
            )
            preview._refresh_source_partitions()
            preview._video_duration_cache[str(original)] = 4.0
            preview._video_proxy_paths[str(original.resolve())] = str(proxy_path)
            item = preview._cached_video_items[0]
            preview.timeline.blockSignals(True)
            preview.timeline.setValue(125)
            preview.timeline.blockSignals(False)

            with patch.object(item, "set_video_preview_position") as position:
                preview._sync_video_sources(
                    preview.tracks[0], 1.25, track_start=0.0,
                )

            self.assertEqual(position.call_args.args[:2], (str(proxy_path), 1.25))
            self.assertEqual(video.video_paths, [str(original)])
            preview._stop_preview()
            preview.deleteLater()

    def test_ready_preview_proxy_is_reused_without_starting_worker(self) -> None:
        with TemporaryDirectory(prefix="preview-proxy-cache-hit-") as raw_directory:
            directory = Path(raw_directory)
            original = directory / "original.mp4"
            ffmpeg = directory / "ffmpeg.exe"
            original.write_bytes(b"large-original")
            ffmpeg.touch()
            cache = PreviewProxyCache(directory / "cache")
            cached = cache.proxy_path(original)
            cached.parent.mkdir(parents=True)
            cached.write_bytes(b"cached-proxy")
            preview = ExportPreviewDialog(
                self.window.canvas.scene_model,
                [PlaylistTrack(
                    "preview.wav", "Preview", duration_seconds=10.0,
                )],
                self.window.translator,
                parent=self.window, source_store=self.window.store,
                embedded=True, preferred_backend="cpu",
            )
            preview._preview_proxy_ffmpeg = ffmpeg
            preview._preview_proxy_cache = cache

            preview._request_video_proxies([str(original)])

            self.assertEqual(
                preview._preview_video_path(str(original)), str(cached),
            )
            self.assertIsNone(preview._video_proxy_worker)
            self.assertEqual(preview._video_proxy_queue, [])
            preview._stop_preview()
            preview.deleteLater()

    def test_preview_prefetches_video_metadata_for_upcoming_tracks_once(self) -> None:
        source = Source(
            SourceType.VIDEO, "Per-track", video_timing_mode="track",
        )
        tracks = [
            PlaylistTrack(
                f"song-{index}.wav", f"Song {index}", duration_seconds=2.0,
                video_paths=[f"clip-{index}.mp4"],
            )
            for index in range(4)
        ]
        request = MagicMock()
        preview = SimpleNamespace(
            _last_video_prefetch_track_index=-1,
            _cached_video_items=(SimpleNamespace(source=source),),
            tracks=tracks,
            _request_video_durations=request,
        )

        ExportPreviewDialog._prefetch_upcoming_video_durations(preview, 0)
        ExportPreviewDialog._prefetch_upcoming_video_durations(preview, 0)

        request.assert_called_once_with([
            "clip-0.mp4", "clip-1.mp4", "clip-2.mp4",
        ])

    def test_inactive_video_and_running_decoder_commands_are_idempotent(self) -> None:
        source = Source(SourceType.VIDEO, "Video")
        item = SourceItem(source)
        item.release_video_decoder()

        class FakePlayer:
            def __init__(self) -> None:
                self.state = QMediaPlayer.PlaybackState.StoppedState
                self.stop_calls = 0
                self.play_calls = 0
                self.rate_calls = 0

            def playbackState(self):  # type: ignore[no-untyped-def]
                return self.state

            def stop(self) -> None:
                self.stop_calls += 1
                self.state = QMediaPlayer.PlaybackState.StoppedState

            def play(self) -> None:
                self.play_calls += 1
                self.state = QMediaPlayer.PlaybackState.PlayingState

            def pause(self) -> None:
                self.state = QMediaPlayer.PlaybackState.PausedState

            def setPlaybackRate(self, _rate: float) -> None:
                self.rate_calls += 1

        player = FakePlayer()
        item._video_player = player  # type: ignore[assignment]
        try:
            item.set_video_preview_position(None)
            item.set_video_preview_position(None)
            self.assertEqual(player.stop_calls, 1)

            item._video_timeline_preview_active = True
            item.set_video_preview_playback(True, 1.25)
            item.set_video_preview_playback(True, 1.25)
            self.assertEqual(player.play_calls, 1)
            self.assertEqual(player.rate_calls, 1)
        finally:
            item._video_player = None
            item.release_video_decoder()

    def test_visualizer_worker_uses_original_analysis_index_for_active_subset(self) -> None:
        first = VisualizerOverlay(0, 0, 32, 20, "bars", "#FFFFFF")
        second = VisualizerOverlay(0, 0, 32, 20, "wave", "#FFFFFF")
        analysis = {
            "levels": [[1.0]],
            "waveform": [[2.0]],
            "processed": ([[11.0]], [[22.0]]),
            "meters": (None, None),
        }
        worker = OverlayFrameWorker(
            "track", 30, 1, 0, 1, (second,), (1,), analysis,
        )
        emitted: list[tuple[int, ...]] = []
        worker.ready.connect(
            lambda _track, _fps, _generation, signature, _frames:
            emitted.append(signature)
        )
        image = QImage(2, 2, QImage.Format.Format_ARGB32)
        image.fill(QColor("#FFFFFF"))
        with patch(
            "app.dialogs.export_preview_dialog.PythonVisualizerRenderer.preview_image",
            return_value=image,
        ) as render:
            worker.run()

        self.assertEqual(render.call_args.args[3], [22.0])
        self.assertEqual(emitted, [(1,)])
        worker.deleteLater()

    def test_visualizer_cache_separates_equal_count_active_effect_sets(self) -> None:
        track = PlaylistTrack("preview.wav", "Preview", duration_seconds=10.0)
        preview = ExportPreviewDialog(
            self.window.canvas.scene_model, [track], self.window.translator,
            parent=self.window, source_store=self.window.store, embedded=True,
            preferred_backend="cpu",
        )
        first = QImage(2, 2, QImage.Format.Format_ARGB32)
        first.fill(QColor("#FF0000"))
        second = QImage(2, 2, QImage.Format.Format_ARGB32)
        second.fill(QColor("#0000FF"))
        preview._overlay_frame_cache[(track.id, (0,), 5)] = (first,)
        preview._overlay_frame_cache[(track.id, (1,), 5)] = (second,)

        first_layers = preview._overlay_layers_for_frame(track.id, 5, (0,))
        second_layers = preview._overlay_layers_for_frame(track.id, 5, (1,))
        missing_layers = preview._overlay_layers_for_frame(track.id, 6, (2,))

        self.assertEqual(first_layers[0].pixelColor(0, 0), QColor("#FF0000"))
        self.assertEqual(second_layers[0].pixelColor(0, 0), QColor("#0000FF"))
        self.assertEqual(missing_layers, ())
        preview._stop_preview()
        preview.deleteLater()

    def test_gpu_z_band_composition_preserves_canvas_layer_order(self) -> None:
        track = PlaylistTrack(
            "preview.wav", "Preview", duration_seconds=10.0,
        )
        preview = ExportPreviewDialog(
            self.window.canvas.scene_model, [track], self.window.translator,
            parent=self.window, source_store=self.window.store, embedded=True,
            preferred_backend="cpu",
        )
        preview._base_image = QImage(64, 36, QImage.Format.Format_RGBA8888)
        preview._base_image.fill(QColor("#101820"))
        overlay = VisualizerOverlay(
            4, 3, 12, 8, "bars", "#FFFFFF", z_index=0.5, rotation=15,
        )
        overlay_image = QImage(12, 8, QImage.Format.Format_RGBA8888)
        overlay_image.fill(QColor(255, 255, 255, 128))

        def captured(*_args: object, **kwargs: object) -> QImage:
            image = QImage(64, 36, QImage.Format.Format_RGBA8888)
            image.fill(
                QColor(0, 0, 0, 0)
                if kwargs.get("transparent") else QColor("#101820")
            )
            return image

        with (
            patch.object(CanvasSnapshot, "z_bands", return_value=[(None, 1.0), (1.0, None)]),
            patch.object(CanvasSnapshot, "capture_track", side_effect=captured),
        ):
            layers = preview._gpu_z_band_layers(
                track, 0, 0.0, 0.0, None, 1.0, 0.0,
                [overlay], (overlay_image,),
            )

        self.assertEqual(layers[0].key[0], "z-base")
        self.assertEqual(layers[1].key, ("audio-overlay", 0))
        self.assertEqual(layers[2].key[0], "z-foreground")
        self.assertEqual(layers[1].rotation, 15)
        preview._stop_preview()
        preview.deleteLater()

    def test_gpu_audio_layers_wait_for_a_complete_async_overlay_frame(self) -> None:
        preview = ExportPreviewDialog(
            self.window.canvas.scene_model,
            [PlaylistTrack("preview.wav", "Preview", duration_seconds=10.0)],
            self.window.translator,
            parent=self.window,
            source_store=self.window.store,
            embedded=True,
            preferred_backend="cpu",
        )
        overlays = [
            VisualizerOverlay(0, 0, 16, 8, "bars", "#FFFFFF"),
            VisualizerOverlay(20, 0, 16, 8, "wave", "#FFFFFF"),
        ]
        image = QImage(16, 8, QImage.Format.Format_RGBA8888)
        image.fill(QColor("#FFFFFF"))

        self.assertEqual(preview._gpu_audio_layers(overlays, ()), [])
        self.assertEqual(preview._gpu_audio_layers(overlays, (image,)), [])
        self.assertEqual(
            len(preview._gpu_audio_layers(overlays, (image, image))), 2,
        )
        preview._stop_preview()
        preview.deleteLater()

    def test_gpu_preview_defers_render_while_previous_frame_is_pending(self) -> None:
        preview = ExportPreviewDialog(
            self.window.canvas.scene_model,
            [PlaylistTrack("preview.wav", "Preview", duration_seconds=10.0)],
            self.window.translator,
            parent=self.window,
            source_store=self.window.store,
            embedded=True,
            preferred_backend="cpu",
        )

        class BusySurfaceStub:
            frame_pending = True

        preview.gpu_surface = BusySurfaceStub()
        preview.gpu_preview_enabled = True
        with patch.object(CanvasSnapshot, "capture_track") as capture:
            preview.refresh_preview()

        capture.assert_not_called()
        self.assertTrue(preview._gpu_refresh_deferred)
        with patch.object(preview, "_schedule_refresh") as schedule:
            preview._gpu_frame_presented()
        schedule.assert_called_once()
        self.assertFalse(preview._gpu_refresh_deferred)
        preview.gpu_surface = None
        preview.gpu_preview_enabled = False
        preview._stop_preview()
        preview.deleteLater()

    def test_gpu_preview_adapts_render_scale_without_changing_base_quality(self) -> None:
        preview = ExportPreviewDialog(
            self.window.canvas.scene_model,
            [PlaylistTrack("preview.wav", "Preview", duration_seconds=10.0)],
            self.window.translator,
            parent=self.window,
            source_store=self.window.store,
            embedded=True,
            preferred_backend="cpu",
        )

        class Stats:
            dropped_pending_frames = 0
            texture_uploads = 0
            texture_reuses = 0
            uploaded_bytes = 0
            texture_evictions = 0
            cached_textures = 0
            allocated_bytes = 0
            texture_budget_bytes = 256 * 1024 * 1024

        class SurfaceStub:
            upload_stats = Stats()

        class ClockStub:
            def elapsed(self) -> int:
                return 600

            def restart(self) -> None:
                pass

        selected_scale = preview.preview_render_scale
        preview.gpu_surface = SurfaceStub()
        preview.gpu_preview_enabled = True
        preview._playing = True
        preview._frame_stats_clock = ClockStub()
        preview._base_image = QImage(64, 36, QImage.Format.Format_RGBA8888)
        for _ in range(2):
            preview._presented_frames = 5
            preview._record_presented_frame()

        self.assertEqual(preview.preview_render_scale, selected_scale)
        self.assertLess(preview._active_render_scale, selected_scale)
        self.assertTrue(preview._base_image.isNull())
        self.assertIn("렌더 85%", preview.performance_scale_label.text())
        preview.gpu_surface = None
        preview.gpu_preview_enabled = False
        preview._stop_preview()
        preview.deleteLater()

    def test_gpu_watchdog_retries_once_then_falls_back_after_second_stall(self) -> None:
        preview = ExportPreviewDialog(
            self.window.canvas.scene_model,
            [PlaylistTrack("preview.wav", "Preview", duration_seconds=10.0)],
            self.window.translator,
            parent=self.window,
            source_store=self.window.store,
            embedded=True,
            preferred_backend="cpu",
        )
        surface = MagicMock()
        preview.gpu_surface = surface
        preview.gpu_preview_enabled = True
        preview._gpu_health.frame_queued(1.0)

        with (
            patch.object(preview, "isVisible", return_value=True),
            patch.object(preview, "_gpu_backend_failed") as fallback,
        ):
            preview._gpu_watchdog_timeout()
            surface.update.assert_called_once()
            fallback.assert_not_called()
            self.assertEqual(preview._gpu_health.stats.total_stalls, 1)

            preview._gpu_watchdog_timeout()
            fallback.assert_called_once()
            self.assertIn("timed out twice", fallback.call_args.args[0])

        preview._gpu_watchdog.stop()
        preview.gpu_surface = None
        preview.gpu_preview_enabled = False
        preview._stop_preview()
        preview.deleteLater()

    def test_animation_preview_button_requires_configured_animation(self) -> None:
        source = self.window.store.sources()[0]
        self.window.store.update(
            source.id, animation_in="none", animation_out="none"
        )
        self.window.store.select(source.id)
        self.assertFalse(self.window.inspector.animation_preview_button.isEnabled())
        self.window.store.update(source.id, animation_in="fade")
        self.assertTrue(self.window.inspector.animation_preview_button.isEnabled())

    def test_animation_inspector_offers_pop_and_rotate(self) -> None:
        values = [
            self.window.inspector.animation_in_combo.itemData(index)
            for index in range(self.window.inspector.animation_in_combo.count())
        ]
        self.assertIn("pop", values)
        self.assertIn("rotate", values)

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
            AppSettings(
                preview_backend="cpu", work_mode=WORK_MODE_MAX_SPEED,
            ), 1, 60.0, self.window.translator,
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
            self.assertEqual(dialog.app_settings.preview_backend, "cpu")
            self.assertEqual(dialog.app_settings.work_mode, WORK_MODE_MAX_SPEED)
        finally:
            dialog.close()

    def test_export_dialog_starts_with_beginner_recommended_mode(self) -> None:
        dialog = ExportSettingsDialog(
            AppSettings(), 3, 180.0, self.window.translator,
            Path("beginner-export.mp4"), canvas_size=(1920, 1080),
        )
        try:
            self.assertEqual(dialog.quality_mode_combo.currentData(), "balanced")
            self.assertFalse(dialog.advanced_group.isHidden())
            self.assertTrue(dialog.storage_group.isHidden())
            self.assertIn("권장", dialog.quality_mode_combo.currentText())
            self.assertIn("예상 작업량", dialog.workload_label.text())
            self.assertIn("권장", dialog.quality_description_label.text())
            settings = dialog.app_settings
            self.assertEqual(settings.video_codec, AUTO_VIDEO_ENCODER)
            self.assertEqual(
                (settings.crf, settings.preset, settings.audio_bitrate),
                ExportSettingsDialog.QUALITY_PROFILES["balanced"],
            )
            self.assertIn("예상 결과 영상", dialog.storage_estimate_label.text())
            self.assertIn("작업 중 최대 필요 공간", dialog.storage_estimate_label.text())
            self.assertGreater(dialog.storage_disk_bar.maximum(), 0)
        finally:
            dialog.close()

    def test_export_dialog_uses_compact_two_column_layout(self) -> None:
        dialog = ExportSettingsDialog(
            AppSettings(), 12, 754.0, self.window.translator,
            Path("layout-export.mp4"), canvas_size=(1920, 1080),
        )
        try:
            dialog.show()
            self.application.processEvents()
            render_geometry = dialog.render_group.geometry()
            quality_geometry = dialog.quality_group.geometry()
            output_geometry = dialog.output_group.geometry()

            self.assertLessEqual(
                abs(render_geometry.top() - quality_geometry.top()), 2,
            )
            self.assertLess(render_geometry.right(), quality_geometry.left())
            self.assertLess(output_geometry.bottom(), render_geometry.top())
            self.assertGreaterEqual(dialog.width(), 760)
            self.assertLess(dialog.height(), dialog.width())

            dialog.advanced_check.setChecked(False)
            self.application.processEvents()
            compact_height = dialog.height()
            dialog.advanced_check.setChecked(True)
            self.application.processEvents()
            self.assertFalse(dialog.advanced_group.isHidden())
            self.assertGreaterEqual(dialog.height(), compact_height)
        finally:
            dialog.close()

    def test_export_quality_modes_apply_plain_language_tradeoffs(self) -> None:
        dialog = ExportSettingsDialog(
            AppSettings(), 1, 60.0, self.window.translator,
            Path("quality-mode.mp4"), canvas_size=(1920, 1080),
        )
        try:
            dialog.quality_mode_combo.setCurrentIndex(
                dialog.quality_mode_combo.findData("fast")
            )
            self.assertEqual(
                (dialog.crf_spin.value(), dialog.preset_combo.currentText(),
                 dialog.audio_bitrate_combo.currentText()),
                ExportSettingsDialog.QUALITY_PROFILES["fast"],
            )
            self.assertIn("빠르게", dialog.quality_description_label.text())

            dialog.quality_mode_combo.setCurrentIndex(
                dialog.quality_mode_combo.findData("high")
            )
            self.assertEqual(
                (dialog.crf_spin.value(), dialog.preset_combo.currentText(),
                 dialog.audio_bitrate_combo.currentText()),
                ExportSettingsDialog.QUALITY_PROFILES["high"],
            )
            self.assertIn("파일이 커", dialog.quality_description_label.text())

            dialog.advanced_check.setChecked(True)
            dialog.crf_spin.setValue(17)
            self.assertEqual(dialog.quality_mode_combo.currentData(), "custom")
            self.assertFalse(dialog.advanced_group.isHidden())
        finally:
            dialog.close()

    def test_export_custom_gpu_defaults_reveal_advanced_settings(self) -> None:
        dialog = ExportSettingsDialog(
            AppSettings(video_codec="h264_nvenc"), 1, 60.0,
            self.window.translator, Path("gpu-export.mp4"),
        )
        try:
            self.assertEqual(dialog.quality_mode_combo.currentData(), "custom")
            self.assertTrue(dialog.advanced_check.isChecked())
            self.assertFalse(dialog.advanced_group.isHidden())
            self.assertIn("GPU", dialog.quality_description_label.text())
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

    def test_export_existing_file_accepts_native_yes_button_value(self) -> None:
        with TemporaryDirectory(prefix="pvs-export-overwrite-") as raw_directory:
            output = Path(raw_directory) / "existing.mp4"
            output.write_bytes(b"existing video")
            dialog = ExportSettingsDialog(
                AppSettings(), 1, 60.0, self.window.translator, output,
            )
            try:
                with patch.object(
                    QMessageBox, "question",
                    return_value=QMessageBox.StandardButton.Yes.value,
                ) as confirmation:
                    dialog._accept_if_valid()

                confirmation.assert_called_once()
                self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
                self.assertEqual(dialog.output_path, output.resolve())
            finally:
                dialog.close()

    def test_export_existing_file_no_keeps_settings_dialog_open(self) -> None:
        with TemporaryDirectory(prefix="pvs-export-no-overwrite-") as raw_directory:
            output = Path(raw_directory) / "existing.mp4"
            output.write_bytes(b"existing video")
            dialog = ExportSettingsDialog(
                AppSettings(), 1, 60.0, self.window.translator, output,
            )
            try:
                with patch.object(
                    QMessageBox, "question",
                    return_value=QMessageBox.StandardButton.No,
                ):
                    dialog._accept_if_valid()

                self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
                self.assertTrue(output.is_file())
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

    def test_track_details_dialog_saves_edited_project_metadata_only_on_accept(self) -> None:
        track = PlaylistTrack(
            "track.wav", "Original title", artist="Original artist",
            album="Original album", duration_seconds=20.0,
        )
        dialog = TrackDetailsDialog(track, self.window.translator, self.window)
        try:
            dialog.title_edit.setText("  Edited title  ")
            dialog.artist_edit.setText("Edited artist")
            dialog.album_edit.setText("Edited album")

            self.assertEqual(track.title, "Original title")
            dialog._accept()

            self.assertEqual(dialog.selected_title, "Edited title")
            self.assertEqual(dialog.selected_artist, "Edited artist")
            self.assertEqual(dialog.selected_album, "Edited album")
            self.assertEqual(track.title, "Original title")
        finally:
            dialog.close()

    def test_track_settings_updates_playlist_metadata_after_save(self) -> None:
        track = PlaylistTrack(
            "track.wav", "Before", artist="Before artist", album="Before album",
        )
        self.window.playlist_service.replace([track])
        dialog = MagicMock()
        dialog.exec.return_value = QDialog.DialogCode.Accepted
        dialog.selected_title = "After"
        dialog.selected_artist = "After artist"
        dialog.selected_album = "After album"
        dialog.selected_cover_path = ""
        dialog.selected_lyrics_path = ""
        dialog.selected_lyrics = []
        dialog.selected_timing_offset = 0.0

        with patch("app.ui.main_window.TrackDetailsDialog", return_value=dialog):
            self.window._show_track_details(track.id)

        updated = self.window.playlist_service.tracks[0]
        self.assertEqual(
            (updated.title, updated.artist, updated.album),
            ("After", "After artist", "After album"),
        )

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
        with TemporaryDirectory() as directory:
            audio_path = Path(directory) / "generator-existing-song.wav"
            audio_path.write_bytes(b"test")
            candidate = AudioImportCandidate(PlaylistTrack(
                str(audio_path), "Loaded title", "Loaded artist",
                duration_seconds=8.0,
            ))
            with (
                patch.object(PlaylistService, "inspect_files", return_value=[candidate]),
                patch("app.dialogs.lrc_generator_dialog.QMediaPlayer.setSource"),
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
                    self.assertEqual(dialog.audio_path, str(audio_path.resolve()))
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
        with TemporaryDirectory() as directory:
            audio_path = Path(directory) / "wizard-song.wav"
            audio_path.write_bytes(b"test")
            candidate = AudioImportCandidate(PlaylistTrack(
                str(audio_path), "Wizard song", duration_seconds=8.0,
            ))
            with (
                patch.object(PlaylistService, "inspect_files", return_value=[candidate]),
                patch("app.dialogs.lrc_generator_dialog.QMediaPlayer.setSource"),
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
                    dialog.done(QDialog.DialogCode.Rejected)

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
                    "Visible title", "Visible artist", "",
                    "C:/Music/long folder/song.m4a", "02:05",
                ],
            )
            self.assertEqual(dialog.album_edit.placeholderText(), "—")
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

    def test_track_details_uses_information_and_lyrics_tabs_and_edits_cover(self) -> None:
        with TemporaryDirectory(prefix="playlist-track-cover-") as raw_directory:
            directory = Path(raw_directory)
            cover_path = directory / "custom-cover.png"
            cover = QImage(180, 180, QImage.Format.Format_ARGB32)
            cover.fill(QColor("#7C3AED"))
            self.assertTrue(cover.save(str(cover_path)))
            track = PlaylistTrack("missing-audio.mp3", "Cover track")
            dialog = TrackDetailsDialog(track, self.window.translator, self.window)
            try:
                self.assertEqual(dialog.windowTitle(), "곡 정보/설정")
                self.assertEqual(dialog.tabs.count(), 3)
                self.assertEqual(dialog.tabs.tabText(0), "곡 정보")
                self.assertEqual(dialog.tabs.tabText(1), "가사 설정")
                self.assertEqual(dialog.tabs.tabText(2), "이 곡의 영상")
                self.assertTrue(dialog.info_tab.isAncestorOf(dialog.info_group))
                self.assertTrue(dialog.lyrics_tab.isAncestorOf(dialog.lyrics_group))
                self.assertTrue(dialog.lyrics_tab.isAncestorOf(dialog.playback_group))

                with patch.object(
                    QFileDialog, "getOpenFileName",
                    return_value=(str(cover_path), "Images (*.png)"),
                ):
                    dialog._choose_cover()
                self.assertEqual(dialog.selected_cover_path, str(cover_path.resolve()))
                self.assertFalse(dialog.cover_preview.pixmap().isNull())
                self.assertEqual(dialog.cover_source_label.text(), cover_path.name)
                self.assertTrue(dialog.reset_cover_button.isEnabled())

                dialog._reset_cover()
                self.assertEqual(dialog.selected_cover_path, "")
                self.assertFalse(dialog.reset_cover_button.isEnabled())
            finally:
                dialog.close()

    def test_track_details_can_attach_lyrics_from_project_content(self) -> None:
        with TemporaryDirectory(prefix="pvs-track-content-lyrics-") as raw:
            lrc = Path(raw) / "my song.lrc"
            lrc.write_text("[00:02.00]From content\n", encoding="utf-8")
            self.window.project_content_service.add_paths([lrc])
            track = PlaylistTrack("my song.wav", "My Song", duration_seconds=30.0)
            self.window.playlist_service.replace([track])

            # The window feeds the dialog the project's lyrics content.
            captured: list[TrackDetailsDialog] = []
            original = TrackDetailsDialog

            with patch(
                "app.ui.main_window.TrackDetailsDialog",
                side_effect=lambda *a, **k: captured.append(original(*a, **k)) or captured[-1],
            ), patch.object(original, "exec", return_value=QDialog.DialogCode.Rejected):
                self.window._show_track_details(track.id)

            dialog = captured[0]
            try:
                self.assertEqual(dialog._content_lyrics, [("my song.lrc", str(lrc))])
                self.assertFalse(dialog.content_lyrics_button.isHidden())
                self.assertTrue(dialog._apply_lyrics_from_path(str(lrc)))
                self.assertEqual(dialog.selected_lyrics[0]["text"], "From content")
                self.assertTrue(dialog.selected_lyrics_path.endswith("my song.lrc"))
            finally:
                dialog.close()

            # An empty library hides the button.
            self.window.project_content_service.replace([])
            empty_dialog = TrackDetailsDialog(track, self.window.translator, self.window)
            try:
                self.assertTrue(empty_dialog.content_lyrics_button.isHidden())
            finally:
                empty_dialog.close()

    def test_video_settings_explain_scope_without_discarding_other_mode_media(self) -> None:
        source = Source(
            SourceType.VIDEO, "Video",
            video_timing_mode="track",
            video_repeat_mode="sequence",
            video_paths=["C:/Videos/whole-a.mp4", "C:/Videos/whole-b.mp4"],
        )
        dialog = VideoSourceDialog(source, True, self.window)
        try:
            self.assertEqual(dialog.timing.currentData(), "track")
            self.assertIn("곡마다 다른", dialog.timing.currentText())
            self.assertFalse(dialog.track_scope_panel.isHidden())
            self.assertTrue(dialog.timeline_media_group.isHidden())
            self.assertEqual(dialog.values["video_paths"], source.video_paths)

            dialog.timing.setCurrentIndex(dialog.timing.findData("timeline"))
            self.assertTrue(dialog.track_scope_panel.isHidden())
            self.assertFalse(dialog.timeline_media_group.isHidden())
            self.assertIn("현재 2개", dialog.scope_summary.text())

            dialog.repeat.setCurrentIndex(dialog.repeat.findData("once"))
            self.assertTrue(dialog.cycle_host.isHidden())
            dialog.repeat.setCurrentIndex(dialog.repeat.findData("random"))
            self.assertFalse(dialog.cycle_host.isHidden())
            self.assertEqual(dialog.values["video_repeat_mode"], "random")
        finally:
            dialog.close()

    def test_track_video_tab_explains_scope_and_supports_sequence_reordering(self) -> None:
        track = PlaylistTrack(
            "track.wav", "Track", duration_seconds=10.0,
            video_paths=["first.mp4", "second.mp4"],
        )
        dialog = TrackDetailsDialog(track, self.window.translator, self.window)
        try:
            self.assertEqual(dialog.tabs.tabText(2), "이 곡의 영상")
            self.assertIn("이 곡이 재생되는 동안만", dialog.video_scope_badge.text())
            self.assertEqual(dialog.video_count_label.text(), "2개 영상")
            dialog.video_list.setCurrentRow(1)
            dialog._move_track_video(-1)
            self.assertEqual(dialog.selected_video_paths, ["second.mp4", "first.mp4"])
            self.assertTrue(dialog.video_down_button.isEnabled())
        finally:
            dialog.close()

    def test_video_inspector_summarizes_current_media_scope(self) -> None:
        source = Source(
            SourceType.VIDEO, "Video", video_timing_mode="track",
        )
        self.window.store.add(source)
        self.window.store.select(source.id)
        self.application.processEvents()
        self.assertEqual(
            self.window.inspector._form_labels["video_settings"].text(),
            "영상 사용 범위",
        )
        self.assertIn("곡마다 다른 영상", self.window.inspector.video_settings_button.text())

        self.window.store.update(
            source.id, video_timing_mode="timeline",
            video_paths=["one.mp4", "two.mp4"],
        )
        self.application.processEvents()
        self.assertIn("전체에서 같은 영상", self.window.inspector.video_settings_button.text())
        self.assertIn("2개", self.window.inspector.video_settings_button.text())

    def test_custom_track_cover_drives_cover_and_ambient_rendering(self) -> None:
        with TemporaryDirectory(prefix="playlist-render-cover-") as raw_directory:
            cover_path = Path(raw_directory) / "render-cover.png"
            image = QImage(96, 96, QImage.Format.Format_ARGB32)
            image.fill(QColor("#16A34A"))
            self.assertTrue(image.save(str(cover_path)))

            cover = extract_track_cover("missing-audio.mp3", cover_path)
            self.assertFalse(cover.isNull())
            self.assertEqual(cover.toImage().pixelColor(20, 20), QColor("#16A34A"))
            ambient = create_cached_ambient_background(
                "missing-audio.mp3", 320, 180, 24.0, cover_path,
            )
            self.assertEqual(ambient.size(), QSize(320, 180))

    def test_ambient_album_background_flows_over_time(self) -> None:
        from PySide6.QtGui import QPainter
        from app.canvas.live_canvas import CanvasScene
        from app.canvas.source_item import SourceItem
        from app.preview.album_art import AMBIENT_FLOW_HZ
        from app.preview.canvas_snapshot import CanvasSnapshot

        with TemporaryDirectory(prefix="playlist-ambient-flow-") as directory:
            cover_path = Path(directory) / "flow-cover.png"
            art = QImage(120, 120, QImage.Format.Format_ARGB32)
            art.fill(QColor("#20308A"))
            painter = QPainter(art)
            painter.fillRect(0, 0, 60, 120, QColor("#E8532A"))
            painter.fillRect(60, 0, 60, 120, QColor("#2EC7A0"))
            painter.end()
            self.assertTrue(art.save(str(cover_path)))

            frame_a = create_cached_ambient_background(
                "missing.mp3", 240, 135, 24.0, cover_path, phase=0.0,
            )
            frame_b = create_cached_ambient_background(
                "missing.mp3", 240, 135, 24.0, cover_path, phase=6.0,
            )
            self.assertEqual(frame_a.size(), QSize(240, 135))
            self.assertNotEqual(frame_a.toImage(), frame_b.toImage())
            # Sub-step phase changes land in the same cached flow frame.
            near = create_cached_ambient_background(
                "missing.mp3", 240, 135, 24.0, cover_path,
                phase=1.0 / AMBIENT_FLOW_HZ / 4.0,
            )
            self.assertEqual(frame_a.toImage(), near.toImage())

            scene = CanvasScene()
            background = Source(
                SourceType.BACKGROUND, "BG", width=1280, height=720, z_index=-20,
                background_mode="album_art", background_ambient=True,
            )
            scene.addItem(SourceItem(background))
            track = PlaylistTrack(
                "missing.wav", "Track", duration_seconds=60.0,
                cover_path=str(cover_path),
            )
            captured_start = CanvasSnapshot.capture_track(
                scene, track, 1, 1, 0.0, elapsed_seconds=0.0,
            )
            captured_later = CanvasSnapshot.capture_track(
                scene, track, 1, 1, 0.0, elapsed_seconds=5.0,
            )
            self.assertNotEqual(captured_start, captured_later)

    def test_album_background_cross_fades_between_tracks(self) -> None:
        from app.canvas.live_canvas import CanvasScene
        from app.canvas.source_item import SourceItem
        from app.preview.canvas_snapshot import CanvasSnapshot

        with TemporaryDirectory(prefix="playlist-bg-fade-") as directory:
            first_cover = Path(directory) / "first.png"
            second_cover = Path(directory) / "second.png"
            green = QImage(64, 64, QImage.Format.Format_ARGB32)
            green.fill(QColor("#12B886"))
            red = QImage(64, 64, QImage.Format.Format_ARGB32)
            red.fill(QColor("#E03131"))
            self.assertTrue(green.save(str(first_cover)))
            self.assertTrue(red.save(str(second_cover)))

            scene = CanvasScene()
            background = Source(
                SourceType.BACKGROUND, "BG", width=320, height=180, z_index=-20,
                background_mode="album_art", background_ambient=False,
                background_track_transition=True,
                background_track_transition_seconds=1.0,
            )
            scene.addItem(SourceItem(background))
            tracks = [
                PlaylistTrack("a.wav", "A", duration_seconds=30.0,
                              cover_path=str(first_cover)),
                PlaylistTrack("b.wav", "B", duration_seconds=30.0,
                              cover_path=str(second_cover)),
            ]

            def capture(track_number, elapsed):
                return CanvasSnapshot.capture_track(
                    scene, tracks[track_number - 1], track_number, 2, 0.0,
                    elapsed_seconds=elapsed, playlist_tracks=tracks,
                )

            fade_start = capture(2, 0.0)     # blend ~ previous track's artwork
            fade_mid = capture(2, 0.5)       # a blend of both covers
            fade_done = capture(2, 1.5)      # past the window: only track B

            self.assertNotEqual(fade_start, fade_mid)
            self.assertNotEqual(fade_mid, fade_done)
            # The first track has no previous cover, so it never cross-fades.
            self.assertEqual(capture(1, 0.0), capture(1, 5.0))

            background.background_track_transition = False
            no_transition = capture(2, 0.0)
            self.assertEqual(no_transition, fade_done)
            self.assertNotEqual(no_transition, fade_start)

    def test_album_background_crossfade_is_dynamic_only_during_preview_transition(self) -> None:
        from app.canvas.live_canvas import CanvasScene
        from app.canvas.source_item import SourceItem

        scene = CanvasScene()
        background = Source(
            SourceType.BACKGROUND, "BG", width=320, height=180,
            background_mode="album_art",
            background_track_transition=True,
            background_track_transition_seconds=0.8,
        )
        scene.addItem(SourceItem(background))
        preview = ExportPreviewDialog(
            scene,
            [
                PlaylistTrack("a.wav", "A", duration_seconds=3.0),
                PlaylistTrack("b.wav", "B", duration_seconds=3.0),
            ],
            self.window.translator,
            parent=self.window,
            preferred_backend="cpu",
        )
        try:
            preview._refresh_source_partitions()
            self.assertNotIn(
                background.id,
                preview._canvas_dynamic_source_ids(0, 0.2, None),
            )
            self.assertIn(
                background.id,
                preview._canvas_dynamic_source_ids(1, 0.4, None),
            )
            self.assertNotIn(
                background.id,
                preview._canvas_dynamic_source_ids(1, 1.0, None),
            )
        finally:
            preview.close()

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

    def test_playlist_rows_have_no_checkbox_and_show_excluded_state(self) -> None:
        from PySide6.QtWidgets import QCheckBox

        from app.widgets.playlist_editor import TrackRow

        on = PlaylistTrack("a.wav", "Kept", duration_seconds=10.0)
        off = PlaylistTrack("b.wav", "Dropped", duration_seconds=10.0, enabled=False)
        self.window.playlist_service.replace([on, off])
        self.application.processEvents()
        editor = self.window.playlist_editor
        rows = [
            editor.list_widget.itemWidget(editor.list_widget.item(index))
            for index in range(editor.list_widget.count())
        ]
        from PySide6.QtWidgets import QWidget

        for row in rows:
            self.assertIsInstance(row, TrackRow)
            self.assertEqual(row.findChildren(QCheckBox), [])
        self.assertFalse(rows[0].property("trackDisabled"))
        self.assertTrue(rows[1].property("trackDisabled"))
        # An excluded row must not disable any child widget: a disabled label
        # swallows the right-click and the include/exclude menu never opens.
        self.assertTrue(all(
            child.isEnabled() for child in rows[1].findChildren(QWidget)
        ))

    def test_playlist_multi_select_and_context_menu_toggle_export_inclusion(
        self,
    ) -> None:
        tracks = [
            PlaylistTrack(f"{i}.wav", f"Track {i}", duration_seconds=10.0)
            for i in range(3)
        ]
        self.window.playlist_service.replace(tracks)
        self.application.processEvents()
        editor = self.window.playlist_editor
        for index in (0, 2):
            editor.list_widget.item(index).setSelected(True)
        self.assertEqual(len(editor._selected_ids()), 2)

        editor._toggle_selected()
        self.application.processEvents()
        by_id = {t.id: t for t in self.window.playlist_service.tracks}
        self.assertFalse(by_id[tracks[0].id].enabled)
        self.assertFalse(by_id[tracks[2].id].enabled)
        self.assertTrue(by_id[tracks[1].id].enabled)

        editor._toggle_selected()
        self.application.processEvents()
        self.assertTrue(
            all(t.enabled for t in self.window.playlist_service.tracks)
        )

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
        dialog.set_ffmpeg_catalog([FFmpegReleaseOption(
            "9.0", "n9.0-test", "latest", "Latest", "2026-08-25",
            "ffmpeg-test-win64-gpl-9.0.zip", "https://example.test/ffmpeg.zip",
            "https://example.test/checksums.sha256", "Test release notes", True,
        )])
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

    def test_export_work_mode_setting_and_layer_limits(self) -> None:
        dialog = SettingsDialog(
            AppSettings(work_mode=WORK_MODE_MAX_SPEED),
            self.window.translator.language,
            self.window.theme_service.preference,
            self.window.translator,
            self.window,
        )
        try:
            self.assertEqual(
                dialog.work_mode_combo.currentData(), WORK_MODE_MAX_SPEED,
            )
            self.assertEqual(dialog.app_settings.work_mode, WORK_MODE_MAX_SPEED)
            self.assertIn("CPU", dialog.work_mode_hint.text())
        finally:
            dialog.close()

        self.assertEqual(MainWindow._export_layer_worker_count(
            RenderSettings(work_mode=WORK_MODE_STABLE), 5,
        ), 1)
        self.assertEqual(MainWindow._export_layer_worker_count(
            RenderSettings(work_mode=WORK_MODE_AUTO), 5,
        ), 2)
        self.assertEqual(MainWindow._export_layer_worker_count(
            RenderSettings(work_mode=WORK_MODE_MAX_SPEED), 5,
        ), 3)
        self.assertEqual(MainWindow._export_layer_worker_count(
            RenderSettings(
                work_mode=WORK_MODE_MAX_SPEED,
                output_width=3840, output_height=2160,
            ), 5,
        ), 2)
        self.assertEqual(MainWindow._export_png_queue_capacity(
            RenderSettings(work_mode=WORK_MODE_STABLE),
        ), 1)
        self.assertEqual(MainWindow._export_png_queue_capacity(
            RenderSettings(work_mode=WORK_MODE_AUTO),
        ), 3)
        self.assertEqual(MainWindow._export_png_queue_capacity(
            RenderSettings(work_mode=WORK_MODE_MAX_SPEED),
        ), 5)
        self.assertEqual(MainWindow._export_png_queue_capacity(RenderSettings(
            work_mode=WORK_MODE_MAX_SPEED,
            output_width=3840, output_height=2160,
        )), 2)

    def test_export_without_ffmpeg_opens_the_ffmpeg_setup_page(self) -> None:
        with (
            patch(
                "app.ui.main_window.FFmpegRenderer",
                side_effect=FFmpegNotFoundError("missing"),
            ),
            patch.object(QMessageBox, "warning") as warning,
            patch.object(self.window, "_show_settings") as show_settings,
        ):
            self.window._export_video()

        warning.assert_called_once()
        self.assertIn("FFmpeg", warning.call_args.args[2])
        self.assertIn("설치 화면", warning.call_args.args[2])
        show_settings.assert_called_once_with(focus_ffmpeg=True)

    def test_settings_can_open_directly_on_ffmpeg_installation(self) -> None:
        dialog = SettingsDialog(
            self.window.settings_service.current,
            self.window.translator.language,
            self.window.theme_service.preference,
            self.window.translator,
            self.window,
        )
        try:
            dialog.tabs.setCurrentWidget(dialog.general_page)
            dialog.show()
            dialog.open_ffmpeg_page()
            dialog.set_ffmpeg_catalog([FFmpegReleaseOption(
                "9.0", "n9.0-test", "latest", "Latest", "2026-08-25",
                "ffmpeg-test-win64-gpl-9.0.zip", "https://example.test/ffmpeg.zip",
                "https://example.test/checksums.sha256", "Test notes", True,
            )])
            self.application.processEvents()
            self.assertIs(dialog.tabs.currentWidget(), dialog.ffmpeg_page)
            self.assertTrue(dialog.ffmpeg_download_button.hasFocus())
        finally:
            dialog.close()

    def test_ffmpeg_version_manager_labels_recommended_and_updates_button_states(self) -> None:
        dialog = SettingsDialog(
            self.window.settings_service.current,
            self.window.translator.language,
            self.window.theme_service.preference,
            self.window.translator,
            self.window,
        )
        recommended = FFmpegReleaseOption(
            "9.0", "n9-current", "latest", "Latest", "2026-08-25",
            "ffmpeg-9.zip", "https://test/9", "https://test/sums",
            "Recommended patch notes", True,
        )
        older = FFmpegReleaseOption(
            "8.1", "n8-old", "latest", "Latest", "2026-08-25",
            "ffmpeg-8.zip", "https://test/8", "https://test/sums",
            "Older patch notes", False,
        )
        try:
            dialog.set_ffmpeg_catalog([recommended, older])
            self.assertEqual(dialog.ffmpeg_about_title.text(), "FFmpeg이란?")
            self.assertIn("동영상 내보내기에는 필요", dialog.ffmpeg_about_description.text())
            self.assertIn("권장 버전", dialog.ffmpeg_about_description.text())
            self.assertEqual(dialog.ffmpeg_version_combo.itemText(0), "9.0(권장)")
            self.assertEqual(dialog.ffmpeg_version_combo.itemText(1), "8.1")
            self.assertIn("빌드 n9-current", dialog.ffmpeg_release_info.text())
            self.assertIn("Recommended patch notes", dialog.ffmpeg_release_info.toolTip())
            current = ManagedFFmpegInstallation(
                Path("managed/ffmpeg.exe"), "n9-current", "9.0", "latest"
            )
            dialog.set_managed_installation(current)
            self.assertFalse(dialog.ffmpeg_update_button.isEnabled())
            self.assertTrue(dialog.ffmpeg_reinstall_button.isEnabled())
            self.assertTrue(dialog.ffmpeg_delete_button.isEnabled())

            dialog.set_managed_installation(ManagedFFmpegInstallation(
                Path("managed/old.exe"), "n8-old", "8.1", "latest"
            ))
            self.assertTrue(dialog.ffmpeg_update_button.isEnabled())
            dialog.set_ffmpeg_installing(True)
            self.assertFalse(dialog.ffmpeg_download_button.isEnabled())
            self.assertFalse(dialog.ffmpeg_update_button.isEnabled())
            self.assertFalse(dialog.ffmpeg_reinstall_button.isEnabled())
            self.assertFalse(dialog.ffmpeg_delete_button.isEnabled())
        finally:
            dialog.close()

    def test_recommended_ffmpeg_skips_warning_but_other_versions_require_it(self) -> None:
        dialog = SettingsDialog(
            self.window.settings_service.current,
            self.window.translator.language,
            self.window.theme_service.preference,
            self.window.translator,
            self.window,
        )
        recommended = FFmpegReleaseOption(
            "9.0", "n9-current", "latest", "Latest", "2026-08-25",
            "ffmpeg-9.zip", "https://test/9", "https://test/sums",
            "Recommended release notes", True,
        )
        unsupported = FFmpegReleaseOption(
            "8.1", "n8-old", "latest", "Latest", "2026-08-25",
            "ffmpeg-8.zip", "https://test/8", "https://test/sums",
            "Compatibility warning and release notes", False,
        )
        self.window._settings_dialog = dialog
        try:
            dialog.set_ffmpeg_catalog([recommended, unsupported])
            with (
                patch.object(self.window, "_confirm_ffmpeg_release") as confirm,
                patch("app.ui.main_window.FFmpegInstallWorker.start") as start,
            ):
                self.window._start_ffmpeg_install(dialog, recommended)
                confirm.assert_not_called()
                start.assert_called_once()
            if self.window._ffmpeg_install_dialog:
                self.window._ffmpeg_install_dialog.complete(False)
            self.window._ffmpeg_install_dialog = None
            self.window._ffmpeg_install_worker = None
            dialog.set_ffmpeg_installing(False)

            with patch.object(
                self.window, "_confirm_ffmpeg_release", return_value=False,
            ) as confirm:
                self.window._start_ffmpeg_install(dialog, unsupported)
            confirm.assert_called_once_with(dialog, unsupported, "설치")
            self.assertIsNone(self.window._ffmpeg_install_worker)
        finally:
            self.window._settings_dialog = None
            self.window._ffmpeg_install_dialog = None
            self.window._ffmpeg_install_worker = None
            dialog.close()

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
            self.assertEqual(AppSettings().preview_backend, "gpu_layers")
            initial_backend = dialog.app_settings.preview_backend
            target_backend = "cpu" if initial_backend == "gpu_layers" else "gpu_layers"
            dialog.preview_backend_combo.setCurrentIndex(
                dialog.preview_backend_combo.findData(target_backend)
            )
            self.assertEqual(dialog.app_settings.preview_backend, target_backend)
            self.assertFalse(dialog.preview_backend_restart_hint.isHidden())
            self.assertIn("다시 실행", dialog.preview_backend_restart_hint.text())
            self.assertIn("미리보기 화면에서 변경할 수 없습니다", dialog.preview_backend_hint.text())
        finally:
            dialog.close()

    def test_export_notification_settings_are_individually_configurable(self) -> None:
        configured = replace(
            self.window.settings_service.current,
            export_notifications_enabled=True,
            export_notification_mode="always",
            export_notify_visuals=False,
            export_notify_audio=True,
            export_notify_effects=False,
            export_notify_encode=True,
            export_notify_complete=True,
            export_notify_failures=False,
        )
        dialog = SettingsDialog(
            configured,
            self.window.translator.language,
            self.window.theme_service.preference,
            self.window.translator,
            self.window,
        )
        try:
            self.assertTrue(dialog.export_notifications_check.isChecked())
            self.assertEqual(
                dialog.export_notification_mode_combo.currentData(), "always",
            )
            result = dialog.app_settings
            self.assertFalse(result.export_notify_visuals)
            self.assertTrue(result.export_notify_audio)
            self.assertFalse(result.export_notify_effects)
            self.assertTrue(result.export_notify_encode)
            self.assertTrue(result.export_notify_complete)
            self.assertFalse(result.export_notify_failures)

            dialog.export_notifications_check.setChecked(False)
            self.assertFalse(dialog.export_notification_mode_combo.isEnabled())
            self.assertTrue(all(
                not checkbox.isEnabled()
                for checkbox in dialog._export_notification_stage_checks()
            ))
            # Turning off the master must retain the user's per-stage choices.
            self.assertFalse(dialog.app_settings.export_notify_visuals)
            self.assertTrue(dialog.app_settings.export_notify_audio)
        finally:
            dialog.close()

    def test_export_notification_policy_respects_focus_and_each_stage(self) -> None:
        configured = AppSettings(
            export_notifications_enabled=True,
            export_notification_mode="unfocused",
            export_notify_visuals=False,
            export_notify_audio=True,
            export_notify_complete=True,
        )
        allowed = self.window._export_notification_allowed

        self.assertFalse(allowed(configured, "visuals", False))
        self.assertFalse(allowed(configured, "audio", True))
        self.assertTrue(allowed(configured, "audio", False))
        self.assertTrue(allowed(
            replace(configured, export_notification_mode="always"),
            "complete",
            True,
        ))

    def test_export_stage_notifications_are_emitted_only_once_per_phase(self) -> None:
        original = self.window.settings_service.current
        configured = replace(
            original,
            export_notifications_enabled=True,
            export_notification_mode="always",
        )
        self.window.settings_service._current = configured
        self.window._export_notified_steps.clear()
        self.window._active_export_output_path = Path("C:/Videos/result.mp4")
        try:
            with patch.object(
                self.window, "_show_system_notification", return_value=True,
            ) as notify:
                self.window._notify_export_stage("Preparing audio")
                self.window._notify_export_stage("Combining audio")
                self.window._notify_export_stage("Encoding video")
                self.window._notify_export_stage("Finalizing export")
                self.window._notify_export_stage("Complete")

            self.assertEqual(notify.call_count, 3)
            self.assertEqual(
                self.window._export_notified_steps,
                {"audio", "encode", "complete"},
            )
        finally:
            self.window.settings_service._current = original
            self.window._active_export_output_path = None

    def test_export_complete_dialog_provides_post_export_tools(self) -> None:
        with TemporaryDirectory(prefix="pvs-export-complete-") as raw_directory:
            output = Path(raw_directory) / "playlist.mp4"
            output.write_bytes(b"completed video")
            dialog = ExportCompleteDialog(
                output, self.window.translator, self.window,
            )
            try:
                with patch(
                    "app.dialogs.export_complete_dialog.QDesktopServices.openUrl",
                    return_value=True,
                ) as open_url:
                    dialog._play_video()
                    dialog._open_folder()
                self.assertEqual(open_url.call_count, 2)

                dialog._copy_path()
                self.assertEqual(QApplication.clipboard().text(), str(output.resolve()))
                self.assertIn("복사", dialog.copy_path_button.text())

                dialog._request_export_again()
                self.assertTrue(dialog.export_again_requested)
            finally:
                dialog.close()

    def test_pending_preview_renderer_setting_shows_restart_notice(self) -> None:
        active_backend = self.window._preview_backend_for_session
        pending_backend = "cpu" if active_backend == "gpu_layers" else "gpu_layers"
        dialog = SettingsDialog(
            replace(
                self.window.settings_service.current,
                preview_backend=pending_backend,
            ),
            self.window.translator.language,
            self.window.theme_service.preference,
            self.window.translator,
            self.window,
            active_preview_backend=active_backend,
        )
        try:
            self.assertFalse(dialog.preview_backend_restart_hint.isHidden())
            self.assertIn("재시작", dialog.preview_backend_restart_hint.text())
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
        service.configure(True, 180)
        self.window.show()
        self.window.left_tabs.setCurrentWidget(self.window.content_library_panel)
        content_list = self.window.content_library_panel.list
        content_list.addItems([f"Content {index}" for index in range(40)])
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

    def test_project_content_drag_returning_to_source_panel_is_rejected(self) -> None:
        panel = self.window.content_library_panel
        source_list = panel.list
        self.window.show()
        self.window.left_tabs.setCurrentWidget(panel)
        panel.show()
        self.application.processEvents()

        panel_center = panel.rect().center()
        window_point = panel.mapTo(self.window, panel_center)
        event = MagicMock()
        event.source.return_value = source_list
        event.position.return_value = QPointF(window_point)

        self.assertTrue(self.window._drag_returned_to_project_content(event))
        self.window.dragEnterEvent(event)

        event.setDropAction.assert_called_once_with(Qt.DropAction.IgnoreAction)
        event.accept.assert_called_once_with()
        event.acceptProposedAction.assert_not_called()
        event.mimeData.assert_not_called()

        outside = self.window.canvas.mapTo(
            self.window, self.window.canvas.rect().center(),
        )
        event.position.return_value = QPointF(outside)
        self.assertFalse(self.window._drag_returned_to_project_content(event))

    def test_arrow_keys_nudge_selection_and_shift_scales_the_step(self) -> None:
        source = Source(SourceType.TEXT, "Nudge me", x=100.0, y=100.0)
        self.window.store.replace([source])
        self.window.store.select(source.id)
        self.window.canvas.setFocus()
        self.application.processEvents()

        QTest.keyClick(self.window.canvas, Qt.Key.Key_Right)
        QTest.keyClick(self.window.canvas, Qt.Key.Key_Down)
        self.assertEqual((self.window.store.get(source.id).x,
                          self.window.store.get(source.id).y), (101.0, 101.0))

        QTest.keyClick(
            self.window.canvas, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier,
        )
        self.assertEqual(self.window.store.get(source.id).x, 91.0)

    def test_double_clicking_a_text_source_opens_the_expanded_editor(self) -> None:
        source = Source(SourceType.TEXT, "Editable", text="before")
        self.window.store.replace([source])
        self.application.processEvents()

        fake = MagicMock()
        fake.exec.return_value = QDialog.DialogCode.Accepted
        fake.text.return_value = "after"
        with patch("app.ui.main_window.TextEditorDialog", return_value=fake):
            self.window.canvas.edit_requested.emit(source.id)

        self.assertEqual(self.window.store.get(source.id).text, "after")

    def test_status_bar_zoom_readout_follows_the_canvas(self) -> None:
        self.window.canvas.set_zoom(1.0)
        self.application.processEvents()
        self.assertEqual(self.window.zoom_reset_button.text(), "100%")
        self.window._adjust_canvas_zoom(1.15)
        self.application.processEvents()
        self.assertEqual(self.window.zoom_reset_button.text(), "115%")

    def test_canvas_zoom_is_view_state_not_document_state(self) -> None:
        self.window._project_dirty = False
        with patch.object(self.window, "_schedule_history") as schedule:
            self.window._adjust_canvas_zoom(1.15)
            self.window.canvas.set_zoom(1.0)
        schedule.assert_not_called()
        self.assertFalse(self.window._project_dirty)

    def test_ctrl_plus_and_ctrl_equals_both_zoom_the_canvas_in(self) -> None:
        sequences = {
            action.shortcut().toString()
            for action in self.window._canvas_shortcut_actions
        }
        self.assertIn("Ctrl++", sequences)
        self.assertIn("Ctrl+=", sequences)

    def test_f2_moves_focus_to_the_inspector_name_field(self) -> None:
        source = Source(SourceType.TEXT, "Rename via F2")
        self.window.store.replace([source])
        self.window.store.select(source.id)
        self.window.show()
        self.window.canvas.setFocus()
        self.application.processEvents()
        QTest.keyClick(self.window.canvas, Qt.Key.Key_F2)
        self.application.processEvents()
        self.assertTrue(self.window.inspector.name_edit.hasFocus())

    def test_inspector_properties_are_grouped_into_tabs(self) -> None:
        source = Source(SourceType.IMAGE, "Tabbed properties")
        self.window.store.replace([source])
        self.window.store.select(source.id)
        self.window.show()
        self.application.processEvents()

        inspector = self.window.inspector
        expected_categories = {
            "name": "layout", "opacity": "shape", "fill_color": "fill",
            "blur": "filter", "animation_in": "animation", "layer": "other",
        }
        for field, category in expected_categories.items():
            self.assertEqual(inspector._field_categories[field], category)
        filter_index = inspector._tab_indices["filter"]
        inspector.property_tabs.setCurrentIndex(inspector._tab_indices["layout"])
        inspector.property_tabs.setCurrentIndex(filter_index)
        self.application.processEvents()
        self.assertEqual(inspector.property_tabs.currentIndex(), filter_index)

    def test_inspector_special_tab_sub_sections_follow_the_source_type(self) -> None:
        inspector = self.window.inspector

        # Every sectioned field is registered with its group and category.
        self.assertEqual(inspector._field_sections["visualizer_attack"],
                         ("special", "vz_response"))
        self.assertEqual(inspector._field_categories["visualizer_attack"], "special")

        groups = inspector._sections
        visualizer = Source(SourceType.AUDIO_VISUALIZER, "VZ")
        self.window.store.replace([visualizer])
        self.window.store.select(visualizer.id)
        self.application.processEvents()
        self.assertFalse(groups[("special", "vz_response")].isHidden())
        self.assertTrue(groups[("special", "tl_layout")].isHidden())

        # A group with no fields for the current source folds away entirely.
        shape = Source(SourceType.SHAPE, "Shape")
        self.window.store.replace([shape])
        self.window.store.select(shape.id)
        self.application.processEvents()
        self.assertTrue(groups[("special", "vz_response")].isHidden())

        # Collapsing a section hides its body but keeps the header.
        self.window.store.replace([visualizer])
        self.window.store.select(visualizer.id)
        self.application.processEvents()
        response = groups[("special", "vz_response")]
        response.header.setChecked(False)
        self.assertTrue(response._body.isHidden())
        self.assertFalse(response.header.isHidden())

    def test_format_bytes_scales_units(self) -> None:
        self.assertEqual(self.window._format_bytes(0), "0.0 B")
        self.assertEqual(self.window._format_bytes(2048), "2.0 KB")
        self.assertEqual(self.window._format_bytes(5 * 1024**3), "5.0 GB")

    def test_export_staging_relocates_to_output_drive_when_temp_is_short(self) -> None:
        from collections import namedtuple
        Usage = namedtuple("Usage", "total used free")
        with TemporaryDirectory(prefix="export-output-") as output_directory:
            self.window._active_export_output_path = (
                Path(output_directory) / "video.mp4"
            )
            settings = RenderSettings(
                fps=30, output_width=1920, output_height=1080,
            )

            output_root = str(Path(output_directory).resolve())

            def fake_usage(path: object) -> object:
                if str(Path(str(path)).resolve()).startswith(output_root):
                    return Usage(0, 0, 900 * 1024**3)       # output drive: 900 GB
                return Usage(0, 0, 200 * 1024**2)           # temp drive: 200 MB

            with (
                patch("app.ui.main_window.shutil.disk_usage", side_effect=fake_usage),
                patch.object(QMessageBox, "warning") as warning,
            ):
                proceed = self.window._prepare_export_staging_space(
                    settings, 120.0, 2, True, korean=False,
                )

            self.assertTrue(proceed)
            warning.assert_not_called()  # output drive has room, so no warning
            self.assertIsNotNone(self.window._export_frame_staging)
            staging = Path(self.window._export_frame_staging.name).resolve()
            self.assertTrue(
                str(staging).startswith(str(Path(output_directory).resolve()))
            )
        self.window._clear_export_frame_staging()

    def test_export_low_space_on_both_drives_asks_before_continuing(self) -> None:
        from collections import namedtuple
        Usage = namedtuple("Usage", "total used free")
        self.window._active_export_output_path = Path(
            tempfile.gettempdir()
        ) / "video.mp4"
        settings = RenderSettings(fps=30, output_width=1920, output_height=1080)
        with (
            patch(
                "app.ui.main_window.shutil.disk_usage",
                return_value=Usage(0, 0, 50 * 1024**2),
            ),
            patch.object(
                QMessageBox, "warning",
                return_value=QMessageBox.StandardButton.No,
            ) as warning,
        ):
            proceed = self.window._prepare_export_staging_space(
                settings, 120.0, 2, True, korean=False,
            )
        self.assertFalse(proceed)
        warning.assert_called_once()
        self.window._clear_export_frame_staging()

    def test_export_warns_when_a_raster_source_is_upscaled_past_its_pixels(self) -> None:
        with TemporaryDirectory(prefix="playlist-upscale-warn-") as directory:
            small = Path(directory) / "small.png"
            big = Path(directory) / "big.png"
            self.assertTrue(
                QImage(160, 90, QImage.Format.Format_ARGB32).save(str(small))
            )
            self.assertTrue(
                QImage(1920, 1080, QImage.Format.Format_ARGB32).save(str(big))
            )
            soft = Source(
                SourceType.IMAGE, "Tiny logo", width=1280.0, height=720.0,
                content_path=str(small),
            )
            crisp = Source(
                SourceType.IMAGE, "Sharp art", width=1280.0, height=720.0,
                content_path=str(big),
            )
            self.window.store.replace([soft, crisp])
            track = PlaylistTrack("song.wav", "Song", duration_seconds=2.0)

            fhd = RenderSettings(output_width=1920, output_height=1080)
            warnings = self.window._export_upscale_warnings(
                [track], fhd, 1.5, korean=False,
            )
            self.assertEqual(len(warnings), 1)
            self.assertIn("Tiny logo", warnings[0])
            self.assertNotIn("Sharp art", warnings[0])

            # Shrink the tiny image on the canvas until it fits its own pixels.
            soft.width = soft.height = 100.0
            self.window.store.replace([soft, crisp])
            self.assertEqual(
                self.window._export_upscale_warnings(
                    [track], fhd, 1.5, korean=False,
                ),
                [],
            )

    def test_dependent_inspector_fields_hide_until_their_toggle_is_active(self) -> None:
        source = Source(SourceType.IMAGE, "Conditional", width=300.0, height=200.0)
        self.window.store.replace([source])
        self.window.store.select(source.id)
        self.window.show()
        self.application.processEvents()
        inspector = self.window.inspector
        widgets = inspector._field_widgets

        # Gradient stops hide until "use gradient" is on.
        self.assertFalse(inspector._field_visibility["gradient_start"])
        self.assertFalse(inspector._field_visibility["gradient_end"])
        inspector.gradient_check.setChecked(True)
        self.application.processEvents()
        self.assertTrue(inspector._field_visibility["gradient_start"])
        inspector.gradient_check.setChecked(False)
        self.application.processEvents()
        self.assertFalse(inspector._field_visibility["gradient_start"])

        # Outline colour follows the outline width.
        self.assertFalse(inspector._field_visibility["outline_color"])
        inspector.outline_spin.setValue(4.0)
        self.application.processEvents()
        self.assertTrue(inspector._field_visibility["outline_color"])

        # Shadow sub-fields follow the shadow toggle.
        self.assertFalse(inspector._field_visibility["shadow_color"])
        inspector.shadow_check.setChecked(True)
        self.application.processEvents()
        self.assertTrue(inspector._field_visibility["shadow_color"])

        # Exit-animation duration hides while the style is "none".
        self.assertFalse(inspector._field_visibility["animation_out_duration"])
        index = inspector.animation_out_combo.findData("fade")
        inspector.animation_out_combo.setCurrentIndex(index)
        self.application.processEvents()
        self.assertTrue(inspector._field_visibility["animation_out_duration"])

    def test_delete_action_also_accepts_backspace(self) -> None:
        sequences = {
            sequence.toString() for sequence in self.window.delete_action.shortcuts()
        }
        self.assertIn("Del", sequences)
        self.assertIn("Backspace", sequences)

    def test_canvas_shows_and_clears_the_resize_readout(self) -> None:
        source = Source(SourceType.TEXT, "Readout", width=200.0, height=100.0)
        self.window.store.replace([source])
        self.application.processEvents()
        item = self.window.canvas._items[source.id]

        item.interaction_hint.emit("240 × 160")
        self.assertFalse(self.window.canvas._interaction_hint.isHidden())
        self.assertEqual(
            self.window.canvas._interaction_hint.text(), "240 × 160",
        )
        item.interaction_hint.emit("")
        self.assertTrue(self.window.canvas._interaction_hint.isHidden())

    def test_left_workspace_combines_sources_content_and_layers_as_tabs(self) -> None:
        tabs = self.window.left_tabs

        self.assertEqual(tabs.count(), 3)
        self.assertIs(tabs.widget(0), self.window.source_sidebar)
        self.assertIs(tabs.widget(1), self.window.content_library_panel)
        self.assertIs(tabs.widget(2), self.window.layer_panel)
        self.assertIs(tabs.parentWidget(), self.window.left_workspace)
        self.assertEqual(
            [tabs.tabText(index) for index in range(tabs.count())],
            ["요소", "프로젝트 콘텐츠", "레이어"],
        )

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
                list(actions), ["preview", "remove", "information", "import"],
            )
            self.assertTrue(actions["preview"].isEnabled())

            with patch.object(QMessageBox, "information") as information:
                actions["information"].trigger()
            self.assertIn(str(image_path.resolve()), information.call_args.args[2])

            actions["remove"].trigger()
            self.assertEqual(panel.list.count(), 0)

        empty_menu = panel._create_context_menu(None)
        self.assertEqual(
            [action.data() for action in empty_menu.actions()], ["import"],
        )

    def test_project_content_marks_items_already_used_in_the_project(self) -> None:
        panel = self.window.content_library_panel
        with TemporaryDirectory(prefix="pvs-content-added-") as raw_directory:
            song = Path(raw_directory) / "track one.mp3"
            song.write_bytes(b"audio placeholder")
            cover = Path(raw_directory) / "art.png"
            cover.write_bytes(b"image placeholder")
            self.window.project_content_service.add_paths([song, cover])

            def row_for(name: str):
                for index in range(panel.list.count()):
                    item = panel.list.item(index)
                    if name in item.text():
                        return item
                raise AssertionError(f"no content row for {name}")

            self.assertFalse(row_for("track one").data(Qt.ItemDataRole.UserRole + 3))
            self.assertNotIn("추가됨", row_for("art").text())

            self.window.playlist_service.add_tracks(
                [PlaylistTrack(str(song.resolve()), "Track One")]
            )
            panel._used_refresh_timer.stop()
            panel.refresh()
            self.assertTrue(row_for("track one").data(Qt.ItemDataRole.UserRole + 3))
            self.assertIn("추가됨", row_for("track one").text())
            self.assertNotIn("추가됨", row_for("art").text())

            source = Source(
                SourceType.IMAGE, "Art", content_path=str(cover.resolve()),
            )
            self.window.store.add(source)
            panel._used_refresh_timer.stop()
            panel.refresh()
            self.assertIn("추가됨", row_for("art").text())

    def test_project_content_switches_between_list_grid_and_compact_views(self) -> None:
        panel = self.window.content_library_panel
        with TemporaryDirectory(prefix="pvs-content-views-") as raw_directory:
            image_path = Path(raw_directory) / "thumbnail.png"
            image = QImage(80, 60, QImage.Format.Format_ARGB32)
            image.fill(QColor("#36A2EB"))
            self.assertTrue(image.save(str(image_path)))
            self.window.project_content_service.add_paths([image_path])
            content_id = panel.list.item(0).data(Qt.ItemDataRole.UserRole)
            panel.list.setCurrentRow(0)

            panel._set_view_mode("grid", persist=False)
            self.assertEqual(panel.view_mode, "grid")
            self.assertEqual(panel.list.viewMode(), QListView.ViewMode.IconMode)
            self.assertEqual(panel.list.iconSize(), QSize(72, 72))
            self.assertTrue(panel.view_buttons["grid"].isChecked())
            self.assertNotIn("\n", panel.list.item(0).text())
            self.assertEqual(
                panel.list.currentItem().data(Qt.ItemDataRole.UserRole), content_id,
            )

            panel._set_view_mode("compact", persist=False)
            self.assertEqual(panel.list.viewMode(), QListView.ViewMode.ListMode)
            self.assertEqual(panel.list.iconSize(), QSize(22, 22))
            self.assertEqual(panel.list.item(0).sizeHint().height(), 32)

            panel._set_view_mode("list", persist=False)
            self.assertEqual(panel.view_mode, "list")
            self.assertEqual(panel.list.iconSize(), QSize(38, 38))
            self.assertIn("\n", panel.list.item(0).text())

    def test_project_content_filters_all_supported_categories(self) -> None:
        panel = self.window.content_library_panel
        panel.filter_combo.setCurrentIndex(panel.filter_combo.findData("all"))
        with TemporaryDirectory(prefix="pvs-content-filter-") as raw_directory:
            directory = Path(raw_directory)
            paths = [
                directory / "cover.png",
                directory / "clip.mp4",
                directory / "song.mp3",
                directory / "captions.lrc",
                directory / "typeface.ttf",
            ]
            for path in paths:
                path.write_bytes(b"fixture")
            self.window.project_content_service.add_paths(paths)
            self.assertEqual(panel.filter_combo.count(), 6)
            self.assertEqual(panel.content_filter, "all")
            self.assertEqual(panel.list.count(), 5)
            self.assertEqual(panel.filter_count_label.text(), "5 / 5")

            for media_type in ("image", "video", "audio", "lyrics", "font"):
                panel.filter_combo.setCurrentIndex(
                    panel.filter_combo.findData(media_type)
                )
                self.assertEqual(panel.content_filter, media_type)
                self.assertEqual(panel.list.count(), 1)
                self.assertEqual(
                    panel.list.item(0).data(Qt.ItemDataRole.UserRole + 2),
                    media_type,
                )
                self.assertEqual(panel.filter_count_label.text(), "1 / 5")

            panel.filter_combo.setCurrentIndex(panel.filter_combo.findData("all"))
            self.assertEqual(panel.list.count(), 5)

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
            self.assertEqual(first.duration_seconds, 0.5)
            self.assertEqual(repeated.duration_seconds, 1.0)
            self.assertEqual(third.duration_seconds, 0.5)
            self.assertEqual(self.window._export_capture_count, 3)
            self.assertEqual(self.window._export_frame_index, 2)
            self.assertTrue(first.image.is_file())
            self.assertTrue(third.image.is_file())
            metrics = self.window._export_frame_metrics
            self.assertIsNotNone(metrics)
            assert metrics is not None
            expected_bytes = first.image.stat().st_size + third.image.stat().st_size
            self.assertEqual(metrics.capture_count, 3)
            self.assertEqual(metrics.unique_file_count, 2)
            self.assertEqual(metrics.reused_frame_count, 1)
            self.assertEqual(metrics.total_bytes, expected_bytes)
            self.assertEqual(metrics.stream_file_counts, {"base": 2})
            self.assertEqual(metrics.stream_bytes, {"base": expected_bytes})
            self.assertEqual((metrics.largest_width, metrics.largest_height), (48, 32))
        finally:
            self.window._clear_export_frame_staging()
        completed_metrics = self.window._last_export_frame_metrics
        self.assertIsNotNone(completed_metrics)
        assert completed_metrics is not None
        self.assertEqual(completed_metrics.capture_count, 3)
        self.assertEqual(completed_metrics.unique_file_count, 2)
        self.assertEqual(completed_metrics.reused_frame_count, 1)
        self.assertGreaterEqual(completed_metrics.elapsed_seconds, 0.0)

    def test_export_animation_sampling_matches_output_frame_rate(self) -> None:
        self.assertEqual(self.window._export_animation_sample_rate(60), 60)
        self.assertEqual(self.window._export_animation_sample_rate(50), 50)
        self.assertEqual(self.window._export_animation_sample_rate(24), 24)
        self.assertEqual(self.window._export_animation_sample_rate(10), 10)

    def test_export_preflight_failure_stops_before_canvas_capture(self) -> None:
        track = PlaylistTrack(
            file_path="missing-before-capture.mp3",
            title="Missing before capture",
            duration_seconds=1.0,
        )
        self.window.playlist_service.replace([track])
        with (
            patch("app.ui.main_window.FFmpegRenderer") as renderer_type,
            patch("app.ui.main_window.RenderWorker") as worker_type,
            patch(
                "app.ui.main_window.ExportSettingsDialog.exec",
                return_value=QDialog.DialogCode.Accepted,
            ),
            patch.object(CanvasSnapshot, "capture_track") as capture,
            patch.object(QMessageBox, "critical") as critical_message,
        ):
            renderer_type.return_value.preflight_export.side_effect = RenderError(
                "Audio file is missing: missing-before-capture.mp3"
            )
            self.window._export_video()

        capture.assert_not_called()
        worker_type.assert_not_called()
        critical_message.assert_called_once()
        self.assertIsNone(self.window._export_dialog)
        self.assertIsNone(self.window._export_frame_staging)

    def test_automatic_nvidia_failure_offers_cpu_preflight_retry(self) -> None:
        track = PlaylistTrack(
            file_path="automatic-encoder-test.mp3",
            title="Automatic encoder",
            duration_seconds=1.0,
        )
        self.window.playlist_service.replace([track])
        original = self.window.settings_service.current
        self.window.settings_service.save(replace(
            original, video_codec=AUTO_VIDEO_ENCODER,
        ))
        try:
            with (
                patch("app.ui.main_window.FFmpegRenderer") as renderer_type,
                patch(
                    "app.ui.main_window.ExportSettingsDialog.exec",
                    return_value=QDialog.DialogCode.Accepted,
                ),
                patch.object(
                    VideoEncoderAdvisor, "automatic_encoder",
                    return_value=NVIDIA_H264_ENCODER,
                ),
                patch.object(
                    QMessageBox, "warning",
                    return_value=QMessageBox.StandardButton.Yes,
                ) as warning,
                patch.object(QMessageBox, "critical") as critical,
            ):
                renderer_type.return_value.preflight_export.side_effect = [
                    EncoderUnavailableError("NVENC startup failed"),
                    RenderError("stop after CPU retry"),
                ]
                self.window._export_video()

            self.assertEqual(
                renderer_type.return_value.preflight_export.call_count, 2,
            )
            first_settings = (
                renderer_type.return_value.preflight_export.call_args_list[0].args[2]
            )
            second_settings = (
                renderer_type.return_value.preflight_export.call_args_list[1].args[2]
            )
            self.assertEqual(first_settings.video_codec, NVIDIA_H264_ENCODER)
            self.assertEqual(second_settings.video_codec, CPU_H264_ENCODER)
            self.assertIn("NVIDIA 인코더 사용 실패", warning.call_args.args[1])
            self.assertIn("CPU H.264", warning.call_args.args[2])
            critical.assert_called_once()
        finally:
            self.window.settings_service.save(original)

    def test_automatic_encoder_prefers_nvidia_then_cpu(self) -> None:
        with patch.object(VideoEncoderAdvisor, "has_nvidia_gpu", return_value=True):
            self.assertEqual(
                VideoEncoderAdvisor.automatic_encoder(), NVIDIA_H264_ENCODER,
            )
            self.assertEqual(
                AppSettings().render_settings().video_codec,
                NVIDIA_H264_ENCODER,
            )
        with patch.object(VideoEncoderAdvisor, "has_nvidia_gpu", return_value=False):
            self.assertEqual(
                VideoEncoderAdvisor.automatic_encoder(), CPU_H264_ENCODER,
            )
            self.assertEqual(
                AppSettings().render_settings().video_codec,
                CPU_H264_ENCODER,
            )

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
                patch("app.ui.main_window.FFmpegRenderer") as renderer_type,
                patch("app.ui.main_window.RenderWorker", WorkerStub),
                patch("app.ui.main_window.ExportSettingsDialog.exec",
                      return_value=QDialog.DialogCode.Accepted),
                patch.object(CanvasSnapshot, "z_bands",
                             return_value=[(None, 1.0), (1.0, None)]),
                patch.object(
                    CanvasSnapshot, "split_mixed_capture_bands",
                    return_value=[(None, 1.0), (1.0, None)],
                ),
                patch.object(CanvasSnapshot, "capture_track", side_effect=capture_phase),
                patch.object(self.window, "_stage_export_frame", side_effect=stage_image),
                patch.object(self.window, "_export_visualizers", return_value=[]),
                patch.object(QMessageBox, "critical") as critical_message,
            ):
                renderer_type.return_value.ensure_encoder_available.side_effect = (
                    lambda encoder: (
                        (_ for _ in ()).throw(RenderError("ffv1 unavailable"))
                        if encoder == "ffv1" else None
                    )
                )
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

    def test_static_export_streams_without_staging_png_frames(self) -> None:
        track = PlaylistTrack(
            file_path="streamed-static.mp3",
            title="Streamed static",
            duration_seconds=1.0,
        )
        self.window.playlist_service.replace([track])
        self.window.store.add(Source(SourceType.TEXT, "Static title"))
        captured_worker_arguments: list[tuple[object, ...]] = []
        submitted_durations: list[float] = []
        direct_profiles: list[object] = []

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

        class EncoderStub:
            def __init__(
                self, _executable: object, output_path: Path, fps: int,
                **kwargs: object,
            ) -> None:
                self.output_path = output_path
                self.fps = fps
                self.width = 4
                self.height = 4
                direct_profiles.append(kwargs.get("direct_profile"))

            def submit(self, image: QImage, duration: float) -> None:
                self.width = image.width()
                self.height = image.height()
                submitted_durations.append(duration)

            def finish(self) -> StaticVideoStreamResult:
                self.output_path.touch()
                return StaticVideoStreamResult(
                    self.output_path,
                    sum(submitted_durations),
                    self.width,
                    self.height,
                    self.fps,
                    False,
                    30,
                    1,
                )

            def cancel(self) -> None:
                pass

        def capture_static(*_arguments: object, **_state: object) -> QImage:
            image = QImage(4, 4, QImage.Format.Format_RGB32)
            image.fill(QColor("#336699"))
            return image

        try:
            with (
                patch("app.ui.main_window.FFmpegRenderer") as renderer_type,
                patch("app.ui.main_window.RenderWorker", WorkerStub),
                patch("app.preview.export_session.StaticVideoStreamEncoder", EncoderStub),
                patch("app.ui.main_window.ExportSettingsDialog.exec",
                      return_value=QDialog.DialogCode.Accepted),
                patch.object(CanvasSnapshot, "z_bands", return_value=[(None, None)]),
                patch.object(CanvasSnapshot, "capture_track", side_effect=capture_static),
                patch.object(self.window, "_stage_export_frame") as png_stage,
                patch.object(self.window, "_export_visualizers", return_value=[]),
                patch.object(QMessageBox, "critical") as critical_message,
            ):
                renderer_type.return_value.ensure_encoder_available.return_value = None
                renderer_type.return_value.direct_encoding_profile.side_effect = (
                    lambda settings: DirectVideoEncodingProfile(
                        settings.output_width, settings.output_height,
                        settings.video_codec, (),
                    )
                )
                self.window._export_video()

            critical_message.assert_not_called()
            png_stage.assert_not_called()
            self.assertTrue(submitted_durations)
            self.assertAlmostEqual(sum(submitted_durations), track.duration_seconds)
            self.assertEqual(len(captured_worker_arguments), 1)
            prepared = captured_worker_arguments[0][1]
            self.assertIsInstance(prepared, PreparedVideoInput)
            self.assertTrue(prepared.ready_for_mux)
            self.assertEqual(len(direct_profiles), 1)
            self.assertIsNotNone(direct_profiles[0])
            self.assertEqual(prepared.encoded_codec, direct_profiles[0].codec)
            self.assertEqual(captured_worker_arguments[0][6], [])
            self.assertEqual(self.window._export_frame_index, 0)
        finally:
            if self.window._export_dialog is not None:
                self.window._export_dialog.complete(False)
                self.window._export_dialog = None
            self.window._export_finished()
            self.application.processEvents()

    def test_export_probes_independent_video_files_concurrently(self) -> None:
        with TemporaryDirectory(prefix="parallel-video-probe-") as raw_directory:
            paths = [Path(raw_directory) / f"clip-{index}.mp4" for index in range(3)]
            for path in paths:
                path.touch()
            track = PlaylistTrack(
                "song.wav", "Song", duration_seconds=3.0,
                video_paths=[str(path) for path in paths],
            )
            source = Source(
                SourceType.VIDEO, "Per-track videos",
                video_timing_mode="track", video_repeat_mode="sequence",
            )
            self.window.store.add(source)
            running = 0
            maximum_running = 0
            lock = threading.Lock()

            def probe(_path: Path) -> float:
                nonlocal running, maximum_running
                with lock:
                    running += 1
                    maximum_running = max(maximum_running, running)
                threading.Event().wait(0.04)
                with lock:
                    running -= 1
                return 1.0

            with patch.object(PlaylistService, "_probe_duration", side_effect=probe) as duration:
                clips = self.window._export_video_clips([track], 3.0)

        self.assertEqual(duration.call_count, 3)
        self.assertGreaterEqual(maximum_running, 2)
        self.assertEqual(len(clips), 3)

    def test_video_clip_geometry_and_blur_follow_the_export_render_scale(self) -> None:
        with TemporaryDirectory(prefix="clip-render-scale-") as raw_directory:
            clip_path = Path(raw_directory) / "clip.mp4"
            clip_path.touch()
            source = Source(
                SourceType.VIDEO, "Overlay clip",
                x=100.0, y=50.0, width=400.0, height=300.0,
                blur=8.0, border_radius=20.0,
                video_timing_mode="timeline", video_paths=[str(clip_path)],
            )
            self.window.store.add(source)
            track = PlaylistTrack("song.wav", "Song", duration_seconds=3.0)

            with patch.object(
                PlaylistService, "_probe_duration", return_value=3.0,
            ):
                base = self.window._export_video_clips([track], 3.0, render_scale=1.0)
                scaled = self.window._export_video_clips(
                    [track], 3.0, WORK_MODE_AUTO, 1.5,
                )

        self.assertEqual((base[0].width, base[0].height), (400, 300))
        self.assertEqual((scaled[0].width, scaled[0].height), (600, 450))
        self.assertEqual((scaled[0].x, scaled[0].y), (150, 75))
        self.assertAlmostEqual(scaled[0].blur, 12.0)
        self.assertAlmostEqual(scaled[0].border_radius, 30.0)

    def test_dynamic_export_streams_base_and_transparent_z_bands(self) -> None:
        track = PlaylistTrack(
            file_path="streamed-dynamic.mp3",
            title="Streamed dynamic",
            duration_seconds=0.5,
        )
        self.window.playlist_service.replace([track])
        particle = Source(SourceType.PARTICLE_OVERLAY, "Particles", z_index=1.0)
        self.window.store.add(particle)
        self.window.store.add(Source(SourceType.TEXT, "Foreground", z_index=2.0))
        captured_worker_arguments: list[tuple[object, ...]] = []
        active_encoder_count = 0
        maximum_active_encoder_count = 0

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

        class EncoderStub:
            def __init__(
                self, _executable: object, output_path: Path, fps: int,
                *, preserve_alpha: bool = False, **_kwargs: object,
            ) -> None:
                nonlocal active_encoder_count, maximum_active_encoder_count
                self.output_path = output_path
                self.fps = fps
                self.preserve_alpha = preserve_alpha
                self.width = 4
                self.height = 4
                self.durations: list[float] = []
                self.active = True
                active_encoder_count += 1
                maximum_active_encoder_count = max(
                    maximum_active_encoder_count, active_encoder_count,
                )

            def submit(self, image: QImage, duration: float) -> None:
                self.width = image.width()
                self.height = image.height()
                self.durations.append(duration)

            def finish(self) -> StaticVideoStreamResult:
                nonlocal active_encoder_count
                self.output_path.touch()
                if self.active:
                    self.active = False
                    active_encoder_count -= 1
                return StaticVideoStreamResult(
                    self.output_path,
                    sum(self.durations),
                    self.width,
                    self.height,
                    self.fps,
                    self.preserve_alpha,
                    max(1, len(self.durations)),
                    1,
                )

            def cancel(self) -> None:
                nonlocal active_encoder_count
                if self.active:
                    self.active = False
                    active_encoder_count -= 1

        overlay = VisualizerOverlay(
            0, 0, 4, 4, "noise", "#FFFFFF",
            kind="particles", z_index=1.0,
        )

        def capture_layer(*_arguments: object, **state: object) -> QImage:
            transparent = bool(state.get("transparent"))
            image = QImage(
                4, 4,
                QImage.Format.Format_ARGB32_Premultiplied
                if transparent else QImage.Format.Format_RGB32,
            )
            image.fill(QColor(255, 0, 0, 128) if transparent else QColor("#123456"))
            return image

        try:
            with (
                patch("app.ui.main_window.FFmpegRenderer") as renderer_type,
                patch("app.ui.main_window.RenderWorker", WorkerStub),
                patch("app.preview.export_session.StaticVideoStreamEncoder", EncoderStub),
                patch("app.ui.main_window.ExportSettingsDialog.exec",
                      return_value=QDialog.DialogCode.Accepted),
                patch.object(CanvasSnapshot, "z_bands",
                             return_value=[
                                 (None, 1.0), (1.0, 2.0), (2.0, None),
                             ]),
                patch.object(
                    CanvasSnapshot, "split_mixed_capture_bands",
                    return_value=[
                        (None, 1.0), (1.0, 2.0), (2.0, None),
                    ],
                ),
                patch.object(CanvasSnapshot, "capture_track", side_effect=capture_layer),
                patch.object(
                    self.window, "_stage_export_frame",
                    wraps=self.window._stage_export_frame,
                ) as png_stage,
                patch.object(self.window, "_export_visualizers", return_value=[overlay]),
                patch.object(QMessageBox, "critical") as critical_message,
            ):
                renderer_type.return_value.ensure_encoder_available.return_value = None
                self.window._export_video()

            critical_message.assert_not_called()
            # The unchanged base is represented by one lossless PNG instead of
            # expanding it into a full-length CPU-only CFR intermediate video.
            self.assertEqual(png_stage.call_count, 1)
            self.assertEqual(len(captured_worker_arguments), 1)
            arguments = captured_worker_arguments[0]
            self.assertIsInstance(arguments[1], list)
            self.assertEqual(arguments[5], [overlay])
            self.assertEqual(len(arguments[6]), 2)
            self.assertIsInstance(arguments[6][0], PreparedStaticOverlayLayer)
            self.assertEqual(arguments[6][0].z_index, 1.0)
            self.assertEqual(arguments[6][1].z_index, 2.0)
            self.assertEqual(self.window._export_frame_index, 1)
            assert self.window._export_dialog is not None
            self.assertEqual(self.window._export_dialog.progress_bar.value(), 25)
            self.assertIn("3/3", self.window._export_dialog.detail_label.text())
            self.assertIn("100%", self.window._export_dialog.detail_label.text())
            self.assertEqual(maximum_active_encoder_count, 2)
            self.assertEqual(active_encoder_count, 0)
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

    def test_export_progress_can_minimize_and_restore_with_main_window(self) -> None:
        dialog = ExportProgressDialog(self.window)
        dialog.set_korean(True)
        self.window._export_dialog = dialog
        self.window._lock_main_form_for_export()
        dialog.minimize_requested.connect(self.window._minimize_during_export)
        try:
            self.window.show()
            dialog.show()
            self.application.processEvents()
            self.assertEqual(dialog.minimize_button.text(), "최소화")

            QTest.mouseClick(dialog.minimize_button, Qt.MouseButton.LeftButton)
            self.application.processEvents()
            self.assertTrue(self.window.isMinimized())
            self.assertFalse(dialog.isVisible())
            self.assertTrue(self.window._export_restore_pending)

            self.window.showNormal()
            self.application.processEvents()
            self.application.processEvents()
            self.assertFalse(self.window.isMinimized())
            self.assertTrue(dialog.isVisible())
            self.assertFalse(self.window._export_restore_pending)
        finally:
            dialog.complete(False)
            self.window._export_dialog = None
            self.window._unlock_main_form_after_export()
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
            self.assertTrue(dialog.is_cancelling)
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
                "Normalizing 6 independent track(s) with 4 parallel worker(s)":
                    "독립 오디오 6곡 정규화 준비 · 병렬 작업 4개",
                "Normalized 3/6 tracks": "오디오 정규화 완료 3/6곡",
                "Inserted 2.5s of silence": "무음 구간 2.5s 추가",
                "Encoding 12.0s / 60.0s": "영상 인코딩 중 · 12.0s / 60.0s",
                "Combining audio 12.0s / 60.0s · 20%":
                    "오디오 결합 중 · 12.0s / 60.0s · 20%",
                "Preparing visual layer 1/2 · frame 8/20 · 40%":
                    "시각 레이어 준비 중 · 1/2 · 프레임 8/20 · 40%",
                "Downloaded 24.0 MB": "다운로드됨 · 24.0 MB",
                "Checksum verified; extracting archive safely":
                    "체크섬 검증 완료 · 안전하게 압축 해제 중",
            }
            for source, expected in translations.items():
                self.assertEqual(dialog._detail_text(source), expected)
            self.assertEqual(
                dialog._stage_text("Preparing visualizers"),
                "음악 반응 효과 준비",
            )
            self.assertEqual(dialog._stage_text("Downloading FFmpeg"), "FFmpeg 다운로드")
            combined = dialog._detail_text(
                "Visualizer 1/2 · frame 12/30 · 40.0%\n"
                "Visualizer 2/2 · frame 6/30 · 20.0%\n"
                "All visualizers · frame 18/60 · 30.0%"
            )
            self.assertIn("비주얼라이저 1/2 · 프레임 12/30", combined)
            self.assertIn("비주얼라이저 2/2 · 프레임 6/30", combined)
            self.assertIn("전체 비주얼라이저 · 프레임 18/60", combined)
            dialog.set_busy(
                "Preparing visualizers",
                "Analyzing audio and rendering Python visualizer frames",
            )
            self.assertEqual(dialog.stage_label.text(), "음악 반응 효과 준비")
            self.assertEqual(
                dialog.detail_label.text(),
                "오디오를 분석하고 비주얼라이저 프레임을 생성하는 중",
            )
        finally:
            dialog.complete(False)

    def test_export_progress_shows_remaining_time_only_in_dedicated_label(self) -> None:
        dialog = ExportProgressDialog(self.window)
        dialog.set_korean(True)
        try:
            with patch.object(dialog._eta_estimator, "update", return_value=40.0):
                dialog.update_progress(
                    "Preparing visualizers", 0.7,
                    "Visualizer 1/2 · frame 120/300 · 20.0% · about 00:40 remaining",
                )
            self.assertNotIn("남은", dialog.stage_label.text())
            self.assertNotIn("남은", dialog.detail_label.text())
            self.assertNotIn("남은", dialog.log_output.toPlainText())
            self.assertIn("남은 시간 약", dialog.time_label.text())
            self.assertIn("비주얼라이저 1/2", dialog.detail_label.text())

            with patch.object(dialog._eta_estimator, "remaining", return_value=35.0):
                dialog.set_busy("Preparing export", "Preparing temporary files")
            # A short indeterminate hand-off must keep counting down the last
            # stable estimate instead of blanking it at every stage boundary.
            self.assertIn("남은 시간 약", dialog.time_label.text())
        finally:
            dialog.complete(False)

    def test_export_progress_steps_and_technical_details_are_user_friendly(self) -> None:
        dialog = ExportProgressDialog(self.window)
        dialog.set_korean(True)
        try:
            self.assertEqual(
                [label.property("stepState") for label in dialog.step_labels],
                ["active", "pending", "pending", "pending", "pending"],
            )
            dialog.update_progress(
                "Combining audio", 0.60,
                "Combining audio 12.0s / 60.0s · 20%",
            )
            self.assertEqual(
                [label.property("stepState") for label in dialog.step_labels],
                ["completed", "active", "pending", "pending", "pending"],
            )
            dialog.update_progress("Encoding video", 0.80, "Encoding 8.0s / 60.0s")
            self.assertEqual(
                [label.property("stepState") for label in dialog.step_labels],
                ["completed", "completed", "completed", "active", "pending"],
            )
            friendly = dialog._detail_text(
                "Z 레이어 2 인코더 버퍼 처리 중 · "
                "17,827/17,828 프레임 · 100% · 창을 닫지 않아도 계속 진행됩니다."
            )
            self.assertEqual(
                friendly,
                "화면 구성 요소를 영상으로 변환하는 중 · "
                "17,827/17,828 프레임 · 100%",
            )
            self.assertNotIn("Z 레이어", friendly)
            self.assertNotIn("버퍼", friendly)
            self.assertFalse(dialog.log_output.isVisible())
            dialog.details_button.setChecked(True)
            self.assertFalse(dialog.log_output.isHidden())
            self.assertEqual(dialog.details_button.text(), "상세 현황 숨기기")
        finally:
            dialog.complete(False)

    def test_export_progress_resize_keeps_summary_controls_anchored(self) -> None:
        dialog = ExportProgressDialog(self.window)
        dialog.set_export_details(
            3, 145.5,
            "Resolution 1920 × 1080 · 30 FPS\nVideo encoder NVIDIA NVENC",
            Path("C:/Videos/playlist.mp4"),
        )
        try:
            dialog.resize(680, 470)
            dialog.show()
            self.application.processEvents()
            original_positions = {
                "steps": dialog.steps_widget.geometry().top(),
                "settings": dialog.export_settings_label.geometry().top(),
                "stage": dialog.stage_label.geometry().top(),
                "progress": dialog.progress_bar.geometry().top(),
                "detail": dialog.detail_label.geometry().top(),
                "time": dialog.time_label.geometry().top(),
            }

            dialog.resize(680, 780)
            self.application.processEvents()
            resized_positions = {
                "steps": dialog.steps_widget.geometry().top(),
                "settings": dialog.export_settings_label.geometry().top(),
                "stage": dialog.stage_label.geometry().top(),
                "progress": dialog.progress_bar.geometry().top(),
                "detail": dialog.detail_label.geometry().top(),
                "time": dialog.time_label.geometry().top(),
            }

            self.assertEqual(resized_positions, original_positions)
            self.assertGreater(
                dialog.buttons.geometry().top(), dialog.time_label.geometry().bottom(),
            )

            dialog.details_button.setChecked(True)
            self.application.processEvents()
            initial_log_height = dialog.log_output.height()
            dialog.resize(680, 920)
            self.application.processEvents()
            self.assertGreater(dialog.log_output.height(), initial_log_height)
        finally:
            dialog.complete(False)

    def test_export_eta_blends_recent_stage_speed_and_counts_down_while_busy(self) -> None:
        estimator = ExportEtaEstimator(0.0)

        self.assertIsNone(estimator.update("Preparing visual frames", 0.01, 1.0))
        initial = estimator.update("Preparing visual frames", 0.10, 10.0)
        slowed = estimator.update("Preparing visual frames", 0.12, 20.0)

        self.assertIsNotNone(initial)
        self.assertIsNotNone(slowed)
        assert initial is not None and slowed is not None
        self.assertGreater(slowed, initial)
        self.assertAlmostEqual(
            estimator.remaining(25.0), max(0.0, slowed - 5.0), delta=0.001,
        )

    def test_export_eta_smooths_weighted_progress_jumps_between_stages(self) -> None:
        estimator = ExportEtaEstimator(0.0)
        before = estimator.update("Preparing visual frames", 0.25, 25.0)
        after = estimator.update("Preparing export", 0.50, 26.0)

        self.assertIsNotNone(before)
        self.assertIsNotNone(after)
        assert before is not None and after is not None
        # A raw whole-export extrapolation falls from 75s to 26s here. Preserve
        # evidence from the completed preparation stage instead of presenting
        # that artificial progress-weight jump as a real 49-second speed-up.
        self.assertGreater(after, 40.0)
        self.assertLess(after, before)

        self.assertEqual(estimator.update("Complete", 1.0, 60.0), 0.0)

    def test_export_progress_keeps_selected_settings_visible(self) -> None:
        dialog = ExportProgressDialog(self.window)
        dialog.set_korean(True)
        try:
            dialog.set_export_details(
                12, 185.0,
                "해상도 3840 × 2160 · 60 FPS\n"
                "비디오 인코더 NVIDIA GPU · H.264 (NVENC)\n"
                "화질 CRF 18 · 인코딩 속도 medium · 오디오 AAC 320k",
                Path("C:/Videos/playlist.mp4"),
            )
            settings_text = dialog.export_settings_label.text()
            dialog.set_busy("Preparing visual frames", "화면 프레임 준비 중")
            dialog.update_progress("Combining audio", 0.6, "Combining audio 30.0s / 185.0s · 16%")

            self.assertEqual(dialog.export_settings_label.text(), settings_text)
            self.assertIn("3840 × 2160", settings_text)
            self.assertIn("60 FPS", settings_text)
            self.assertIn("NVENC", settings_text)
            self.assertIn("CRF 18", settings_text)
            self.assertIn("medium", settings_text)
            self.assertIn("AAC 320k", settings_text)
            self.assertIn("playlist.mp4", settings_text)
            self.assertIn("오디오 결합 중", dialog.detail_label.text())
        finally:
            dialog.complete(False)

    def test_export_progress_shows_live_storage_breakdown_and_disk_space(self) -> None:
        dialog = ExportProgressDialog(self.window)
        dialog.set_korean(True)
        try:
            estimate = estimate_export_storage(
                1920, 1080, 30, 60.0, 18, "192k", 2,
            )
            dialog.set_storage_estimate(estimate)
            dialog.update_storage_snapshot(ExportStorageSnapshot(
                categories={
                    "visuals": 3 * 1024**3,
                    "audio": 256 * 1024**2,
                    "effects": 128 * 1024**2,
                    "processing": 16 * 1024**2,
                },
                temporary_total=4 * 1024**3,
                output_in_progress=640 * 1024**2,
                disk_total=1024 * 1024**3,
                disk_free=901 * 1024**3,
            ))

            self.assertEqual(dialog.storage_rows["visuals"][1].text(), "3.0 GB")
            self.assertEqual(dialog.storage_rows["output"][1].text(), "640.0 MB")
            self.assertEqual(dialog.storage_total_value.text(), "4.0 GB")
            self.assertIn("901.0 GB", dialog.storage_disk_label.text())
            self.assertIn("1.0 TB", dialog.storage_disk_label.text())
            self.assertIn("예상 결과 영상", dialog.storage_widget.toolTip())
        finally:
            dialog.complete(False)

    def test_export_progress_storage_panel_can_be_toggled_off_and_on(self) -> None:
        dialog = ExportProgressDialog(self.window)
        dialog.set_korean(True)
        try:
            self.assertTrue(dialog.storage_button.isChecked())
            self.assertFalse(dialog.storage_widget.isHidden())

            dialog.storage_button.setChecked(False)
            self.assertTrue(dialog.storage_widget.isHidden())
            self.assertTrue(dialog.storage_heading.isHidden())
            self.assertIn("보기", dialog.storage_button.text())

            dialog.storage_button.setChecked(True)
            self.assertFalse(dialog.storage_widget.isHidden())
            self.assertIn("숨기기", dialog.storage_button.text())
        finally:
            dialog.complete(False)

    def test_export_progress_dialog_grows_for_wrapped_content_instead_of_compressing(
        self,
    ) -> None:
        from PySide6.QtWidgets import QLayout

        dialog = ExportProgressDialog(self.window)
        dialog.resize(700, 620)
        try:
            self.assertEqual(
                dialog.layout().sizeConstraint(),
                QLayout.SizeConstraint.SetMinimumSize,
            )
            dialog.set_export_details(2, 120.0, "single line summary", "C:/out.mp4")
            dialog.layout().activate()
            short_hint = dialog.sizeHint().height()

            tall_summary = "\n".join(
                f"detail row {index} with a meaningful amount of text"
                for index in range(6)
            )
            dialog.set_export_details(2, 120.0, tall_summary, "C:/out.mp4")
            dialog.layout().activate()
            tall_hint = dialog.sizeHint().height()

            # More content must make the dialog want to be taller, not squeeze
            # the cards above the label.
            self.assertGreater(tall_hint, short_hint)
        finally:
            dialog.complete(False)


if __name__ == "__main__":
    unittest.main()
