"""Primary application window and Phase 1A workspace composition."""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
import json
import logging
from pathlib import Path
import shutil
import threading
from time import monotonic
import traceback as traceback_module
import tempfile
from tempfile import TemporaryDirectory

from PySide6.QtCore import (QByteArray, QEvent, QEventLoop, QMimeData, QProcess, QSettings,
                            QStandardPaths, Qt, QTimer)
from PySide6.QtGui import (QAction, QActionGroup, QColor, QCloseEvent, QDragEnterEvent,
                           QDropEvent, QFontDatabase, QIcon, QImage, QImageReader,
                           QImageWriter, QKeySequence, QPainter, QPalette, QPen, QPixmap)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QFileDialog,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyle,
    QSystemTrayIcon,
    QTabBar,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.canvas.live_canvas import LiveCanvas
from app.animation.motion import MotionController
from app.animation.canvas_preview import CanvasAnimationPreviewController
from app.dialogs.export_progress_dialog import ExportProgressDialog
from app.dialogs.export_complete_dialog import ExportCompleteDialog
from app.dialogs.ffmpeg_install_progress_dialog import FFmpegInstallProgressDialog
from app.dialogs.export_preview_dialog import ExportPreviewDialog
from app.dialogs.export_settings_dialog import ExportSettingsDialog
from app.dialogs.missing_media_dialog import MissingMediaDialog
from app.dialogs.new_project_dialog import NewProjectDialog
from app.dialogs.playlist_export_dialog import PlaylistExportDialog
from app.dialogs.preset_dialog import DesignPresetDialog
from app.dialogs.ai_project_builder_dialog import AIProjectBuilderDialog
from app.dialogs.audio_metadata_dialog import AudioMetadataDialog
from app.dialogs.project_settings_dialog import ProjectSettingsDialog
from app.dialogs.project_crash_report_dialog import ProjectCrashReportDialog
from app.dialogs.lrc_generator_dialog import LrcGeneratorDialog
from app.dialogs.lyrics_compare_dialog import LyricsCompareDialog
from app.dialogs.settings_dialog import SettingsDialog
from app.dialogs.startup_dialog import StartupDialog
from app.dialogs.track_details_dialog import TrackDetailsDialog
from app.dialogs.shortcuts_dialog import ShortcutsDialog
from app.dialogs.text_editor_dialog import TextEditorDialog
from app.dialogs.about_dialog import AboutDialog
from app.dialogs.help_dialog import HelpDialog
from app.dialogs.update_dialogs import UpdateAvailableDialog, UpdateDownloadDialog
from app.ffmpeg.install_worker import FFmpegCatalogWorker, FFmpegInstallWorker
from app.ffmpeg.managed_installer import (
    FFmpegInstallError,
    FFmpegReleaseOption,
    ManagedFFmpegInstallation,
    ManagedFFmpegInstaller,
)
from app.inspector.source_inspector import SourceInspector
from app.layers.layer_panel import LayerPanel
from app.models.source import Source, SourceType
from app.models.project import CanvasSettings, ProjectDocument, ProjectSettings
from app.services.project_service import ProjectError, ProjectService
from app.services.project_save_worker import ProjectSaveWorker
from app.services.project_media_service import ProjectMediaService
from app.services.project_content_service import LYRICS_EXTENSIONS, ProjectContentService
from app.services.recent_projects_service import RecentProjectsService
from app.services.autosave_service import AutosaveService
from app.services.autosave_worker import AutosaveWorker
from app.services.history_service import HistoryService
from app.services.lyrics_service import (
    LyricsError,
    LyricsService,
    find_sidecar_lyrics,
)
from app.services.theme_service import Theme, ThemeService
from app.services.source_store import SourceStore
from app.services.playlist_service import AUDIO_EXTENSIONS, PlaylistService
from app.services.playlist_export_service import PlaylistExportError, PlaylistExportService
from app.services.app_settings_service import (
    AppSettings,
    AppSettingsService,
    VIDEO_ENCODERS,
)
from app.services.export_storage_service import (
    ExportStorageMonitor,
    estimate_export_storage,
)
from app.services.video_encoder_service import (
    AUTO_VIDEO_ENCODER,
    CPU_H264_ENCODER,
    NVIDIA_H264_ENCODER,
    VideoEncoderAdvisor,
)
from app.services.smooth_scroll_service import SmoothScrollService
from app.widgets.source_template_button import SourceTemplateButton
from app.services.update_service import (
    GitHubUpdateService,
    ReleaseInfo,
    normalized_version,
)
from app.services.update_worker import UpdateCheckWorker, UpdateDownloadWorker
from app.presets.preset_service import PresetDefinition
from app.presets.user_preset_service import UserPresetError, UserPresetService
from app.preview.canvas_snapshot import CanvasSnapshot
from app.preview.album_art import (
    adjust_personal_color, extract_track_cover, extract_track_personal_color,
)
from app.preview.export_plan import build_export_plan, canvas_render_scale
from app.preview.export_session import ExportSession, PngStaging
from app.preview.gpu_texture_surface import (
    GPU_TEXTURE_SURFACE_AVAILABLE, GpuTexturePreviewSurface,
)
from app.renderer.png_frame_staging import (
    PngFrameStagingCancelled,
    PngFrameStagingError,
    PngFrameStagingPipeline,
)
from app.renderer.ffmpeg_renderer import (
    EncoderUnavailableError,
    ExportMetadata,
    FFmpegNotFoundError,
    FFmpegRenderer,
    RenderCancelledError,
    RenderError,
    RenderFrame,
    RenderSettings,
    RenderResult,
    VisualizerOverlay,
    VideoClipOverlay,
    WORK_MODE_AUTO,
    WORK_MODE_MAX_SPEED,
    WORK_MODE_STABLE,
)
from app.video.timeline import build_video_occurrences
from app.renderer.render_worker import RenderWorker
from app.timeline.timeline_panel import TimelinePanel
from app.utils.i18n import Language, Translator
from app.utils.image_loader import load_pixmap
from app.utils.logging_setup import log_directory, report_unexpected_error
from app.widgets.playlist_editor import PlaylistEditor
from app.widgets.content_library_panel import ContentLibraryPanel
from app.widgets.activity_progress import ActivityProgressWidget
from app import __version__


LOGGER = logging.getLogger(__name__)
EXPORT_PREPARATION_PROGRESS_WEIGHT = 0.25

SOURCE_CLIPBOARD_MIME = "application/x-playlist-video-studio-sources+json"


@dataclass(slots=True)
class ExportFrameStagingMetrics:
    """Read-only export diagnostics collected without changing staged frames."""

    started_at: float = field(default_factory=monotonic)
    elapsed_seconds: float = 0.0
    capture_count: int = 0
    unique_file_count: int = 0
    reused_frame_count: int = 0
    total_bytes: int = 0
    largest_file_bytes: int = 0
    largest_width: int = 0
    largest_height: int = 0
    stream_file_counts: dict[str, int] = field(default_factory=dict)
    stream_bytes: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False,
    )

    def record_capture(self) -> None:
        with self._lock:
            self.capture_count += 1

    def record_reuse(self) -> None:
        with self._lock:
            self.reused_frame_count += 1

    def record_file(self, stream_key: str, image: QImage, byte_count: int) -> None:
        with self._lock:
            self.unique_file_count += 1
            self.total_bytes += byte_count
            self.stream_file_counts[stream_key] = self.stream_file_counts.get(stream_key, 0) + 1
            self.stream_bytes[stream_key] = self.stream_bytes.get(stream_key, 0) + byte_count
            if byte_count > self.largest_file_bytes:
                self.largest_file_bytes = byte_count
                self.largest_width = image.width()
                self.largest_height = image.height()

    def snapshot(self) -> ExportFrameStagingMetrics:
        """Freeze current counters for diagnostics after the temp directory is gone."""
        with self._lock:
            return replace(
                self,
                elapsed_seconds=max(0.0, monotonic() - self.started_at),
                stream_file_counts=dict(self.stream_file_counts),
                stream_bytes=dict(self.stream_bytes),
            )


class CanvasCenteredSplitter(QSplitter):
    """Keep edge panels fixed while the Canvas-side pane absorbs window resize."""

    def __init__(
        self, orientation: Qt.Orientation, center_index: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(orientation, parent)
        self._center_index = center_index
        self._edge_sizes: dict[int, int] = {}
        self._restoring_edges = False
        self.splitterMoved.connect(self._remember_edge_sizes)

    def lock_edge_sizes(self, sizes: dict[int, int] | None = None) -> None:
        """Use the current user-visible edge sizes for future window resizes."""
        if sizes is not None:
            self._edge_sizes = {
                int(index): max(0, int(size))
                for index, size in sizes.items()
                if int(index) != self._center_index
            }
            return
        current = self.sizes()
        self._edge_sizes = {
            index: size for index, size in enumerate(current)
            if index != self._center_index
        }

    def setSizes(self, sizes: list[int]) -> None:  # noqa: N802 - Qt API name
        super().setSizes(sizes)
        if not self._restoring_edges:
            self.lock_edge_sizes()

    def _remember_edge_sizes(self, _position: int, _index: int) -> None:
        if not self._restoring_edges:
            self.lock_edge_sizes()

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        before_resize = self.sizes()
        old_size = event.oldSize()
        old_extent = (
            old_size.width()
            if self.orientation() == Qt.Orientation.Horizontal
            else old_size.height()
        )
        remembered = dict(self._edge_sizes)
        if not remembered and old_extent > 0 and sum(before_resize) > 0:
            remembered = {
                index: size for index, size in enumerate(before_resize)
                if index != self._center_index
            }
        super().resizeEvent(event)
        if not remembered or self.count() <= self._center_index:
            return
        current = self.sizes()
        if len(current) != self.count():
            return
        total = sum(current)
        target = list(current)
        fixed_total = 0
        for index, size in remembered.items():
            widget = self.widget(index)
            if widget is None:
                continue
            if widget.isHidden() or (
                self.orientation() == Qt.Orientation.Horizontal
                and widget.maximumWidth() == 0
            ):
                preserved = 0
            else:
                preserved = max(0, size)
            target[index] = preserved
            fixed_total += preserved
        target[self._center_index] = max(0, total - fixed_total)
        self._restoring_edges = True
        try:
            super().setSizes(target)
        finally:
            self._restoring_edges = False
        # A temporarily small window may force Qt to compress edge panels.
        # Keep the user's remembered sizes so expanding the window restores
        # them instead of treating the temporary compression as a new choice.
        self.lock_edge_sizes(remembered)


class MainWindow(QMainWindow):
    """The runnable Phase 1A desktop workspace."""

    def __init__(self) -> None:
        super().__init__()
        self.store = SourceStore(self)
        self.playlist_service = PlaylistService(self)
        self.project_content_service = ProjectContentService(self)
        self.project_settings = ProjectSettings()
        self.playlist_export_service = PlaylistExportService()
        self.settings_service = AppSettingsService(self)
        # Renderer selection is process-scoped. Saving a different backend does
        # not mutate an already-created OpenGL/widget hierarchy mid-session.
        self._preview_backend_for_session = (
            self.settings_service.current.preview_backend
        )
        self.smooth_scroll = SmoothScrollService(
            self.settings_service.current.smooth_scrolling,
            self.settings_service.current.smooth_scroll_duration_ms,
            self,
        )
        self.smooth_scroll.install()
        self.settings_service.changed.connect(
            lambda settings: self.smooth_scroll.configure(
                settings.smooth_scrolling, settings.smooth_scroll_duration_ms
            )
        )
        self.settings_service.changed.connect(
            self._sync_export_notification_tray
        )
        self.recent_projects = RecentProjectsService(self)
        self.translator = Translator(self)
        self.theme_service = ThemeService(self)
        self.motion = MotionController(self)
        self.animation_preview_controller = CanvasAnimationPreviewController(self)
        self.animation_preview_controller.finished.connect(
            self._finish_canvas_animation_preview
        )
        self._animation_preview_active = False
        self._animation_preview_cancel_armed = False
        self.store.source_changed.connect(self._cancel_animation_preview)
        self.store.selection_set_changed.connect(self._cancel_animation_preview)
        self._inline_preview: ExportPreviewDialog | None = None
        self._inline_preview_controls: QWidget | None = None
        self._inline_preview_track_panel: QWidget | None = None
        self._preview_ui_lock_state: dict[str, object] | None = None
        self._bottom_tab_change_guard = False
        self._last_edit_bottom_tab = 0
        self.history = HistoryService(self)
        recovery_directory = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppLocalDataLocation
        )
        self.autosave = AutosaveService(Path(recovery_directory or Path.cwd() / ".app-data"))
        self._history_ready = False
        self._history_restoring = False
        self._history_applying = False
        self._project_dirty = False
        self._project_change_serial = 0
        self._project_save_worker: ProjectSaveWorker | None = None
        self._project_save_context: tuple[int, Path | None] | None = None
        self._autosave_worker: AutosaveWorker | None = None
        self._project_save_succeeded: bool | None = None
        self._history_timer = QTimer(self)
        self._history_timer.setSingleShot(True)
        self._history_timer.setInterval(300)
        self._history_timer.timeout.connect(self._commit_history)
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(30_000)
        self._autosave_timer.timeout.connect(self._autosave_project)
        self._autosave_debounce_timer = QTimer(self)
        self._autosave_debounce_timer.setSingleShot(True)
        self._autosave_debounce_timer.setInterval(4_000)
        self._autosave_debounce_timer.timeout.connect(self._autosave_project)
        self.current_project_path: Path | None = None
        self._legacy_project_path: Path | None = None
        self.current_theme = self.theme_service.preference.value
        # Version-2 projects contain legacy UI preference metadata. Keep it for
        # lossless round-trips, but do not let a project overwrite app-wide UI
        # preferences or participate in project dirty/history state.
        self._project_theme_metadata = self.current_theme
        self._project_language_metadata = self.translator.language.value
        self._sidebar_open_width = 260
        self._inspector_open_width = 300
        self._bottom_open_height = 270
        self._sidebar_transition = False
        self._panel_transition_serial = {"left": 0, "right": 0, "bottom": 0}
        self._render_worker: RenderWorker | None = None
        self._active_export_session: ExportSession | None = None
        self._export_frame_staging: TemporaryDirectory[str] | None = None
        self._export_frame_index = 0
        self._export_capture_count = 0
        self._export_frame_cache: dict[str, tuple[QImage, Path]] = {}
        self._export_frame_metrics: ExportFrameStagingMetrics | None = None
        self._export_png_pipeline: PngFrameStagingPipeline | None = None
        self._last_export_frame_metrics: ExportFrameStagingMetrics | None = None
        self._export_dialog: ExportProgressDialog | None = None
        self._export_storage_monitor: ExportStorageMonitor | None = None
        self._export_ui_lock_state: tuple[bool, bool, bool, bool, bool] | None = None
        self._export_preparation_cancel: threading.Event | None = None
        self._close_after_export_cancel = False
        self._export_restore_pending = False
        self._canvas_fit_pending = False
        self._canvas_fit_timer = QTimer(self)
        self._canvas_fit_timer.setSingleShot(True)
        self._canvas_fit_timer.timeout.connect(self._apply_scheduled_canvas_fit)
        self._notification_tray: QSystemTrayIcon | None = None
        self._export_notified_steps: set[str] = set()
        self._active_export_output_path: Path | None = None
        self._pending_export_result: RenderResult | None = None
        self._clipboard_paste_serial = 0
        self._ffmpeg_install_worker: FFmpegInstallWorker | None = None
        self._ffmpeg_catalog_worker: FFmpegCatalogWorker | None = None
        self._ffmpeg_catalog_cache: tuple[
            list[FFmpegReleaseOption], ManagedFFmpegInstallation | None,
        ] | None = None
        self._close_after_ffmpeg_catalog_cancel = False
        self._ffmpeg_install_dialog: FFmpegInstallProgressDialog | None = None
        self._settings_dialog: SettingsDialog | None = None
        self._update_service = GitHubUpdateService()
        self._update_check_worker: UpdateCheckWorker | None = None
        self._update_check_manual = False
        self._update_download_worker: UpdateDownloadWorker | None = None
        self._update_download_dialog: UpdateDownloadDialog | None = None
        self._downloaded_update_path: Path | None = None
        self._update_install_authorized = False
        self._source_buttons: dict[SourceType, QPushButton] = {}
        self._source_card_groups: dict[SourceType, QWidget] = {}
        self._source_variant_containers: dict[SourceType, QWidget] = {}
        self._source_variant_toggles: dict[SourceType, QToolButton] = {}
        self.setAcceptDrops(True)
        self.resize(1560, 920)
        self.setMinimumSize(1100, 680)
        self._build_toolbar()
        self._build_workspace()
        self._build_canvas_edit_actions()
        self._build_menu_bar()
        self.activity_progress = ActivityProgressWidget(
            self.translator.language is Language.KOREAN, self,
        )
        self.statusBar().addPermanentWidget(self.activity_progress)
        self._build_zoom_controls()
        self._apply_style()
        self._add_welcome_sources()
        self.canvas.scene_model.set_placeholder_language(
            self.translator.language is Language.KOREAN
        )
        self.translator.language_changed.connect(self.retranslate)
        self.translator.language_changed.connect(
            lambda: self.activity_progress.set_korean(
                self.translator.language is Language.KOREAN
            )
        )
        self.translator.language_changed.connect(self._sync_language_actions)
        self.translator.packs_changed.connect(self._rebuild_language_menu)
        self.theme_service.theme_changed.connect(self._on_theme_changed)
        self.retranslate()
        self._connect_history()
        self._autosave_timer.start()
        self._sync_export_notification_tray(self.settings_service.current)
        QTimer.singleShot(0, self._finish_initialization)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main toolbar")
        self.toolbar = toolbar
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)
        self.new_action = QAction(self)
        self.new_action.setShortcut(QKeySequence.StandardKey.New)
        self.new_action.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon))
        self.new_action.triggered.connect(self._show_project_start_dialog)
        toolbar.addAction(self.new_action)
        self.open_action = QAction(self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton)
        )
        self.open_action.triggered.connect(self._open_project)
        toolbar.addAction(self.open_action)
        self.save_action = QAction(self)
        self.save_action.setShortcut(QKeySequence.StandardKey.Save)
        self.save_action.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton)
        )
        self.save_action.triggered.connect(self._save_project)
        toolbar.addAction(self.save_action)
        self.save_as_action = QAction(self)
        self.save_as_action.setShortcut(QKeySequence.StandardKey.SaveAs)
        self.save_as_action.triggered.connect(lambda: self._save_project(True))
        self.undo_action = QAction(self)
        self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self.undo_action.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowBack)
        )
        self.undo_action.triggered.connect(self._undo)
        self.undo_action.setEnabled(False)
        toolbar.addAction(self.undo_action)
        self.redo_action = QAction(self)
        self.redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        self.redo_action.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowForward)
        )
        self.redo_action.triggered.connect(self._redo)
        self.redo_action.setEnabled(False)
        toolbar.addAction(self.redo_action)
        self.center_horizontal_action = QAction(
            self._alignment_toolbar_icon(horizontal=True), "", self
        )
        self.center_horizontal_action.setEnabled(False)
        self.center_horizontal_action.triggered.connect(
            lambda: self._center_selected_sources(horizontal=True)
        )
        self.center_vertical_action = QAction(
            self._alignment_toolbar_icon(horizontal=False), "", self
        )
        self.center_vertical_action.setEnabled(False)
        self.center_vertical_action.triggered.connect(
            lambda: self._center_selected_sources(horizontal=False)
        )
        self.presets_action = QAction(self)
        self.presets_action.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView)
        )
        self.presets_action.triggered.connect(self._choose_preset)
        toolbar.addAction(self.presets_action)
        self.ai_project_builder_action = QAction(self)
        self.ai_project_builder_action.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        )
        self.ai_project_builder_action.triggered.connect(self._show_ai_project_builder)
        toolbar.addAction(self.ai_project_builder_action)
        toolbar.addSeparator()
        self.fit_action = QAction(self)
        self.fit_action.setShortcut("F")
        self.fit_action.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DesktopIcon)
        )
        self.fit_action.triggered.connect(self.canvas_fit)
        toolbar.addAction(self.fit_action)
        self.grid_action = QAction(self)
        self.grid_action.setCheckable(True)
        self.grid_action.setChecked(True)
        self.grid_action.toggled.connect(self._toggle_grid)
        toolbar.addAction(self.grid_action)
        # Kept as a non-toolbar action for legacy translated text; snapping is now
        # temporarily disabled with Alt instead of a persistent toolbar toggle.
        self.snap_action = QAction(self)
        self.delete_action = QAction(self)
        self.delete_action.setShortcuts([
            QKeySequence(QKeySequence.StandardKey.Delete),
            QKeySequence(Qt.Key.Key_Backspace),
        ])
        self.delete_action.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon)
        )
        self.delete_action.triggered.connect(self._delete_contextual_selection)
        toolbar.addAction(self.delete_action)
        toolbar.addSeparator()
        language_menu = QMenu(self)
        self.language_menu = language_menu
        self.language_group = QActionGroup(self)
        self.language_group.setExclusive(True)
        self.language_actions: dict[str, QAction] = {}
        self._rebuild_language_menu()
        self.language_button = QToolButton()
        self.language_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.language_button.setMenu(language_menu)
        theme_menu = QMenu(self)
        self.theme_menu = theme_menu
        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)
        self.theme_actions: dict[Theme, QAction] = {}
        for theme in Theme:
            action = QAction(self)
            action.setCheckable(True)
            action.setChecked(theme is self.theme_service.preference)
            action.triggered.connect(
                lambda checked=False, value=theme: self.theme_service.set_preference(value)
            )
            theme_group.addAction(action)
            theme_menu.addAction(action)
            self.theme_actions[theme] = action
        self.theme_button = QToolButton()
        self.theme_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.theme_button.setMenu(theme_menu)
        self.panels_action = QAction(self)
        self.panels_action.setCheckable(True)
        self.panels_action.setChecked(True)
        self.panels_action.setShortcut(QKeySequence("Ctrl+Alt+L"))
        self.panels_action.toggled.connect(self._set_sidebar_visible)
        self.panels_action.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarMenuButton)
        )
        self.inspector_panel_action = QAction(self)
        self.inspector_panel_action.setCheckable(True)
        self.inspector_panel_action.setChecked(True)
        self.inspector_panel_action.setShortcut(QKeySequence("Ctrl+Alt+R"))
        self.inspector_panel_action.toggled.connect(
            self._set_inspector_panel_visible
        )
        self.bottom_panel_action = QAction(self)
        self.bottom_panel_action.setCheckable(True)
        self.bottom_panel_action.setChecked(True)
        self.bottom_panel_action.setShortcut(QKeySequence("Ctrl+Alt+B"))
        self.bottom_panel_action.toggled.connect(self._set_bottom_panel_visible)
        self.settings_action = QAction(self)
        self.settings_action.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView)
        )
        self.settings_action.triggered.connect(self._show_settings)

        toolbar.addWidget(self._toolbar_spacer())
        toolbar.addAction(self.panels_action)
        toolbar.addAction(self.settings_action)
        toolbar.addWidget(self.language_button)
        toolbar.addWidget(self.theme_button)
        toolbar.addSeparator()
        self.playlist_files_action = QAction(self)
        self.playlist_files_action.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogListView)
        )
        self.playlist_files_action.triggered.connect(self._export_playlist_files)
        toolbar.addAction(self.playlist_files_action)
        self.export_action = QAction(self)
        self.export_action.setEnabled(True)
        self.export_action.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton)
        )
        self.export_action.triggered.connect(self._export_video)
        toolbar.addAction(self.export_action)
        self.preview_action = QAction(self)
        self.preview_action.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        )
        self.preview_action.triggered.connect(lambda: self._show_bottom_panel(2))
        self.export_button = toolbar.widgetForAction(self.export_action)
        if self.export_button is not None:
            self.export_button.setObjectName("exportButton")
        # File/edit/view controls live in the menu bar.  Keep only the primary
        # everyday controls in the toolbar so the top area stays balanced.
        toolbar.clear()
        toolbar.addAction(self.new_action)
        toolbar.addAction(self.open_action)
        toolbar.addAction(self.save_action)
        toolbar.addSeparator()
        toolbar.addAction(self.undo_action)
        toolbar.addAction(self.redo_action)
        toolbar.addSeparator()
        toolbar.addAction(self.center_horizontal_action)
        toolbar.addAction(self.center_vertical_action)
        toolbar.addWidget(self._toolbar_spacer())
        self.project_status_label = QLabel()
        self.project_status_label.setObjectName("projectStatusChip")
        self.project_status_label.setToolTip(
            "현재 프로젝트 이름과 저장 상태" if self.translator.language is Language.KOREAN
            else "Current project name and save state"
        )
        toolbar.addWidget(self.project_status_label)
        toolbar.addAction(self.export_action)
        self.export_button = toolbar.widgetForAction(self.export_action)
        if self.export_button is not None:
            self.export_button.setObjectName("exportButton")
        self.store.selection_set_changed.connect(
            lambda _selected_ids, _active: self._update_alignment_toolbar_actions()
        )
        self.store.source_changed.connect(
            lambda _source: self._update_alignment_toolbar_actions()
        )
        self._update_alignment_toolbar_actions()

    def _rebuild_language_menu(self) -> None:
        """Rebuild the language picker after external packs change."""
        for action in tuple(self.language_group.actions()):
            self.language_group.removeAction(action)
            action.deleteLater()
        self.language_menu.clear()
        self.language_actions.clear()

        for option in self.translator.available_languages():
            action = QAction(option.display_name, self)
            action.setCheckable(True)
            action.setChecked(option.locale == self.translator.locale)
            action.triggered.connect(
                lambda checked=False, locale=option.locale: (
                    self.translator.set_language(locale) if checked else None
                )
            )
            self.language_group.addAction(action)
            self.language_menu.addAction(action)
            self.language_actions[option.locale] = action

    def _sync_language_actions(self) -> None:
        """Keep the toolbar language selection in sync with Settings."""
        for locale, action in self.language_actions.items():
            action.setChecked(locale == self.translator.locale)

    @staticmethod
    def _toolbar_spacer() -> QWidget:
        """Create the expanding spacer that keeps output controls right-aligned."""
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        return spacer

    @staticmethod
    def _alignment_toolbar_icon(horizontal: bool) -> QIcon:
        """Draw a compact, theme-neutral artboard-center alignment icon."""
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor("#77879E")
        guide_pen = QPen(color, 1.0, Qt.PenStyle.DashLine)
        shape_pen = QPen(color, 1.8)
        if horizontal:
            painter.setPen(guide_pen)
            painter.drawLine(12, 2, 12, 22)
            painter.setPen(shape_pen)
            painter.drawRect(5, 5, 14, 5)
            painter.drawRect(7, 14, 10, 5)
        else:
            painter.setPen(guide_pen)
            painter.drawLine(2, 12, 22, 12)
            painter.setPen(shape_pen)
            painter.drawRect(5, 5, 5, 14)
            painter.drawRect(14, 7, 5, 10)
        painter.end()
        return QIcon(pixmap)

    def _update_alignment_toolbar_actions(self) -> None:
        """Enable artboard-center controls only for an editable selection."""
        enabled = any(
            source is not None and not source.locked
            for source in (self.store.get(source_id) for source_id in self.store.selected_ids)
        )
        self.center_horizontal_action.setEnabled(enabled)
        self.center_vertical_action.setEnabled(enabled)

    def _build_canvas_edit_actions(self) -> None:
        """Register shortcuts shared by the Canvas and its Layer panel.

        The Layer tree owns keyboard focus after a layer is clicked.  Canvas-child
        shortcuts therefore stopped working even though the selected graphics item
        was synchronized correctly.  Window shortcuts let Qt see the key sequence
        first, while the dispatch guard keeps them out of Inspector, Playlist, and
        other text-editing controls.
        """
        self.canvas.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._canvas_shortcut_actions: list[QAction] = []

        def register(shortcut: str, callback: object) -> None:
            action = QAction(self)
            action.setShortcut(QKeySequence(shortcut))
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)

            def dispatch() -> None:
                if self._canvas_shortcut_scope_active():
                    callback()  # type: ignore[operator]

            action.triggered.connect(dispatch)
            self.addAction(action)
            self._canvas_shortcut_actions.append(action)

        # Ctrl+D and Ctrl+A are already window actions in the Edit menu.  Do not
        # register a second QAction for either sequence, because duplicate window
        # shortcuts become ambiguous and Qt suppresses both activations.
        register("Esc", self._clear_canvas_selection)
        # Arrow-key nudging is handled inside LiveCanvas.keyPressEvent so it only
        # fires while the Canvas view itself has focus (Arrow = 1px,
        # Shift+Arrow = 10px), keeping arrow navigation intact in the Layer tree
        # and every spin box.
        register("Ctrl+Shift+H", lambda: self._center_selected_sources(horizontal=True))
        register("Ctrl+Shift+V", lambda: self._center_selected_sources(horizontal=False))
        register("Ctrl+]", lambda: self._move_selected_to_edge(front=True))
        register("Ctrl+[", lambda: self._move_selected_to_edge(front=False))
        register("Ctrl+G", self._group_selected_sources)
        register("Ctrl+Shift+G", self._ungroup_selected_sources)
        register("Ctrl+L", self._toggle_selected_lock)
        register("Ctrl+0", self.canvas_fit)
        register("Ctrl+=", lambda: self._adjust_canvas_zoom(1.15))
        register("Ctrl++", lambda: self._adjust_canvas_zoom(1.15))
        register("Ctrl+-", lambda: self._adjust_canvas_zoom(1.0 / 1.15))
        register("Home", self.canvas_fit)
        # F2 rename is handled in LiveCanvas.keyPressEvent so the Layer tree keeps
        # its own built-in F2 item rename when that panel holds focus.
        QApplication.instance().focusChanged.connect(self._sync_canvas_shortcut_actions)
        self._sync_canvas_shortcut_actions(None, QApplication.focusWidget())

    def _sync_canvas_shortcut_actions(
        self, previous: QWidget | None, current: QWidget | None,
    ) -> None:
        """Enable global key sequences only while Canvas editing owns focus."""
        del previous
        enabled = self._canvas_shortcut_scope_active(current)
        for action in self._canvas_shortcut_actions:
            action.setEnabled(enabled)
        if hasattr(self, "duplicate_action"):
            editing_text = bool(
                current is not None
                and (current.inherits("QLineEdit") or current.inherits("QTextEdit")
                     or current.inherits("QPlainTextEdit")
                     or current.inherits("QAbstractSpinBox")
                     or current.inherits("QComboBox"))
            )
            self.duplicate_action.setEnabled(not editing_text)
            self.select_all_action.setEnabled(not editing_text)
            self.delete_action.setEnabled(not editing_text)
            canvas_scope = self._canvas_shortcut_scope_active(current)
            self.cut_action.setEnabled(canvas_scope)
            self.copy_action.setEnabled(canvas_scope)
            self.paste_action.setEnabled(canvas_scope)

    def _canvas_shortcut_scope_active(self, focus: QWidget | None = None) -> bool:
        """Return whether keyboard focus belongs to Canvas editing UI."""
        focus = focus or QApplication.focusWidget()
        if focus is None:
            return False
        return (
            focus is self.canvas
            or self.canvas.isAncestorOf(focus)
            or focus is self.layer_panel
            or self.layer_panel.isAncestorOf(focus)
        )

    def _selected_editable_sources(self) -> list[Source]:
        """Return selected, editable Canvas source models in drawing order."""
        selected_ids = {
            item.source.id for item in self.canvas.scene_model.selectedItems()
            if hasattr(item, "source") and not item.source.locked
        }
        return [source for source in self.store.sources() if source.id in selected_ids]

    def _duplicate_selected_sources(self) -> None:
        """Duplicate every selected source with a visible offset."""
        sources = self._selected_editable_sources()
        if not sources:
            return
        highest_z = max((source.z_index for source in self.store.sources()), default=0)
        for index, source in enumerate(sources, start=1):
            payload = source.to_dict()
            payload.pop("id", None)
            copied = Source.from_dict(payload)
            copied.name = f"{source.name} copy"
            copied.x += 24
            copied.y += 24
            copied.z_index = highest_z + index
            self.store.add(copied)
        self.statusBar().showMessage(
            "선택한 요소를 복제했습니다." if self.translator.language is Language.KOREAN
            else "Duplicated selected sources.", 2500
        )

    def _copy_selected_sources(self) -> bool:
        """Copy selected Canvas sources to a versioned application clipboard payload."""
        sources = self._selected_editable_sources()
        if not sources:
            return False
        payload_sources: list[dict[str, object]] = []
        for source in sources:
            payload = source.to_dict()
            # Clipboard groups are intentionally detached. Group IDs belong to
            # one project and must never point at an unrelated group after a
            # cross-project paste.
            payload["group_id"] = None
            payload_sources.append(payload)
        payload = {
            "schema": "playlist-video-studio/sources",
            "version": 1,
            "sources": payload_sources,
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        mime_data = QMimeData()
        mime_data.setData(
            SOURCE_CLIPBOARD_MIME, QByteArray(encoded.encode("utf-8"))
        )
        mime_data.setText(encoded)
        QApplication.clipboard().setMimeData(mime_data)
        self._clipboard_paste_serial = 0
        self.statusBar().showMessage(
            f"요소 {len(sources)}개를 복사했습니다."
            if self.translator.language is Language.KOREAN
            else f"Copied {len(sources)} source(s).",
            2500,
        )
        return True

    def _cut_selected_sources(self) -> None:
        """Copy and then remove the selected editable Canvas sources."""
        sources = self._selected_editable_sources()
        if not sources or not self._copy_selected_sources():
            return
        for source in sources:
            self.store.remove(source.id)
        self.statusBar().showMessage(
            f"요소 {len(sources)}개를 잘라냈습니다."
            if self.translator.language is Language.KOREAN
            else f"Cut {len(sources)} source(s).",
            2500,
        )

    @staticmethod
    def _clipboard_source_payload() -> list[dict[str, object]] | None:
        """Read and validate the lightweight outer clipboard contract."""
        mime_data = QApplication.clipboard().mimeData()
        if mime_data is None:
            return None
        raw = ""
        if mime_data.hasFormat(SOURCE_CLIPBOARD_MIME):
            try:
                raw = bytes(mime_data.data(SOURCE_CLIPBOARD_MIME)).decode(
                    "utf-8", errors="strict"
                )
            except UnicodeError:
                return None
        elif mime_data.hasText():
            raw = mime_data.text()
        if not raw or len(raw.encode("utf-8")) > 5_000_000:
            return None
        try:
            payload = json.loads(raw)
        except (UnicodeError, json.JSONDecodeError):
            return None
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != "playlist-video-studio/sources"
            or payload.get("version") != 1
            or not isinstance(payload.get("sources"), list)
            or len(payload["sources"]) > 1_000
            or not all(isinstance(entry, dict) for entry in payload["sources"])
        ):
            return None
        return [entry.copy() for entry in payload["sources"]]

    def _paste_sources(self) -> None:
        """Paste copied sources above existing layers with fresh IDs and an offset."""
        payloads = self._clipboard_source_payload()
        if not payloads:
            self.statusBar().showMessage(
                "붙여넣을 요소가 없습니다."
                if self.translator.language is Language.KOREAN
                else "There are no copied sources to paste.",
                2500,
            )
            return
        self._clipboard_paste_serial += 1
        offset = 24.0 * self._clipboard_paste_serial
        highest_z = max((source.z_index for source in self.store.sources()), default=0.0)
        pasted: list[Source] = []
        try:
            for index, payload in enumerate(payloads, start=1):
                payload.pop("id", None)
                payload["group_id"] = None
                copied = Source.from_dict(payload)
                copied.name = (
                    f"{copied.name} 복사본"
                    if self.translator.language is Language.KOREAN
                    else f"{copied.name} copy"
                )
                copied.x += offset
                copied.y += offset
                copied.z_index = highest_z + index
                pasted.append(copied)
        except (KeyError, TypeError, ValueError) as error:
            LOGGER.warning("Rejected invalid source clipboard payload: %s", error)
            self.statusBar().showMessage(
                "복사된 요소 데이터가 올바르지 않습니다."
                if self.translator.language is Language.KOREAN
                else "The copied source data is invalid.",
                3500,
            )
            return
        for source in pasted:
            self.store.add(source)
        pasted_ids = [source.id for source in pasted]
        self.store.select_many(pasted_ids, pasted_ids[-1] if pasted_ids else None)
        self.statusBar().showMessage(
            f"요소 {len(pasted)}개를 붙여넣었습니다."
            if self.translator.language is Language.KOREAN
            else f"Pasted {len(pasted)} source(s).",
            2500,
        )

    def _playlist_focus_active(self) -> bool:
        focus = QApplication.focusWidget()
        return bool(
            focus is not None
            and (focus is self.playlist_editor or self.playlist_editor.isAncestorOf(focus))
        )

    def _duplicate_contextual_selection(self) -> None:
        """Apply Ctrl+D to tracks when Playlist owns focus, otherwise to Canvas."""
        if self._playlist_focus_active():
            self.playlist_editor.duplicate_selected()
        else:
            self._duplicate_selected_sources()

    def _delete_contextual_selection(self) -> None:
        """Apply Delete to the currently focused editing surface."""
        if self._playlist_focus_active():
            self.playlist_editor.remove_selected()
        else:
            self._delete_selected_sources()

    def _select_all_contextual(self) -> None:
        """Apply Ctrl+A to Playlist rows or Canvas sources by focus context."""
        if self._playlist_focus_active():
            self.playlist_editor.list_widget.selectAll()
        else:
            self._select_all_canvas_sources()

    def _select_all_canvas_sources(self) -> None:
        """Select all visible Canvas sources while avoiding selection-signal churn."""
        items = [item for item in self.canvas.scene_model.items()
                 if hasattr(item, "source") and item.isVisible()]
        source_ids = [item.source.id for item in items]
        self.store.select_many(source_ids, source_ids[-1] if source_ids else None)

    def _clear_canvas_selection(self) -> None:
        """Clear selection from Canvas, Layer panel, and Inspector together."""
        self.store.select(None)

    def _adjust_canvas_zoom(self, factor: float) -> None:
        """Apply a bounded keyboard zoom around the Canvas view centre."""
        self.canvas.zoom_by(factor)

    def _build_zoom_controls(self) -> None:
        """Add a compact zoom readout and stepper to the status bar."""
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 8, 0)
        row.setSpacing(1)
        self.zoom_out_button = QToolButton()
        self.zoom_out_button.setText("−")
        self.zoom_out_button.setAutoRaise(True)
        self.zoom_out_button.clicked.connect(
            lambda: self._adjust_canvas_zoom(1.0 / 1.15)
        )
        self.zoom_reset_button = QToolButton()
        self.zoom_reset_button.setAutoRaise(True)
        self.zoom_reset_button.setMinimumWidth(52)
        self.zoom_reset_button.clicked.connect(self._toggle_canvas_zoom_100)
        self.zoom_in_button = QToolButton()
        self.zoom_in_button.setText("+")
        self.zoom_in_button.setAutoRaise(True)
        self.zoom_in_button.clicked.connect(lambda: self._adjust_canvas_zoom(1.15))
        for widget in (self.zoom_out_button, self.zoom_reset_button, self.zoom_in_button):
            row.addWidget(widget)
        self.statusBar().addPermanentWidget(container)
        self.canvas.zoom_changed.connect(self._update_zoom_label)
        self._update_zoom_label(self.canvas.transform().m11())

    def _update_zoom_label(self, zoom: float) -> None:
        self.zoom_reset_button.setText(f"{round(zoom * 100)}%")

    def _toggle_canvas_zoom_100(self) -> None:
        """Switch between 1:1 and fit-to-view from the status bar readout."""
        if abs(self.canvas.transform().m11() - 1.0) < 0.01:
            self.canvas.fit_artboard()
        else:
            self.canvas.set_zoom(1.0)

    def _nudge_selected_sources(self, x_delta: float, y_delta: float) -> None:
        """Move selected sources by an exact keyboard increment."""
        for source in self._selected_editable_sources():
            self.store.update(source.id, x=source.x + x_delta, y=source.y + y_delta)

    def _edit_canvas_source(self, source_id: str) -> None:
        """Open the in-place editor for a double-clicked Canvas source."""
        source = self.store.get(source_id)
        if source is None:
            return
        self.store.select(source_id)
        if source.source_type in {SourceType.TEXT, SourceType.LYRICS}:
            dialog = TextEditorDialog(source.text, self.translator, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.store.update(source_id, text=dialog.text())

    def _rename_selected_source(self) -> None:
        """Jump keyboard focus to the Inspector name field for a quick rename."""
        if self.store.selected is None:
            return
        if not self.inspector_panel_action.isChecked():
            self.inspector_panel_action.setChecked(True)
        self.inspector.name_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.inspector.name_edit.selectAll()

    def _center_selected_sources(self, horizontal: bool) -> None:
        """Center selected sources on the artboard horizontally or vertically."""
        artboard = self.canvas.scene_model.artboard_rect
        for source in self._selected_editable_sources():
            if horizontal:
                position = artboard.center().x() - source.width * source.scale / 2
                self.store.update(source.id, x=position)
            else:
                position = artboard.center().y() - source.height * source.scale / 2
                self.store.update(source.id, y=position)

    def _move_selected_to_edge(self, front: bool) -> None:
        """Bring selected sources in front of, or behind, every other source."""
        sources = self._selected_editable_sources()
        if not sources:
            return
        self.store.move_layers_to_edge((source.id for source in sources), front)

    def _group_selected_sources(self) -> None:
        """Create a quick group from the currently selected sources."""
        sources = self._selected_editable_sources()
        if len(sources) >= 2:
            self.store.add_group("Group", (source.id for source in sources))

    def _ungroup_selected_sources(self) -> None:
        """Clear group membership for the selected sources."""
        self.store.assign_group(
            (source.id for source in self._selected_editable_sources()), None
        )

    def _toggle_selected_lock(self) -> None:
        """Toggle lock state for one or more selected sources."""
        selected = [
            item.source for item in self.canvas.scene_model.selectedItems()
            if hasattr(item, "source")
        ]
        if not selected:
            return
        should_lock = any(not source.locked for source in selected)
        for source in selected:
            self.store.update(source.id, locked=should_lock)

    def _unlock_all_sources(self) -> None:
        """Clear the lock flag on every locked source, from any entry point."""
        unlocked = self.store.unlock_all()
        if unlocked:
            korean = self.translator.language is Language.KOREAN
            self.statusBar().showMessage(
                f"{unlocked}개 요소의 잠금을 해제했습니다." if korean
                else f"Unlocked {unlocked} source(s).",
                3000,
            )

    def _handle_canvas_context_command(self, command: str) -> None:
        """Route Canvas menu commands through the editor's shared operations."""
        handlers = {
            "duplicate": self._duplicate_selected_sources,
            "delete": self._delete_selected_sources,
            "move_forward": lambda: self._move_selected_one_layer(front=True),
            "move_backward": lambda: self._move_selected_one_layer(front=False),
            "bring_front": lambda: self._move_selected_to_edge(front=True),
            "send_back": lambda: self._move_selected_to_edge(front=False),
            "center_horizontal": lambda: self._center_selected_sources(horizontal=True),
            "center_vertical": lambda: self._center_selected_sources(horizontal=False),
            "align_left": lambda: self._align_selected_sources("left"),
            "align_hcenter": lambda: self._align_selected_sources("hcenter"),
            "align_right": lambda: self._align_selected_sources("right"),
            "align_top": lambda: self._align_selected_sources("top"),
            "align_vcenter": lambda: self._align_selected_sources("vcenter"),
            "align_bottom": lambda: self._align_selected_sources("bottom"),
            "distribute_horizontal": lambda: self._distribute_selected_sources("horizontal"),
            "distribute_vertical": lambda: self._distribute_selected_sources("vertical"),
            "group": self._group_selected_sources,
            "ungroup": self._ungroup_selected_sources,
            "toggle_visible": self._toggle_selected_visibility,
            "toggle_lock": self._toggle_selected_lock,
            "unlock_all_layers": self._unlock_all_sources,
            "select_all": self._select_all_canvas_sources,
            "fit_canvas": self.canvas_fit,
        }
        handler = handlers.get(command)
        if handler is not None:
            handler()

    def _move_selected_one_layer(self, front: bool) -> None:
        """Move the selected block by one layer without disturbing its order."""
        self.store.move_layers(
            (source.id for source in self._selected_editable_sources()),
            1 if front else -1,
        )

    def _align_selected_sources(self, mode: str) -> None:
        """Align two or more selected sources to their collective bounds."""
        sources = self._selected_editable_sources()
        if len(sources) < 2:
            return
        left = min(source.x for source in sources)
        right = max(source.x + source.width * source.scale for source in sources)
        top = min(source.y for source in sources)
        bottom = max(source.y + source.height * source.scale for source in sources)
        for source in sources:
            width = source.width * source.scale
            height = source.height * source.scale
            if mode == "left":
                self.store.update(source.id, x=left)
            elif mode == "hcenter":
                self.store.update(source.id, x=(left + right - width) / 2)
            elif mode == "right":
                self.store.update(source.id, x=right - width)
            elif mode == "top":
                self.store.update(source.id, y=top)
            elif mode == "vcenter":
                self.store.update(source.id, y=(top + bottom - height) / 2)
            elif mode == "bottom":
                self.store.update(source.id, y=bottom - height)

    def _distribute_selected_sources(self, axis: str) -> None:
        """Space three or more selected sources with equal gaps along one axis.

        The outermost two sources keep their positions; the ones between them
        are moved so every gap between adjacent edges is identical.
        """
        sources = self._selected_editable_sources()
        if len(sources) < 3:
            return
        horizontal = axis == "horizontal"
        ordered = sorted(sources, key=lambda source: source.x if horizontal else source.y)
        extents = [
            (source.width if horizontal else source.height) * source.scale
            for source in ordered
        ]
        start = ordered[0].x if horizontal else ordered[0].y
        end = (ordered[-1].x if horizontal else ordered[-1].y) + extents[-1]
        gap = (end - start - sum(extents)) / (len(ordered) - 1)
        cursor = start
        for source, extent in zip(ordered, extents):
            self.store.update(source.id, **{"x" if horizontal else "y": cursor})
            cursor += extent + gap

    def _toggle_selected_visibility(self) -> None:
        """Show or hide all selected Canvas sources as one operation."""
        selected_ids = set(self.store.selected_ids)
        sources = [
            source for source in self.store.sources() if source.id in selected_ids
        ]
        if not sources:
            return
        visible = not all(source.visible for source in sources)
        for source in sources:
            self.store.update(source.id, visible=visible)

    def _build_menu_bar(self) -> None:
        """Build a localized menu bar organized by the user's editing workflow."""
        menu_bar = self.menuBar()
        self.file_menu = menu_bar.addMenu("")
        self.file_menu.addAction(self.new_action)
        self.file_menu.addAction(self.open_action)
        self.recent_projects_menu = self.file_menu.addMenu("")
        self.recent_projects_menu.aboutToShow.connect(
            self._rebuild_recent_projects_menu
        )
        self.recent_projects.changed.connect(self._rebuild_recent_projects_menu)
        self._rebuild_recent_projects_menu()
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.save_action)
        self.file_menu.addAction(self.save_as_action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.export_action)
        self.file_menu.addAction(self.playlist_files_action)
        self.file_menu.addSeparator()
        self.exit_action = QAction(self)
        self.exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        self.exit_action.setMenuRole(QAction.MenuRole.QuitRole)
        self.exit_action.triggered.connect(self.close)
        self.file_menu.addAction(self.exit_action)

        self.project_menu = menu_bar.addMenu("")
        self.project_settings_action = QAction(self)
        self.project_settings_action.triggered.connect(self._show_project_settings)
        self.project_menu.addAction(self.project_settings_action)
        self.project_menu.addSeparator()
        self.project_menu.addAction(self.presets_action)
        self.save_preset_action = QAction(self)
        self.save_preset_action.triggered.connect(self._save_current_as_preset)
        self.project_menu.addAction(self.save_preset_action)
        self.project_menu.addAction(self.ai_project_builder_action)
        self.project_menu.addSeparator()
        self.upgrade_project_action = QAction(self)
        self.upgrade_project_action.setEnabled(False)
        self.upgrade_project_action.triggered.connect(self._upgrade_legacy_project)
        self.project_menu.addAction(self.upgrade_project_action)

        self.edit_menu = menu_bar.addMenu("")
        self.edit_menu.addAction(self.undo_action)
        self.edit_menu.addAction(self.redo_action)
        self.edit_menu.addSeparator()
        self.cut_action = QAction(self)
        self.cut_action.setShortcut(QKeySequence.StandardKey.Cut)
        self.cut_action.triggered.connect(self._cut_selected_sources)
        self.edit_menu.addAction(self.cut_action)
        self.copy_action = QAction(self)
        self.copy_action.setShortcut(QKeySequence.StandardKey.Copy)
        self.copy_action.triggered.connect(self._copy_selected_sources)
        self.edit_menu.addAction(self.copy_action)
        self.paste_action = QAction(self)
        self.paste_action.setShortcut(QKeySequence.StandardKey.Paste)
        self.paste_action.triggered.connect(self._paste_sources)
        self.edit_menu.addAction(self.paste_action)
        self.edit_menu.addSeparator()
        self.duplicate_action = QAction("복제", self)
        self.duplicate_action.setShortcut(QKeySequence("Ctrl+D"))
        self.duplicate_action.triggered.connect(self._duplicate_contextual_selection)
        self.edit_menu.addAction(self.duplicate_action)
        self.edit_menu.addAction(self.delete_action)
        self.edit_menu.addSeparator()
        self.select_all_action = QAction("전체 선택", self)
        self.select_all_action.setShortcut(QKeySequence("Ctrl+A"))
        self.select_all_action.triggered.connect(self._select_all_contextual)
        self.edit_menu.addAction(self.select_all_action)
        self.clear_selection_action = QAction(self)
        self.clear_selection_action.triggered.connect(self._clear_canvas_selection)
        self.edit_menu.addAction(self.clear_selection_action)
        QApplication.clipboard().dataChanged.connect(
            lambda: self._sync_canvas_shortcut_actions(
                None, QApplication.focusWidget()
            )
        )

        self.insert_menu = menu_bar.addMenu("")
        source_categories = (
            ("basic", (SourceType.IMAGE, SourceType.VIDEO, SourceType.TEXT, SourceType.SHAPE)),
            ("playback", (
                SourceType.PROGRESS_BAR, SourceType.TIME, SourceType.ALBUM_COVER,
                SourceType.LYRICS, SourceType.TRACK_LIST, SourceType.NOW_PLAYING,
            )),
            ("branding", (
                SourceType.LOGO, SourceType.WATERMARK, SourceType.BACKGROUND,
            )),
            ("audio_effects", (
                SourceType.AUDIO_VISUALIZER, SourceType.AUDIO_WAVEFORM,
                SourceType.AUDIO_LEVEL_METER, SourceType.PARTICLE_OVERLAY,
            )),
        )
        self.insert_category_menus: dict[str, QMenu] = {}
        self.source_insert_actions: dict[SourceType, QAction] = {}
        for category, source_types in source_categories:
            category_menu = self.insert_menu.addMenu("")
            self.insert_category_menus[category] = category_menu
            for source_type in source_types:
                action = QAction(self)
                action.triggered.connect(
                    lambda checked=False, kind=source_type: self._add_source(kind)
                )
                category_menu.addAction(action)
                self.source_insert_actions[source_type] = action

        self.view_menu = menu_bar.addMenu("")
        self.view_menu.addAction(self.fit_action)
        self.view_menu.addAction(self.grid_action)
        self.view_menu.addSeparator()
        self.view_menu.addAction(self.panels_action)
        self.view_menu.addAction(self.inspector_panel_action)
        self.view_menu.addAction(self.bottom_panel_action)
        self.view_menu.addSeparator()
        self.show_playlist_action = QAction(self)
        self.show_playlist_action.setShortcut(QKeySequence("Ctrl+Alt+1"))
        self.show_playlist_action.triggered.connect(lambda: self._show_bottom_panel(0))
        self.view_menu.addAction(self.show_playlist_action)
        self.show_timeline_action = QAction(self)
        self.show_timeline_action.setShortcut(QKeySequence("Ctrl+Alt+2"))
        self.show_timeline_action.triggered.connect(lambda: self._show_bottom_panel(1))
        self.view_menu.addAction(self.show_timeline_action)
        self.view_menu.addSeparator()
        self.view_menu.addAction(self.preview_action)

        self.tools_menu = menu_bar.addMenu("")
        self.lrc_generator_action = QAction(self)
        self.lrc_generator_action.triggered.connect(self._show_lrc_generator)
        self.tools_menu.addAction(self.lrc_generator_action)
        self.tools_menu.addSeparator()
        self.tools_menu.addAction(self.settings_action)
        self.tools_menu.addSeparator()
        self.language_menu.setTitle("언어")
        self.theme_menu.setTitle("테마")
        self.tools_menu.addMenu(self.language_menu)
        self.tools_menu.addMenu(self.theme_menu)
        self.help_menu = menu_bar.addMenu("")
        self.help_action = QAction(self)
        self.help_action.setShortcut(QKeySequence("F1"))
        self.help_action.triggered.connect(self._show_help)
        self.help_menu.addAction(self.help_action)
        self.shortcuts_action = QAction(self)
        self.shortcuts_action.triggered.connect(self._show_shortcuts)
        self.help_menu.addAction(self.shortcuts_action)
        self.help_menu.addSeparator()
        self.check_updates_action = QAction(self)
        self.check_updates_action.triggered.connect(
            lambda: self._check_for_updates(manual=True)
        )
        self.help_menu.addAction(self.check_updates_action)
        self.help_menu.addSeparator()
        self.about_action = QAction(self)
        self.about_action.setMenuRole(QAction.MenuRole.AboutRole)
        self.about_action.triggered.connect(self._show_about)
        self.help_menu.addAction(self.about_action)
        self._sync_canvas_shortcut_actions(None, QApplication.focusWidget())

    def _rebuild_recent_projects_menu(self) -> None:
        """Populate File > Recent projects from the current valid MRU list."""
        menu = getattr(self, "recent_projects_menu", None)
        if not isinstance(menu, QMenu):
            return
        menu.clear()
        korean = self.translator.language is Language.KOREAN
        projects = self.recent_projects.projects()
        if not projects:
            empty_action = menu.addAction(
                "최근 프로젝트가 없습니다." if korean else "No recent projects"
            )
            empty_action.setEnabled(False)
            return

        for index, path in enumerate(projects, start=1):
            action = menu.addAction(f"{index}. {path.name}")
            action.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
            )
            action.setToolTip(str(path))
            action.setStatusTip(str(path))
            action.triggered.connect(
                lambda _checked=False, selected=Path(path):
                self._open_recent_project(selected)
            )
        menu.addSeparator()
        clear_action = menu.addAction(
            "최근 프로젝트 목록 지우기" if korean else "Clear recent projects"
        )
        clear_action.triggered.connect(self._confirm_clear_recent_projects)

    def _confirm_clear_recent_projects(self) -> bool:
        """Clear only the MRU history after an explicit user confirmation."""
        korean = self.translator.language is Language.KOREAN
        response = QMessageBox.question(
            self,
            "최근 프로젝트 목록 지우기" if korean else "Clear recent projects",
            (
                "최근에 연 프로젝트 목록을 모두 지울까요?\n\n"
                "프로젝트 파일 자체는 삭제되지 않습니다."
                if korean else
                "Clear the entire recent projects list?\n\n"
                "The project files themselves will not be deleted."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if response != QMessageBox.StandardButton.Yes:
            return False
        self.recent_projects.clear()
        self.statusBar().showMessage(
            "최근 프로젝트 목록을 지웠습니다."
            if korean else "Cleared the recent projects list.",
            3000,
        )
        return True

    def _open_recent_project(self, path: Path) -> bool:
        """Open one MRU entry through the same guarded workflow as File > Open."""
        project_path = Path(path).expanduser()
        korean = self.translator.language is Language.KOREAN
        if not project_path.is_file():
            self.recent_projects.remove(project_path)
            QMessageBox.warning(
                self,
                "프로젝트를 찾을 수 없음" if korean else "Project not found",
                f"최근 프로젝트 파일을 찾을 수 없어 목록에서 제거했습니다.\n\n{project_path}"
                if korean else
                f"The recent project could not be found and was removed from the list.\n\n{project_path}",
            )
            return False
        return self._open_project_with_confirmation(project_path)

    def _build_workspace(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        top_splitter = CanvasCenteredSplitter(
            Qt.Orientation.Horizontal, center_index=1,
        )
        top_splitter.setChildrenCollapsible(False)
        self.main_splitter = top_splitter
        left_workspace = QFrame()
        left_workspace.setObjectName("leftWorkspace")
        self.left_workspace = left_workspace
        left_workspace.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding,
        )
        left_layout = QVBoxLayout(left_workspace)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        self.source_sidebar = self._make_source_sidebar()
        self.content_library_panel = ContentLibraryPanel(
            self.project_content_service, self.translator,
            used_keys_provider=lambda: self.project_content_service.referenced_keys(
                self._project_document()
            ),
        )
        self.content_library_panel.add_requested.connect(self._add_library_content)
        _refresh_content_markers = self.content_library_panel.schedule_used_refresh
        self.playlist_service.playlist_changed.connect(_refresh_content_markers)
        for _store_signal in (
            self.store.source_added, self.store.source_removed,
            self.store.source_changed, self.store.sources_replaced,
        ):
            _store_signal.connect(_refresh_content_markers)
        self.left_tabs = QTabWidget()
        self.left_tabs.setObjectName("leftProjectTabs")
        self.left_tabs.addTab(self.source_sidebar, "")
        self.left_tabs.addTab(self.content_library_panel, "")
        self.layer_panel = LayerPanel(self.store, self.translator)
        self.left_tabs.addTab(self.layer_panel, "")
        self.left_tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self.left_tabs.tabBar().setExpanding(True)
        left_layout.addWidget(self.left_tabs)
        top_splitter.addWidget(left_workspace)
        center = QWidget()
        center.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding,
        )
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(10, 10, 10, 8)
        self.canvas = LiveCanvas(self.store, self.translator)
        self.canvas.files_dropped.connect(self._handle_dropped_files)
        self.canvas.source_template_dropped.connect(self._handle_source_template_drop)
        self.canvas.cut_requested.connect(self._cut_selected_sources)
        self.canvas.copy_requested.connect(self._copy_selected_sources)
        self.canvas.paste_requested.connect(self._paste_sources)
        self.canvas.command_requested.connect(self._handle_canvas_context_command)
        self.canvas.nudge_requested.connect(self._nudge_selected_sources)
        self.canvas.edit_requested.connect(self._edit_canvas_source)
        self.canvas.rename_requested.connect(self._rename_selected_source)
        self.canvas_stack = QStackedWidget()
        self.canvas_stack.setObjectName("canvasWorkspaceStack")
        self.canvas_stack.addWidget(self.canvas)
        self._preview_gpu_composition_anchor: QWidget | None = None
        if (
            self._preview_backend_for_session == "gpu_layers"
            and GPU_TEXTURE_SURFACE_AVAILABLE
            and GpuTexturePreviewSurface is not None
        ):
            # Adding the first QOpenGLWidget to an already-visible top-level
            # window can make Qt recreate that native window. Register a hidden
            # surface before MainWindow is shown so opening Preview never causes
            # the visible close/reopen flash on Windows.
            self._preview_gpu_composition_anchor = GpuTexturePreviewSurface()
            self._preview_gpu_composition_anchor.setObjectName(
                "previewGpuCompositionAnchor"
            )
            self.canvas_stack.addWidget(self._preview_gpu_composition_anchor)
            self.canvas_stack.setCurrentWidget(self.canvas)
        center_layout.addWidget(self.canvas_stack, 1)
        top_splitter.addWidget(center)
        self.inspector = SourceInspector(
            self.store, self.translator,
            tracks_provider=lambda: self.playlist_service.tracks,
        )
        self.inspector.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding,
        )
        self.inspector.animation_preview_requested.connect(
            self._preview_source_animation
        )
        self.inspector_stack = QStackedWidget()
        self.inspector_stack.setObjectName("inspectorWorkspaceStack")
        self.inspector_stack.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding,
        )
        self.preview_track_inspector = QFrame()
        self.preview_track_inspector.setObjectName("previewTrackInspectorPage")
        self.preview_track_inspector_layout = QVBoxLayout(
            self.preview_track_inspector
        )
        self.preview_track_inspector_layout.setContentsMargins(0, 0, 0, 0)
        self.preview_track_inspector_layout.setSpacing(0)
        self.inspector_stack.addWidget(self.inspector)
        self.inspector_stack.addWidget(self.preview_track_inspector)
        self.inspector_stack.setCurrentWidget(self.inspector)
        top_splitter.addWidget(self.inspector_stack)
        top_splitter.setStretchFactor(0, 0)
        top_splitter.setStretchFactor(1, 1)
        top_splitter.setStretchFactor(2, 0)
        settings = QSettings()

        def saved_panel_size(
            key: str, default: int, minimum: int, maximum: int,
        ) -> int:
            try:
                value = int(settings.value(key, default))
            except (TypeError, ValueError):
                value = default
            return max(minimum, min(maximum, value))

        self._sidebar_open_width = saved_panel_size(
            "workspace/left_panel_width", self._sidebar_open_width, 180, 1600,
        )
        self._inspector_open_width = saved_panel_size(
            "workspace/right_panel_width", self._inspector_open_width, 220, 1600,
        )
        top_splitter.setSizes([
            self._sidebar_open_width, 990, self._inspector_open_width,
        ])
        top_splitter.lock_edge_sizes({
            0: self._sidebar_open_width,
            2: self._inspector_open_width,
        })
        self.workspace_splitter = CanvasCenteredSplitter(
            Qt.Orientation.Vertical, center_index=0,
        )
        self.workspace_splitter.setObjectName("workspaceSplitter")
        self.workspace_splitter.setChildrenCollapsible(False)
        self.workspace_splitter.addWidget(top_splitter)
        top_splitter.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding,
        )
        self.bottom_tabs = QTabWidget()
        self.bottom_tabs.setObjectName("bottomWorkspaceTabs")
        self.bottom_tabs.setDocumentMode(True)
        self.bottom_tabs.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred,
        )
        self.playlist_editor = PlaylistEditor(self.playlist_service, self.translator)
        self.playlist_editor.request_files.connect(self._choose_audio_files)
        self.playlist_editor.files_dropped.connect(self._handle_dropped_files)
        self.playlist_editor.lyrics_dropped.connect(self._handle_lyrics_drop)
        self.playlist_editor.track_double_clicked.connect(self._show_track_details)
        self.bottom_tabs.addTab(self.playlist_editor, "")
        self.timeline_panel = TimelinePanel(
            self.playlist_service, self.store, self.translator
        )
        self.bottom_tabs.addTab(self.timeline_panel, "")
        self.preview_tab_page = QFrame()
        self.preview_tab_page.setObjectName("previewWorkspaceTab")
        self.preview_tab_layout = QVBoxLayout(self.preview_tab_page)
        self.preview_tab_layout.setContentsMargins(0, 0, 0, 0)
        self.preview_tab_layout.setSpacing(0)
        self.bottom_tabs.addTab(self.preview_tab_page, "")
        self.bottom_workspace_stack = QStackedWidget()
        self.bottom_workspace_stack.setObjectName("bottomWorkspaceStack")
        self.bottom_workspace_stack.addWidget(self.bottom_tabs)
        self.workspace_splitter.addWidget(self.bottom_workspace_stack)
        self.workspace_splitter.setStretchFactor(0, 1)
        self.workspace_splitter.setStretchFactor(1, 0)
        saved_sizes = settings.value("workspace/vertical_splitter", [650, 270])
        try:
            sizes = [max(120, int(value)) for value in saved_sizes]
        except (TypeError, ValueError):
            sizes = [650, 270]
        legacy_bottom_height = sizes[1] if len(sizes) == 2 else self._bottom_open_height
        self._bottom_open_height = saved_panel_size(
            "workspace/bottom_panel_height", legacy_bottom_height, 180, 1200,
        )
        self.workspace_splitter.setSizes([650, self._bottom_open_height])
        self.workspace_splitter.lock_edge_sizes({1: self._bottom_open_height})
        try:
            saved_tab = int(QSettings().value("workspace/bottom_tab", 0))
        except (TypeError, ValueError):
            saved_tab = 0
        self._last_edit_bottom_tab = max(0, min(1, saved_tab))
        self.bottom_tabs.setCurrentIndex(self._last_edit_bottom_tab)
        self.bottom_tabs.currentChanged.connect(self._bottom_workspace_tab_changed)
        self._workspace_settings_timer = QTimer(self)
        self._workspace_settings_timer.setSingleShot(True)
        self._workspace_settings_timer.setInterval(250)
        self._workspace_settings_timer.timeout.connect(self._save_workspace_layout)
        self.workspace_splitter.splitterMoved.connect(
            lambda _position, _index: self._workspace_settings_timer.start()
        )
        self.main_splitter.splitterMoved.connect(
            lambda _position, _index: self._workspace_settings_timer.start()
        )
        root_layout.addWidget(self.workspace_splitter, 1)
        self.setCentralWidget(root)
        self._restore_workspace_panel_visibility()

    def _save_workspace_layout(self) -> None:
        """Persist each panel's last useful open size after resizing settles."""
        horizontal = self.main_splitter.sizes()
        if len(horizontal) == 3:
            if not self.left_workspace.isHidden() and horizontal[0] >= 180:
                self._sidebar_open_width = horizontal[0]
            if not self.inspector_stack.isHidden() and horizontal[2] >= 220:
                self._inspector_open_width = horizontal[2]
        vertical = self.workspace_splitter.sizes()
        if (len(vertical) == 2 and not self.bottom_workspace_stack.isHidden()
                and vertical[1] >= 180):
            self._bottom_open_height = vertical[1]

        settings = QSettings()
        settings.setValue("workspace/left_panel_width", self._sidebar_open_width)
        settings.setValue("workspace/right_panel_width", self._inspector_open_width)
        settings.setValue("workspace/bottom_panel_height", self._bottom_open_height)
        # Retain the former key for compatibility with builds that only knew
        # the vertical splitter pair. Never persist a hidden panel's zero size.
        top_height = vertical[0] if len(vertical) == 2 else 650
        settings.setValue(
            "workspace/vertical_splitter",
            [max(120, top_height), self._bottom_open_height],
        )
        settings.sync()

    def _restore_workspace_panel_visibility(self) -> None:
        """Restore the three independent workspace panel visibility choices."""
        settings = QSettings()

        def preference(key: str) -> bool:
            try:
                return bool(settings.value(key, True, type=bool))
            except TypeError:
                value = settings.value(key, True)
                return str(value).strip().lower() not in {"0", "false", "no", "off"}

        entries = (
            (
                self.panels_action, self.left_workspace,
                preference("workspace/left_panel_visible"),
            ),
            (
                self.inspector_panel_action, self.inspector_stack,
                preference("workspace/right_panel_visible"),
            ),
            (
                self.bottom_panel_action, self.bottom_workspace_stack,
                preference("workspace/bottom_panel_visible"),
            ),
        )
        for action, widget, visible in entries:
            previous = action.blockSignals(True)
            action.setChecked(visible)
            action.blockSignals(previous)
            widget.setVisible(visible)

    def _make_source_sidebar(self) -> QWidget:
        """Build the source palette with a scrollable source-card area."""
        panel = QFrame()
        panel.setObjectName("sidePanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 16, 14, 14)
        layout.setSpacing(8)
        self.sidebar_title = QLabel()
        self.sidebar_title.setObjectName("panelTitle")
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self.source_result_label = QLabel()
        self.source_result_label.setObjectName("sourceResultCount")
        header.addWidget(self.sidebar_title)
        header.addStretch()
        header.addWidget(self.source_result_label)
        layout.addLayout(header)

        # Category tabs. "all" shows every stacked section; a category tab shows
        # only its own. A search spans everything and pins the bar to "all";
        # clicking any tab during a search clears the query first.
        self._source_tab_categories = ("all", "basic", "playback", "audio", "scene")
        self._active_source_category = "all"
        self._source_tab_syncing = False
        self.source_tab_bar = QTabBar()
        self.source_tab_bar.setObjectName("sourceCategoryTabs")
        self.source_tab_bar.setExpanding(False)
        self.source_tab_bar.setDrawBase(False)
        self.source_tab_bar.setUsesScrollButtons(True)
        for _category in self._source_tab_categories:
            self.source_tab_bar.addTab("")
        self.source_tab_bar.tabBarClicked.connect(self._on_source_tab_clicked)
        layout.addWidget(self.source_tab_bar)

        self.source_search = QLineEdit()
        self.source_search.setObjectName("sourceSearch")
        self.source_search.setClearButtonEnabled(True)
        self.source_search.addAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.source_search.textChanged.connect(self._filter_source_cards)
        layout.addWidget(self.source_search)

        self.source_cards_scroll = QScrollArea()
        self.source_cards_scroll.setObjectName("sourceCardsScroll")
        self.source_cards_scroll.setWidgetResizable(True)
        self.source_cards_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.source_cards_scroll.setFrameShape(QFrame.Shape.NoFrame)
        cards_widget = QWidget()
        cards_layout = QVBoxLayout(cards_widget)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setSpacing(12)
        descriptions = [
            ("image", SourceType.IMAGE), ("video", SourceType.VIDEO), ("text", SourceType.TEXT), ("shape", SourceType.SHAPE),
            ("progress_bar", SourceType.PROGRESS_BAR), ("album_cover", SourceType.ALBUM_COVER),
            ("time", SourceType.TIME), ("logo", SourceType.LOGO), ("watermark", SourceType.WATERMARK),
            ("background", SourceType.BACKGROUND), ("audio_visualizer", SourceType.AUDIO_VISUALIZER),
            ("lyrics", SourceType.LYRICS),
            ("track_list", SourceType.TRACK_LIST), ("now_playing", SourceType.NOW_PLAYING),
            ("audio_waveform", SourceType.AUDIO_WAVEFORM),
            ("audio_level_meter", SourceType.AUDIO_LEVEL_METER),
            ("particle_overlay", SourceType.PARTICLE_OVERLAY),
        ]
        variant_parents = {
            SourceType.TIME: SourceType.TEXT,
            SourceType.LOGO: SourceType.IMAGE,
            SourceType.WATERMARK: SourceType.IMAGE,
        }
        self._source_variant_parents = variant_parents
        self._source_search_terms: dict[SourceType, str] = {}
        description_keys = {source_type: key for key, source_type in descriptions}
        source_categories = (
            ("basic", (
                SourceType.TEXT, SourceType.SHAPE, SourceType.IMAGE, SourceType.VIDEO,
            )),
            ("playback", (
                SourceType.LYRICS, SourceType.ALBUM_COVER,
                SourceType.PROGRESS_BAR, SourceType.TRACK_LIST,
                SourceType.NOW_PLAYING,
            )),
            ("audio", (
                SourceType.AUDIO_VISUALIZER, SourceType.AUDIO_WAVEFORM,
                SourceType.AUDIO_LEVEL_METER,
            )),
            ("scene", (
                SourceType.BACKGROUND, SourceType.PARTICLE_OVERLAY,
            )),
        )
        self._source_category_sections: dict[str, QWidget] = {}
        self._source_category_titles: dict[str, QLabel] = {}
        self._source_type_categories: dict[SourceType, str] = {}
        for category, category_types in source_categories:
            section = QWidget()
            section.setObjectName("sourceCategorySection")
            section_layout = QVBoxLayout(section)
            section_layout.setContentsMargins(0, 0, 0, 0)
            section_layout.setSpacing(5)
            category_label = QLabel()
            category_label.setObjectName("sourceCategoryTitle")
            section_layout.addWidget(category_label)
            self._source_category_sections[category] = section
            self._source_category_titles[category] = category_label
            cards_layout.addWidget(section)
            for source_type in category_types:
                self._source_type_categories[source_type] = category
                self._add_source_palette_card(
                    source_type, description_keys, variant_parents,
                    section_layout,
                )
        cards_layout.addStretch(1)
        self.source_cards_scroll.setWidget(cards_widget)
        layout.addWidget(self.source_cards_scroll, 1)
        return panel

    def _add_source_palette_card(
        self, source_type: SourceType,
        description_keys: dict[SourceType, str],
        variant_parents: dict[SourceType, SourceType],
        section_layout: QVBoxLayout,
    ) -> None:
        """Add one top-level palette card and its property-only variants."""
        group = QFrame()
        group.setObjectName("sourceTemplateGroup")
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(0, 0, 0, 0)
        group_layout.setSpacing(4)
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(4)
        button = SourceTemplateButton(source_type.value, source_type.value)
        button.set_card_icon(self._source_palette_icon(source_type))
        button.clicked.connect(
            lambda checked=False, kind=source_type: self._add_source(kind)
        )
        row_layout.addWidget(button, 1)
        variants = [
            child for child, parent in variant_parents.items()
            if parent is source_type
        ]
        if variants:
            toggle = QToolButton()
            toggle.setObjectName("sourceVariantToggle")
            toggle.setCheckable(True)
            toggle.setFixedWidth(30)
            toggle.setText("▸")
            toggle.toggled.connect(
                lambda expanded, parent_type=source_type: self._set_source_variants_expanded(
                    parent_type, expanded
                )
            )
            row_layout.addWidget(toggle)
            self._source_variant_toggles[source_type] = toggle
        group_layout.addWidget(row)
        self._source_buttons[source_type] = button
        self._source_search_terms[source_type] = (
            f"{description_keys[source_type]} {source_type.value}"
        ).lower()
        if variants:
            variant_container = QWidget()
            variant_container.setObjectName("sourceVariantContainer")
            variant_layout = QVBoxLayout(variant_container)
            variant_layout.setContentsMargins(18, 0, 0, 0)
            variant_layout.setSpacing(4)
            for child_type in variants:
                child_button = SourceTemplateButton(
                    child_type.value, source_type.value
                )
                child_button.set_card_icon(
                    self._source_palette_icon(child_type)
                )
                child_button.clicked.connect(
                    lambda checked=False, kind=child_type: self._add_source(kind)
                )
                self._source_buttons[child_type] = child_button
                self._source_search_terms[child_type] = (
                    f"{description_keys[child_type]} {child_type.value} "
                    f"{description_keys[source_type]} {source_type.value}"
                ).lower()
                variant_layout.addWidget(child_button)
            variant_container.hide()
            group_layout.addWidget(variant_container)
            self._source_variant_containers[source_type] = variant_container
        self._source_card_groups[source_type] = group
        section_layout.addWidget(group)

    def _source_palette_icon(self, source_type: SourceType) -> QIcon:
        """Return a familiar, theme-aware icon for a source palette card."""
        icon_types = {
            SourceType.IMAGE: QStyle.StandardPixmap.SP_FileIcon,
            SourceType.LOGO: QStyle.StandardPixmap.SP_FileIcon,
            SourceType.WATERMARK: QStyle.StandardPixmap.SP_FileIcon,
            SourceType.VIDEO: QStyle.StandardPixmap.SP_MediaPlay,
            SourceType.TEXT: QStyle.StandardPixmap.SP_FileDialogDetailedView,
            SourceType.TIME: QStyle.StandardPixmap.SP_BrowserReload,
            SourceType.SHAPE: QStyle.StandardPixmap.SP_DialogResetButton,
            SourceType.PROGRESS_BAR: QStyle.StandardPixmap.SP_MediaSeekForward,
            SourceType.ALBUM_COVER: QStyle.StandardPixmap.SP_DirIcon,
            SourceType.BACKGROUND: QStyle.StandardPixmap.SP_DesktopIcon,
            SourceType.AUDIO_VISUALIZER: QStyle.StandardPixmap.SP_MediaVolume,
            SourceType.AUDIO_WAVEFORM: QStyle.StandardPixmap.SP_MediaVolume,
            SourceType.AUDIO_LEVEL_METER: QStyle.StandardPixmap.SP_MediaVolume,
            SourceType.LYRICS: QStyle.StandardPixmap.SP_FileDialogDetailedView,
            SourceType.TRACK_LIST: QStyle.StandardPixmap.SP_FileDialogListView,
            SourceType.NOW_PLAYING: QStyle.StandardPixmap.SP_MediaPlay,
            SourceType.PARTICLE_OVERLAY: QStyle.StandardPixmap.SP_ComputerIcon,
        }
        return self.style().standardIcon(
            icon_types.get(source_type, QStyle.StandardPixmap.SP_FileIcon)
        )

    def _filter_source_cards(self, query: str) -> None:
        """Show palette sources matching the search and the active category tab."""
        normalized = query.strip().lower()
        if normalized and self.source_tab_bar.currentIndex() != 0:
            # A search always spans every category; pin the bar to "all".
            self._source_tab_syncing = True
            self.source_tab_bar.setCurrentIndex(0)
            self._source_tab_syncing = False
        active_category = (
            "all" if normalized
            else self._source_tab_categories[self.source_tab_bar.currentIndex()]
        )
        self._active_source_category = active_category
        matched_count = 0
        for parent_type, group in self._source_card_groups.items():
            in_tab = (
                active_category == "all"
                or self._source_type_categories.get(parent_type) == active_category
            )
            parent_button = self._source_buttons[parent_type]
            parent_text = (
                f"{self._source_search_terms.get(parent_type, '')} "
                f"{parent_button.property('paletteText') or ''}"
            ).lower()
            parent_match = in_tab and (not normalized or normalized in parent_text)
            if parent_match:
                matched_count += 1
            child_types = [
                child for child, parent in self._source_variant_parents.items()
                if parent is parent_type
            ]
            child_matches: dict[SourceType, bool] = {}
            for child_type in child_types:
                child_button = self._source_buttons[child_type]
                searchable = (
                    f"{self._source_search_terms.get(child_type, '')} "
                    f"{child_button.property('paletteText') or ''}"
                ).lower()
                child_matches[child_type] = in_tab and (
                    not normalized or normalized in searchable
                )
                if child_matches[child_type]:
                    matched_count += 1
                child_button.setVisible(child_matches[child_type])
            group.setVisible(parent_match or any(child_matches.values()))
            parent_button.setVisible(True)
            container = self._source_variant_containers.get(parent_type)
            if container is not None:
                expanded = self._source_variant_toggles[parent_type].isChecked()
                container.setVisible((in_tab and expanded and not normalized) or (
                    bool(normalized) and any(child_matches.values())
                ))
        for category, section in self._source_category_sections.items():
            section.setVisible(any(
                not group.isHidden()
                for source_type, group in self._source_card_groups.items()
                if self._source_type_categories.get(source_type) == category
            ))
        korean = self.translator.language is Language.KOREAN
        self.source_result_label.setText(
            f"{matched_count}개 결과" if korean and normalized else
            f"{matched_count}개" if korean else
            f"{matched_count} results" if normalized else
            f"{matched_count} sources"
        )

    def _on_source_tab_clicked(self, index: int) -> None:
        """Switch category, clearing any active search first (fires on every click)."""
        if self._source_tab_syncing or not 0 <= index < len(self._source_tab_categories):
            return
        if self.source_search.text():
            self.source_search.blockSignals(True)
            self.source_search.clear()
            self.source_search.blockSignals(False)
        self.source_tab_bar.setCurrentIndex(index)
        self._filter_source_cards("")

    def _set_source_variants_expanded(
        self, parent_type: SourceType, expanded: bool,
    ) -> None:
        """Expand one parent's property-only template variants."""
        toggle = self._source_variant_toggles.get(parent_type)
        container = self._source_variant_containers.get(parent_type)
        if toggle is not None:
            toggle.setText("▾" if expanded else "▸")
        if container is not None:
            container.setVisible(expanded)

    def _source_hover_help(self, source_type: SourceType) -> tuple[str, str]:
        """Return a concise purpose and Inspector-setting summary for a source."""
        korean = self.translator.language is Language.KOREAN
        korean_help = {
            SourceType.IMAGE: (
                "사진이나 그래픽 파일을 캔버스에 표시합니다.",
                "이미지 파일 · 맞춤 방식 · 밝기 · 대비 · 흐림 · 그림자",
            ),
            SourceType.VIDEO: (
                "곡별 또는 전체 타임라인에 맞춰 영상 파일을 재생합니다.",
                "실행 타이밍 · 영상 목록 · 반복 방식 · 사이클 · 속도 · 필터 · 흐림",
            ),
            SourceType.TEXT: (
                "제목과 설명 또는 동적 트랙 정보를 표시합니다.",
                "텍스트 · 글꼴 · 크기 · 정렬 · 줄바꿈 · 색상 · 외곽선",
            ),
            SourceType.SHAPE: (
                "디자인을 구성하는 도형과 색상 면을 추가합니다.",
                "도형 종류 · 채우기 · 그라데이션 · 외곽선 · 모서리 둥글기",
            ),
            SourceType.PROGRESS_BAR: (
                "현재 곡 또는 전체 재생 진행률을 표시합니다.",
                "진행 방식 · 스타일 · 진행 값 · 트랙 색상 · 채우기 색상",
            ),
            SourceType.ALBUM_COVER: (
                "재생 중인 곡의 앨범 이미지를 자동으로 표시합니다.",
                "이미지 맞춤 · 프레임 스타일 · 밝기 · 대비 · 그림자",
            ),
            SourceType.TIME: (
                "현재 시간이나 재생 시간을 동적으로 표시합니다.",
                "시간 형식 · 글꼴 · 크기 · 정렬 · 색상 · 외곽선",
            ),
            SourceType.LOGO: (
                "브랜드 또는 채널 로고 이미지를 배치합니다.",
                "로고 파일 · 이미지 맞춤 · 투명도 · 밝기 · 대비 · 그림자",
            ),
            SourceType.WATERMARK: (
                "영상 위에 워터마크 이미지를 표시합니다.",
                "이미지 파일 · 맞춤 방식 · 투명도 · 위치 · 크기 · 그림자",
            ),
            SourceType.BACKGROUND: (
                "캔버스 전체의 색상, 이미지 또는 앨범 아트 배경을 만듭니다.",
                "배경 방식 · 이미지 맞춤 · 앰비언트 효과 · 밝기 · 대비 · 흐림",
            ),
            SourceType.AUDIO_VISUALIZER: (
                "음악의 주파수 변화에 반응하는 시각 효과를 표시합니다.",
                "스타일 · 막대 수 · 선 굵기 · 감도 · 반응성 · 어택 · 릴리즈 · 스무딩",
            ),
            SourceType.LYRICS: (
                "재생 위치에 맞춰 가사 또는 자막을 표시합니다.",
                "자막 스타일 · 전환 · 문맥 줄 · 줄 간격 · 이전 줄 효과 · 타이밍 보정",
            ),
            SourceType.TRACK_LIST: (
                "현재 곡 주변의 플레이리스트 항목을 표시합니다.",
                "표시 곡 수 · 목록 스타일 · 표시 범위 · 곡 정보 · 간격 · 강조 색상",
            ),
            SourceType.NOW_PLAYING: (
                "현재 재생 중인 곡 정보를 카드 형태로 표시합니다.",
                "카드 스타일 · 표시 시간 · 퇴장 효과 · 글꼴 · 정렬 · 색상",
            ),
            SourceType.AUDIO_WAVEFORM: (
                "음원의 파형을 재생 진행과 함께 표시합니다.",
                "파형 스타일 · 채우기 색상 · 크기 · 투명도 · 애니메이션",
            ),
            SourceType.AUDIO_LEVEL_METER: (
                "음량 레벨과 피크를 실시간 미터로 표시합니다.",
                "모드 · 스타일 · 방향 · 감도 · 어택 · 릴리즈 · 구간 · 피크 · 색상",
            ),
            SourceType.PARTICLE_OVERLAY: (
                "캔버스 위에 움직이는 파티클 또는 노이즈 효과를 추가합니다.",
                "스타일 · 밀도 · 속도 · 크기 · 방향 · 반짝임 · 광택 · 색상 · 시드",
            ),
        }
        english_help = {
            SourceType.IMAGE: ("Display a photo or graphic file on the Canvas.", "Image file · fit mode · brightness · contrast · blur · shadow"),
            SourceType.VIDEO: ("Play video files per track or across the whole timeline.", "Timing · media list · repeat mode · cycles · speed · filters · blur"),
            SourceType.TEXT: ("Display a title, description, or dynamic track information.", "Text · font · size · alignment · wrapping · color · outline"),
            SourceType.SHAPE: ("Add a shape or color surface to the design.", "Shape · fill · gradient · outline · corner radius"),
            SourceType.PROGRESS_BAR: ("Show current-track or playlist progress.", "Progress mode · style · value · track color · fill color"),
            SourceType.ALBUM_COVER: ("Automatically show the current track's album artwork.", "Image fit · frame style · brightness · contrast · shadow"),
            SourceType.TIME: ("Dynamically show clock or playback time.", "Time format · font · size · alignment · color · outline"),
            SourceType.LOGO: ("Place a brand or channel logo image.", "Logo file · image fit · opacity · brightness · contrast · shadow"),
            SourceType.WATERMARK: ("Display a watermark image over the video.", "Image file · fit mode · opacity · position · size · shadow"),
            SourceType.BACKGROUND: ("Create a full-Canvas color, image, or album-art background.", "Background mode · image fit · ambient effect · brightness · contrast · blur"),
            SourceType.AUDIO_VISUALIZER: ("Show a visual effect that reacts to music frequencies.", "Style · bars · line width · sensitivity · reactivity · attack · release · smoothing"),
            SourceType.LYRICS: ("Show lyrics or subtitles synchronized to playback.", "Text color · transition · context lines · spacing · previous-line effect · timing offset"),
            SourceType.TRACK_LIST: ("Show playlist entries around the current track.", "Track count · list style · window · metadata · spacing · highlight colors"),
            SourceType.NOW_PLAYING: ("Show current-track information as a card.", "Card style · duration · exit effect · font · alignment · colors"),
            SourceType.AUDIO_WAVEFORM: ("Show the audio waveform together with playback progress.", "Waveform style · fill color · size · opacity · animation"),
            SourceType.AUDIO_LEVEL_METER: ("Show audio levels and peaks as a live meter.", "Mode · style · direction · sensitivity · attack · release · segments · peak · colors"),
            SourceType.PARTICLE_OVERLAY: ("Add moving particles or noise over the Canvas.", "Style · density · speed · size · direction · twinkle · glow · colors · seed"),
        }
        return (korean_help if korean else english_help)[source_type]

    def _source_palette_summary(self, source_type: SourceType) -> str:
        """Return a short secondary line sized for the narrow source palette."""
        korean = self.translator.language is Language.KOREAN
        summaries = {
            SourceType.IMAGE: ("사진과 그래픽", "Photos and graphics"),
            SourceType.VIDEO: ("영상 클립 재생", "Play video clips"),
            SourceType.TEXT: ("제목과 동적 정보", "Titles and dynamic info"),
            SourceType.SHAPE: ("색상 면과 도형", "Color surfaces and shapes"),
            SourceType.PROGRESS_BAR: ("곡·전체 진행률", "Track or playlist progress"),
            SourceType.ALBUM_COVER: ("현재 곡의 앨범 커버", "Current album artwork"),
            SourceType.TIME: ("시간과 재생 위치", "Clock and playback time"),
            SourceType.LOGO: ("브랜드 로고 이미지", "Brand logo image"),
            SourceType.WATERMARK: ("반투명 워터마크", "Transparent watermark"),
            SourceType.BACKGROUND: ("캔버스 전체 배경", "Full Canvas background"),
            SourceType.AUDIO_VISUALIZER: ("주파수 반응 효과", "Frequency-reactive effect"),
            SourceType.LYRICS: ("실시간 가사와 자막", "Timed lyrics and subtitles"),
            SourceType.TRACK_LIST: ("플레이리스트 목록", "Playlist track list"),
            SourceType.NOW_PLAYING: ("현재 곡 정보 카드", "Current-track info card"),
            SourceType.AUDIO_WAVEFORM: ("재생 반응 오디오 파형", "Playback-reactive waveform"),
            SourceType.AUDIO_LEVEL_METER: ("실시간 음량 미터", "Live audio level meter"),
            SourceType.PARTICLE_OVERLAY: ("파티클과 노이즈 효과", "Particles and noise effects"),
        }
        localized = summaries.get(source_type, ("캔버스 요소", "Canvas source"))
        return localized[0 if korean else 1]

    def _source_type_label(self, source_type: SourceType) -> str:
        """Return the same localized source name for menus and palette buttons."""
        korean = self.translator.language is Language.KOREAN
        try:
            return self.translator.text(source_type.value)
        except KeyError:
            labels = {
                SourceType.LYRICS: "가사 / 자막" if korean else "Lyrics / subtitles",
                SourceType.TRACK_LIST: "트랙 목록" if korean else "Track list",
                SourceType.NOW_PLAYING: "현재 재생 카드" if korean else "Now playing card",
                SourceType.AUDIO_WAVEFORM: "오디오 파형" if korean else "Audio waveform",
                SourceType.AUDIO_LEVEL_METER: "오디오 레벨 미터" if korean else "Audio level meter",
                SourceType.PARTICLE_OVERLAY: "파티클 / 노이즈" if korean else "Particles / noise",
                SourceType.VIDEO: "영상" if korean else "Video",
            }
            return labels.get(source_type, source_type.value.replace("_", " ").title())

    def _update_source_button_help(
        self, source_type: SourceType, button: QPushButton, label: str,
    ) -> None:
        """Install localized rich hover help without changing the compact layout."""
        description, settings = self._source_hover_help(source_type)
        korean = self.translator.language is Language.KOREAN
        settings_heading = "추가 후 설정" if korean else "Settings after adding"
        click_hint = (
            "클릭하거나 캔버스로 드래그하면 추가됩니다."
            if korean else "Click or drag it onto the Canvas to add it."
        )
        button.setToolTip(
            f"<div style='width: 330px'><b>{label}</b><br>"
            f"{description}<br><br><b>{settings_heading}</b><br>"
            f"{settings}<br><br><i>{click_hint}</i></div>"
        )
        button.setToolTipDuration(15_000)
        button.setAccessibleDescription(f"{description} {settings_heading}: {settings}")

    def _add_welcome_sources(self) -> None:
        artboard = self.canvas.scene_model.artboard_rect
        width = artboard.width()
        height = artboard.height()
        margin_x = max(40.0, min(100.0, width * 0.078125))
        title_width = max(180.0, min(520.0, width - margin_x * 2))
        progress_width = max(180.0, min(760.0, width - margin_x * 2))
        title_y = max(40.0, min(85.0, height * 0.118))
        progress_y = max(title_y + 140.0, height - max(80.0, height * 0.1944))
        progress_y = min(progress_y, height - 24.0)
        self.store.add(Source(SourceType.BACKGROUND, "Background", width=width, height=height,
                              fill_color="#263042", locked=False, z_index=-10, text=""))
        self.store.add(Source(SourceType.TEXT, "Playlist title", x=margin_x, y=title_y,
                              width=title_width,
                              height=100, fill_color="#7C3AED", border_radius=16,
                              text="Late Night Playlist", z_index=1))
        self.store.add(Source(SourceType.PROGRESS_BAR, "Progress", x=margin_x,
                              y=progress_y, width=progress_width,
                              height=14, fill_color="#27D17F", border_radius=7, z_index=2))

    def _add_source(self, source_type: SourceType, position: object | None = None) -> None:
        """Add a base source or one of its property-only palette templates."""
        count = len(self.store.sources())
        template_type = source_type
        source_type = self._source_variant_parents.get(source_type, source_type)
        name = template_type.value.replace("_", " ").title()
        dimensions = (260.0, 90.0)
        if template_type in {SourceType.ALBUM_COVER, SourceType.LOGO}:
            dimensions = (180.0, 180.0)
        if source_type is SourceType.VIDEO:
            dimensions = (480.0, 270.0)
        if source_type in {SourceType.AUDIO_VISUALIZER, SourceType.AUDIO_WAVEFORM}:
            dimensions = (460.0, 100.0)
        if source_type is SourceType.LYRICS:
            # Timed preview normally displays previous/current/next cues. The
            # former generic 260x90 box was only large enough for its one-line
            # editor placeholder, making preview lyrics appear outside the
            # position chosen on the Canvas.
            dimensions = (620.0, 220.0)
        if source_type is SourceType.AUDIO_LEVEL_METER:
            dimensions = (80.0, 180.0)
        if source_type is SourceType.PARTICLE_OVERLAY:
            dimensions = (1280.0, 720.0)
        if source_type is SourceType.TRACK_LIST:
            dimensions = (460.0, 260.0)
        if source_type is SourceType.NOW_PLAYING:
            dimensions = (440.0, 170.0)
        if source_type is SourceType.BACKGROUND:
            dimensions = (1280.0, 720.0)
        default_text = name
        if template_type is SourceType.TIME:
            default_text = "%current_time% / %total_time%"
        elif source_type is SourceType.LYRICS:
            default_text = "Lyrics are not available for this track."
        elif source_type is SourceType.TRACK_LIST:
            default_text = "▶ 01. Current track\n  02. Next track"
        elif source_type is SourceType.NOW_PLAYING:
            default_text = "NOW PLAYING\nTrack title\nArtist"
        artboard = self.canvas.scene_model.artboard_rect
        default_x = 170 + (count % 4) * 35
        default_y = 190 + (count % 3) * 35
        if position is not None and hasattr(position, "x") and hasattr(position, "y"):
            default_x = max(
                artboard.left(),
                min(float(position.x()) - dimensions[0] / 2, artboard.right() - dimensions[0]),
            )
            default_y = max(
                artboard.top(),
                min(float(position.y()) - dimensions[1] / 2, artboard.bottom() - dimensions[1]),
            )
        source = Source(
            source_type=source_type,
            name=name,
            x=default_x,
            y=default_y,
            width=dimensions[0], height=dimensions[1],
            border_radius=12 if source_type is not SourceType.PROGRESS_BAR else 8,
            fill_color="#1685D1" if source_type is not SourceType.BACKGROUND else "#263042",
            text=default_text,
            z_index=count,
            locked=False,
        )
        if source_type is SourceType.LYRICS:
            source.subtitle_context_lines = -1
            source.subtitle_next_lines = -1
        elif source_type is SourceType.TRACK_LIST:
            source.track_list_count = 0
        if template_type is SourceType.LOGO:
            source.image_fit_mode = "contain"
            source.border_radius = 0.0
            source.text = ""
        elif template_type is SourceType.WATERMARK:
            source.image_fit_mode = "contain"
            source.opacity = 0.45
            source.border_radius = 0.0
            source.text = ""
        self.store.add(source)

    def _handle_source_template_drop(
        self, source_type_value: str, parent_type_value: str, position: object,
    ) -> None:
        """Create a palette template centered at its Canvas drop point."""
        try:
            source_type = SourceType(source_type_value)
            parent_type = SourceType(parent_type_value)
        except ValueError:
            return
        expected_parent = self._source_variant_parents.get(source_type, source_type)
        if parent_type is not expected_parent:
            return
        self._add_source(source_type, position)

    def _show_track_details(self, track_id: str) -> None:
        """Open track metadata and timed-lyrics editing for a playlist card."""
        track = next((entry for entry in self.playlist_service.tracks if entry.id == track_id), None)
        if track is None:
            return
        content_lyrics = [
            (Path(content.path).name, content.path)
            for content in self.project_content_service.items
            if content.media_type == "lyrics"
        ]
        dialog = TrackDetailsDialog(
            track, self.translator, self, content_lyrics=content_lyrics,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.playlist_service.update_track(
                track_id,
                title=dialog.selected_title,
                artist=dialog.selected_artist,
                album=dialog.selected_album,
                cover_path=dialog.selected_cover_path,
                video_paths=dialog.selected_video_paths,
                lyrics_path=dialog.selected_lyrics_path,
                lyrics=dialog.selected_lyrics,
                lyrics_timing_offset_seconds=dialog.selected_timing_offset,
            )

    def _choose_audio_files(self) -> None:
        """Let the user select supported audio files for the playlist."""
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "음악 파일 추가" if self.translator.language is Language.KOREAN else "Add music files",
            "",
            "Audio files (*.mp3 *.wav *.flac *.aac *.m4a *.ogg)",
        )
        if paths:
            _added, accepted_paths, lyric_notes = self._import_audio_files(paths)
            self.project_content_service.add_paths(accepted_paths)
            self._notify_sidecar_lyrics(lyric_notes)

    def _import_audio_files(
        self, paths: list[str] | list[Path],
    ) -> tuple[int, list[Path], list[str]]:
        """Inspect audio tags, request missing project metadata, and add tracks."""
        if not paths:
            return 0, [], []
        korean = self.translator.language is Language.KOREAN
        self.activity_progress.begin(
            "content_add", "콘텐츠 추가" if korean else "Adding content",
            detail=(f"오디오 {len(paths)}개 분석 중" if korean
                    else f"Inspecting {len(paths)} audio file(s)"),
        )
        QApplication.processEvents()
        try:
            candidates = self.playlist_service.inspect_files(paths)
            if not candidates:
                return 0, [], []
            tracks = [candidate.track for candidate in candidates]
            if any(candidate.missing_fields for candidate in candidates):
                self.activity_progress.update(
                    "content_add", detail=(
                        "누락된 곡 정보를 확인하는 중" if korean
                        else "Waiting for missing track information"
                    ),
                )
                dialog = AudioMetadataDialog(candidates, self.translator, self)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return 0, [], []
                tracks = dialog.selected_tracks
            self.activity_progress.update(
                "content_add", 0.8,
                "프로젝트 콘텐츠에 등록하는 중" if korean
                else "Registering project content",
            )
            lyric_notes = self._attach_sidecar_lyrics(tracks)
            added = self.playlist_service.add_tracks(tracks)
            accepted_paths = [Path(track.file_path) for track in tracks]
            return added, accepted_paths, lyric_notes
        finally:
            self.activity_progress.finish("content_add")

    def _attach_sidecar_lyrics(self, tracks: list) -> list[str]:
        """Attach a same-name lyric file, or prompt for a similar one, per settings.

        Returns a short "track ← file" note for every attachment so the caller
        can surface a status-bar notification.
        """
        mode = self.settings_service.current.lyrics_auto_attach_mode
        if mode == "never":
            return []
        korean = self.translator.language is Language.KOREAN
        notes: list[str] = []
        for track in tracks:
            if track.lyrics or track.lyrics_path:
                continue
            exact, similar = find_sidecar_lyrics(track.file_path)
            sidecar = None
            if exact:
                sidecar = exact[0]
                if mode == "ask" and not self._confirm_sidecar_lyrics(
                    track.title, sidecar, korean,
                ):
                    sidecar = None
            elif similar and self._confirm_sidecar_lyrics(
                track.title, similar[0], korean,
            ):
                sidecar = similar[0]
            if sidecar is None:
                continue
            try:
                cues = LyricsService.load(sidecar.path)
            except LyricsError:
                continue
            if not cues:
                continue
            track.lyrics = cues
            track.lyrics_path = str(sidecar.path.resolve())
            notes.append(f"'{track.title}' ← {sidecar.path.name}")
        return notes

    def _confirm_sidecar_lyrics(
        self, track_title: str, sidecar: object, korean: bool,
    ) -> bool:
        """Ask before attaching a lyric file that was matched by name."""
        exact = bool(getattr(sidecar, "exact", False))
        name = Path(getattr(sidecar, "path", "")).name
        if korean:
            title = "가사·자막 파일 발견"
            relation = "이름이 같은" if exact else "이름이 비슷한"
            body = (
                f"'{track_title}' 곡과 {relation} 자막 파일이 있습니다:\n{name}\n\n"
                "가사로 함께 추가할까요?"
            )
        else:
            title = "Lyric file found"
            relation = "the same name as" if exact else "a similar name to"
            body = (
                f"A subtitle file with {relation} '{track_title}' was found:\n"
                f"{name}\n\nAttach it as lyrics?"
            )
        return QMessageBox.question(
            self, title, body,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        ) == QMessageBox.StandardButton.Yes

    def _notify_sidecar_lyrics(self, notes: list[str]) -> None:
        """Show a transient status-bar note for auto-attached lyric files."""
        if not notes:
            return
        korean = self.translator.language is Language.KOREAN
        if len(notes) == 1:
            message = (
                f"가사·자막 파일을 자동으로 연결했습니다 · {notes[0]}"
                if korean else
                f"Attached a lyric file automatically · {notes[0]}"
            )
        else:
            message = (
                f"가사·자막 파일 {len(notes)}개를 자동으로 연결했습니다"
                if korean else
                f"Attached {len(notes)} lyric files automatically"
            )
        self.statusBar().showMessage(message, 7000)

    def _add_library_content(self, path: str, media_type: str) -> None:
        """Turn a reusable library entry into the appropriate project object."""
        content_path = Path(path)
        if not content_path.is_file():
            QMessageBox.warning(
                self,
                "콘텐츠를 찾을 수 없음" if self.translator.language is Language.KOREAN else "Content not found",
                str(content_path),
            )
            return
        if media_type == "image":
            self._add_dropped_images([content_path], None)
        elif media_type == "audio":
            _added, _paths, lyric_notes = self._import_audio_files([content_path])
            self._notify_sidecar_lyrics(lyric_notes)
        elif media_type == "video":
            self._add_dropped_videos([content_path], None)
        elif media_type == "font":
            font_id = QFontDatabase.addApplicationFont(str(content_path))
            families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
            source = Source(
                SourceType.TEXT, content_path.stem, x=180, y=180,
                width=520, height=110, text="Playlist title",
                font_family=families[0] if families else "Segoe UI",
                font_path=str(content_path.resolve()), z_index=len(self.store.sources()),
            )
            self.store.add(source)
            self.store.select(source.id)
        elif media_type == "lyrics":
            selected_ids = [
                str(item.data(Qt.ItemDataRole.UserRole))
                for item in self.playlist_editor.list_widget.selectedItems()
            ]
            tracks = self.playlist_service.tracks
            target = next((track for track in tracks if track.id in selected_ids), None)
            if target is None and len(tracks) == 1:
                target = tracks[0]
            if target is None:
                QMessageBox.information(
                    self,
                    "가사 연결" if self.translator.language is Language.KOREAN else "Attach lyrics",
                    "플레이리스트에서 가사를 연결할 곡 하나를 선택해 주세요."
                    if self.translator.language is Language.KOREAN else
                    "Select one playlist track, then add this lyrics file again.",
                )
                return
            self._attach_lyrics_to_track(content_path, target.id)

    def _handle_lyrics_drop(self, path: str, track_id: str) -> None:
        """Attach a dropped lyrics file to the exact playlist row under the pointer."""
        korean = self.translator.language is Language.KOREAN
        if not track_id:
            QMessageBox.information(
                self,
                "가사 연결" if korean else "Attach lyrics",
                "가사 파일을 적용할 곡 위에 직접 놓아 주세요."
                if korean else "Drop the lyrics file directly onto a playlist track.",
            )
            return
        self._attach_lyrics_to_track(Path(path), track_id)

    def _attach_lyrics_to_track(self, content_path: Path, track_id: str) -> bool:
        """Load, compare, and attach lyrics without silently replacing existing work."""
        korean = self.translator.language is Language.KOREAN
        target = next(
            (track for track in self.playlist_service.tracks if track.id == track_id),
            None,
        )
        if target is None:
            QMessageBox.warning(
                self, "가사 연결" if korean else "Attach lyrics",
                "선택한 곡을 찾을 수 없습니다."
                if korean else "The selected track could not be found.",
            )
            return False
        if not content_path.is_file():
            QMessageBox.warning(
                self, "가사 파일을 찾을 수 없음" if korean else "Lyrics not found",
                str(content_path),
            )
            return False
        try:
            incoming_cues = LyricsService.load(content_path)
        except LyricsError as error:
            QMessageBox.warning(self, "Lyrics", str(error))
            return False
        if not incoming_cues:
            QMessageBox.warning(
                self, "가사 연결" if korean else "Attach lyrics",
                "가사 파일에 인식할 수 있는 타이밍 가사가 없습니다."
                if korean else "The file contains no recognizable timed lyrics.",
            )
            return False

        current_cues = list(target.lyrics)
        if not current_cues and target.lyrics_path:
            try:
                current_cues = LyricsService.load(target.lyrics_path)
            except LyricsError:
                # The project may still contain usable embedded cues or a stale
                # path. The comparison remains useful even with an empty side.
                current_cues = []
        if target.lyrics or target.lyrics_path:
            dialog = LyricsCompareDialog(
                current_cues, incoming_cues, target.lyrics_path,
                str(content_path.resolve()), self.translator, self,
            )
            if (
                dialog.exec() != QDialog.DialogCode.Accepted
                or not dialog.replace_requested
            ):
                return False

        resolved = str(content_path.resolve())
        self.playlist_service.update_track(
            target.id, lyrics_path=resolved, lyrics=incoming_cues,
        )
        self.project_content_service.add_paths([content_path])
        if not any(
            source.source_type is SourceType.LYRICS for source in self.store.sources()
        ):
            self._add_source(SourceType.LYRICS)
        self.statusBar().showMessage(
            f"'{target.title}' 곡에 가사를 적용했습니다."
            if korean else f"Attached lyrics to '{target.title}'.",
            5000,
        )
        return True

    @staticmethod
    def _window_drop_paths(event: QDragEnterEvent | QDropEvent) -> list[str]:
        if not event.mimeData().hasUrls():
            return []
        return [
            path
            for url in event.mimeData().urls()
            if url.isLocalFile() and (path := url.toLocalFile())
        ]

    @staticmethod
    def _drop_contains_lyrics(paths: list[str]) -> bool:
        return any(
            Path(path).suffix.lower() in LYRICS_EXTENSIONS for path in paths
        )

    def _drag_returned_to_project_content(
        self, event: QDragEnterEvent | QDropEvent,
    ) -> bool:
        """Treat the Project Content panel as an invalid target for its own drag."""
        if event.source() is not self.content_library_panel.list:
            return False
        if not self.content_library_panel.isVisible():
            return False
        window_position = event.position().toPoint()
        global_position = self.mapToGlobal(window_position)
        panel_position = self.content_library_panel.mapFromGlobal(global_position)
        return self.content_library_panel.rect().contains(panel_position)

    @staticmethod
    def _reject_returned_project_content_drag(
        event: QDragEnterEvent | QDropEvent,
    ) -> None:
        # Consume IgnoreAction at the top-level window. This prevents the event
        # from falling through to the normal global file-drop/import path.
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """Accept general files globally, but reserve lyrics for Playlist rows."""
        if self._drag_returned_to_project_content(event):
            self._reject_returned_project_content_drag(event)
            return
        paths = self._window_drop_paths(event)
        if paths and not self._drop_contains_lyrics(paths):
            event.acceptProposedAction()
            return
        # A lyric drag rejected by child widgets must stay rejected here too;
        # otherwise it bubbles to MainWindow and the cursor becomes allowed.
        event.ignore()

    def dragMoveEvent(self, event: QDragEnterEvent) -> None:
        """Keep lyrics forbidden everywhere except a Playlist track target."""
        self.dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        """Route general file drops while refusing lyrics outside Playlist rows."""
        if self._drag_returned_to_project_content(event):
            self._reject_returned_project_content_drag(event)
            return
        paths = self._window_drop_paths(event)
        if not paths or self._drop_contains_lyrics(paths):
            event.ignore()
            return
        self._handle_dropped_files(paths)
        event.acceptProposedAction()

    def _handle_dropped_files(self, raw_paths: list[str], position: object | None = None) -> None:
        """Classify a mixed file drop and add it to the appropriate editor area."""
        paths = [Path(raw_path) for raw_path in raw_paths if Path(raw_path).is_file()]
        project_paths = [
            path for path in paths
            if path.suffix.lower() == ProjectService.PACKAGE_SUFFIX
            or path.name.lower().endswith(".project.json")
        ]
        if project_paths:
            self._confirm_and_load_dropped_project(project_paths[0])
            return
        image_extensions = {".jpg", ".jpeg", ".png", ".webp", ".svg"}
        image_paths = [path for path in paths if path.suffix.lower() in image_extensions]
        video_extensions = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
        video_paths = [path for path in paths if path.suffix.lower() in video_extensions]
        audio_paths = [path for path in paths if path.suffix.lower() in AUDIO_EXTENSIONS]
        image_count = self._add_dropped_images(image_paths, position)
        video_count = self._add_dropped_videos(video_paths, position)
        audio_count, accepted_audio_paths, lyric_notes = self._import_audio_files(
            audio_paths
        )
        self.project_content_service.add_paths([*image_paths, *video_paths, *accepted_audio_paths])
        if image_count or video_count or audio_count:
            korean = self.translator.language is Language.KOREAN
            message = (
                f"이미지 {image_count}개, 영상 {video_count}개, 음악 {audio_count}개를 추가했습니다."
                if korean else f"Added {image_count} image(s), {video_count} video source(s), and {audio_count} music file(s)."
            )
            if lyric_notes:
                message += (
                    f" 가사·자막 {len(lyric_notes)}개 연결됨."
                    if korean else
                    f" Attached {len(lyric_notes)} lyric file(s)."
                )
            self.statusBar().showMessage(message, 7000)
            return
        korean = self.translator.language is Language.KOREAN
        self.statusBar().showMessage(
            "지원되는 이미지, 영상, 음악 또는 프로젝트 파일을 놓아 주세요."
            if korean else "Drop supported image, video, music, or project files.",
            5000,
        )

    def _add_dropped_images(self, paths: list[Path], position: object | None) -> int:
        """Create Canvas image sources at the drop point while preserving aspect ratio."""
        if not paths:
            return 0
        korean = self.translator.language is Language.KOREAN
        self.activity_progress.begin(
            "content_add", "콘텐츠 추가" if korean else "Adding content",
            detail=(f"이미지 {len(paths)}개 처리 중" if korean
                    else f"Processing {len(paths)} image(s)"),
        )
        QApplication.processEvents()
        try:
            artboard = self.canvas.scene_model.artboard_rect
            point = position if hasattr(position, "x") and hasattr(position, "y") else artboard.center()
            count = 0
            last_source: Source | None = None
            for index, path in enumerate(paths):
                self.activity_progress.update(
                    "content_add", index / len(paths),
                    f"{path.name} ({index + 1}/{len(paths)})",
                )
                pixmap = load_pixmap(path)
                if pixmap.isNull():
                    continue
                source_width = float(pixmap.width())
                source_height = float(pixmap.height())
                scale = min(1.0, 520.0 / source_width, 360.0 / source_height)
                width = max(48.0, source_width * scale)
                height = max(48.0, source_height * scale)
                x = max(0.0, min(float(point.x()) + index * 24, artboard.width() - width))
                y = max(0.0, min(float(point.y()) + index * 24, artboard.height() - height))
                last_source = Source(
                    source_type=SourceType.IMAGE,
                    name=path.stem,
                    x=x,
                    y=y,
                    width=width,
                    height=height,
                    border_radius=0.0,
                    content_path=str(path.resolve()),
                    text="",
                    z_index=len(self.store.sources()) + index,
                )
                self.store.add(last_source)
                count += 1
                if index % 4 == 0:
                    QApplication.processEvents()
            if last_source is not None:
                self.store.select(last_source.id)
            return count
        finally:
            self.activity_progress.finish("content_add")

    def _confirm_and_load_dropped_project(self, path: Path) -> None:
        """Ask before replacing the active work with a dropped project document."""
        korean = self.translator.language is Language.KOREAN
        if self._project_dirty:
            previous_project_path = self.current_project_path
            if (self._confirm_unsaved_changes()
                    and self._load_project_path(path)):
                try:
                    self.autosave.clear(previous_project_path)
                except ProjectError as error:
                    self.statusBar().showMessage(str(error), 5000)
            return
        response = QMessageBox.question(
            settings_dialog,
            "프로젝트 열기" if korean else "Open project",
            f"'{path.name}' 프로젝트를 열까요?\n현재 작업은 저장하지 않으면 사라질 수 있습니다."
            if korean else
            f"Open '{path.name}'?\nUnsaved current work may be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if response == QMessageBox.StandardButton.Yes:
            self._load_project_path(path)

    def _add_dropped_videos(self, paths: list[Path], position: object | None) -> int:
        """Create one video element whose ordered media list is the dropped files."""
        valid = [path.resolve() for path in paths if path.is_file()]
        if not valid:
            return 0
        artboard = self.canvas.scene_model.artboard_rect
        point = position if hasattr(position, "x") and hasattr(position, "y") else artboard.center()
        width, height = 480.0, 270.0
        source = Source(
            SourceType.VIDEO,
            valid[0].stem if len(valid) == 1 else f"Video playlist ({len(valid)})",
            x=max(0.0, min(float(point.x()) - width / 2, artboard.width() - width)),
            y=max(0.0, min(float(point.y()) - height / 2, artboard.height() - height)),
            width=width,
            height=height,
            content_path=str(valid[0]),
            video_paths=[str(path) for path in valid],
            video_repeat_mode="once" if len(valid) == 1 else "sequence",
            text="",
            border_radius=0.0,
            z_index=len(self.store.sources()),
        )
        self.store.add(source)
        self.store.select(source.id)
        return 1

    def _stage_export_frame(
        self, image: QImage, duration_seconds: float, stream_key: str = "base",
    ) -> RenderFrame:
        """Stage a frame synchronously or queue it to the active PNG pipeline."""
        if self._export_frame_staging is None:
            raise RenderError("Export frame staging has not been initialized.")
        if image.isNull():
            raise RenderError("Could not stage an empty export frame on disk.")
        self._export_capture_count += 1
        if self._export_frame_metrics is None:
            self._export_frame_metrics = ExportFrameStagingMetrics()
        self._export_frame_metrics.record_capture()
        previous = self._export_frame_cache.get(stream_key)
        if previous is not None and image == previous[0]:
            self._export_frame_metrics.record_reuse()
            return RenderFrame(previous[1], max(0.001, duration_seconds))
        # Disk usage queries are surprisingly expensive on synced/network-backed
        # Windows temp drives. Check periodically instead of once per PNG.
        if self._export_frame_index % 32 == 0:
            free_space = shutil.disk_usage(self._export_frame_staging.name).free
            minimum_free = max(512 * 1024 * 1024, image.width() * image.height() * 8)
            if free_space < minimum_free:
                raise RenderError(
                    "Not enough temporary disk space to safely prepare export frames. "
                    "Free at least 1 GB on the system temporary drive and try again."
                )
        path = Path(self._export_frame_staging.name) / f"frame_{self._export_frame_index:07d}.png"
        self._export_frame_index += 1
        owned_image = image.copy()
        pipeline = self._export_png_pipeline
        if pipeline is not None:
            try:
                pipeline.submit(owned_image, path, stream_key)
            except PngFrameStagingCancelled as error:
                raise RenderCancelledError(str(error)) from error
            except PngFrameStagingError as error:
                raise RenderError(str(error)) from error
        else:
            writer = QImageWriter(str(path), b"png")
            # Compression level 1 trades a little temporary disk space for much
            # faster preparation. FFmpeg output quality is unaffected.
            writer.setCompression(1)
            writer.setOptimizedWrite(False)
            if not writer.write(owned_image):
                raise RenderError(
                    f"Could not stage an export frame on disk: {writer.errorString()}"
                )
            try:
                staged_bytes = path.stat().st_size
            except OSError as error:
                # Diagnostics must never turn a successfully written export frame
                # into an export failure on an unusual or transient filesystem.
                LOGGER.warning("Could not measure staged export frame %s: %s", path, error)
                staged_bytes = 0
            self._export_frame_metrics.record_file(
                stream_key, owned_image, staged_bytes,
            )
        self._export_frame_cache[stream_key] = (owned_image, path)
        return RenderFrame(path, max(0.001, duration_seconds))

    def _start_export_png_pipeline(
        self, cancel_event: threading.Event, *, queue_capacity: int = 3,
    ) -> None:
        """Start bounded PNG writes so Canvas capture can continue concurrently."""
        if self._export_png_pipeline is not None:
            raise RenderError("Export PNG staging is already active.")
        if self._export_frame_metrics is None:
            self._export_frame_metrics = ExportFrameStagingMetrics()

        def record_written(stream_key: str, image: QImage, byte_count: int) -> None:
            metrics = self._export_frame_metrics
            if metrics is not None:
                metrics.record_file(stream_key, image, byte_count)

        self._export_png_pipeline = PngFrameStagingPipeline(
            record_written,
            cancel_event=cancel_event,
            wait_callback=QApplication.processEvents,
            queue_capacity=max(1, queue_capacity),
        )

    def _finish_export_png_pipeline(self) -> None:
        pipeline = self._export_png_pipeline
        if pipeline is None:
            return
        self._export_png_pipeline = None
        try:
            pipeline.finish()
        except PngFrameStagingCancelled as error:
            raise RenderCancelledError(str(error)) from error
        except PngFrameStagingError as error:
            raise RenderError(str(error)) from error
        LOGGER.info(
            "PNG frame staging pipeline drained: peak_buffered_frames=%d",
            pipeline.peak_buffered_frames,
        )

    def _cancel_export_png_pipeline(self) -> None:
        pipeline = self._export_png_pipeline
        self._export_png_pipeline = None
        if pipeline is not None:
            pipeline.cancel()

    @staticmethod
    def _export_animation_sample_rate(output_fps: int) -> int:
        """Sample Canvas motion at the exact frame rate selected for export."""
        return max(1, min(240, int(output_fps)))

    def _clear_export_frame_staging(self) -> None:
        """Release disk-backed captured frames after every export completion path."""
        self._stop_export_storage_monitor()
        self._cancel_export_png_pipeline()
        if self._export_frame_metrics is not None:
            summary = self._export_frame_metrics.snapshot()
            self._last_export_frame_metrics = summary
            LOGGER.info(
                "Export frame staging summary: captures=%d files=%d reused=%d "
                "bytes=%d largest=%d (%dx%d) elapsed=%.3fs streams=%s",
                summary.capture_count,
                summary.unique_file_count,
                summary.reused_frame_count,
                summary.total_bytes,
                summary.largest_file_bytes,
                summary.largest_width,
                summary.largest_height,
                summary.elapsed_seconds,
                {
                    key: {
                        "files": summary.stream_file_counts[key],
                        "bytes": summary.stream_bytes.get(key, 0),
                    }
                    for key in sorted(summary.stream_file_counts)
                },
            )
        if self._export_frame_staging is not None:
            self._export_frame_staging.cleanup()
            self._export_frame_staging = None
        self._export_frame_index = 0
        self._export_capture_count = 0
        self._export_frame_cache.clear()
        self._export_frame_metrics = None

    def _lock_main_form_for_export(self) -> None:
        """Block every main-form interaction while an export is in flight."""
        if self._export_ui_lock_state is not None:
            return
        central_widget = self.centralWidget()
        menu_bar = self.menuBar()
        self._export_ui_lock_state = (
            central_widget.isEnabled(),
            menu_bar.isEnabled(),
            self.toolbar.isEnabled(),
            self.export_action.isEnabled(),
            self.acceptDrops(),
        )
        central_widget.setEnabled(False)
        menu_bar.setEnabled(False)
        self.toolbar.setEnabled(False)
        self.export_action.setEnabled(False)
        self.setAcceptDrops(False)

    def _unlock_main_form_after_export(self) -> None:
        """Restore the main form after every successful, failed, or cancelled export."""
        self._export_restore_pending = False
        state = self._export_ui_lock_state
        if state is None:
            return
        self._export_ui_lock_state = None
        central_enabled, menu_enabled, toolbar_enabled, export_enabled, accepts_drops = state
        self.centralWidget().setEnabled(central_enabled)
        self.menuBar().setEnabled(menu_enabled)
        self.toolbar.setEnabled(toolbar_enabled)
        self.export_action.setEnabled(export_enabled)
        self.setAcceptDrops(accepts_drops)

    @staticmethod
    def _export_notification_allowed(
        settings: AppSettings,
        step: str,
        application_active: bool,
    ) -> bool:
        """Apply the master, per-stage, and focus notification preferences."""
        if not settings.export_notifications_enabled:
            return False
        enabled_for_step = {
            "visuals": settings.export_notify_visuals,
            "audio": settings.export_notify_audio,
            "effects": settings.export_notify_effects,
            "encode": settings.export_notify_encode,
            "complete": settings.export_notify_complete,
            "failures": settings.export_notify_failures,
        }.get(step, False)
        if not enabled_for_step:
            return False
        return (
            settings.export_notification_mode == "always"
            or not application_active
        )

    def _sync_export_notification_tray(self, settings: AppSettings) -> None:
        """Create a tray endpoint only while export notifications are enabled."""
        if not settings.export_notifications_enabled:
            if self._notification_tray is not None:
                self._notification_tray.hide()
                self._notification_tray.deleteLater()
                self._notification_tray = None
            return
        self._ensure_notification_tray()

    def _ensure_notification_tray(self) -> QSystemTrayIcon | None:
        if self._notification_tray is not None:
            return self._notification_tray
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        icon = QApplication.windowIcon()
        if icon.isNull():
            icon = self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        tray = QSystemTrayIcon(icon, self)
        tray.setToolTip("Playlist Canvas")
        tray.messageClicked.connect(self._restore_from_export_notification)
        tray.show()
        self._notification_tray = tray
        return tray

    def _restore_from_export_notification(self) -> None:
        """Bring the running export or completed workspace back to the user."""
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()
        if self._export_dialog is not None:
            self._export_restore_pending = False
            self._export_dialog.show()
            self._export_dialog.raise_()
            self._export_dialog.activateWindow()

    def _show_system_notification(
        self,
        title: str,
        message: str,
        *,
        critical: bool = False,
    ) -> bool:
        tray = self._ensure_notification_tray()
        if tray is None:
            return False
        icon = (
            QSystemTrayIcon.MessageIcon.Critical
            if critical else QSystemTrayIcon.MessageIcon.Information
        )
        tray.showMessage(title, message, icon, 7000)
        return True

    def _notify_export_stage(self, stage: str) -> None:
        """Notify once when the export crosses into a user-facing phase."""
        step = ExportProgressDialog._stage_key(stage)
        if step in self._export_notified_steps:
            return
        self._export_notified_steps.add(step)
        settings = self.settings_service.current
        application_active = (
            QApplication.applicationState() == Qt.ApplicationState.ApplicationActive
        )
        if not self._export_notification_allowed(
            settings, step, application_active,
        ):
            return
        korean = self.translator.language is Language.KOREAN
        names = {
            "visuals": "화면 준비" if korean else "Visual preparation",
            "audio": "오디오 준비" if korean else "Audio preparation",
            "effects": "효과 준비" if korean else "Effects preparation",
            "encode": "영상 만들기" if korean else "Creating video",
            "complete": "내보내기 완료" if korean else "Export complete",
        }
        name = names.get(step)
        if name is None:
            return
        output_name = (
            self._active_export_output_path.name
            if self._active_export_output_path is not None else ""
        )
        if step == "complete":
            title = "내보내기 완료" if korean else "Export complete"
            message = (
                f"{output_name} 파일을 만들었습니다."
                if korean else f"Created {output_name}."
            )
        else:
            title = "내보내기 진행" if korean else "Export progress"
            message = (
                f"{name} 단계를 시작했습니다."
                if korean else f"Started: {name}."
            )
        self._show_system_notification(title, message)

    def _notify_export_problem(self, message: str, *, cancelled: bool = False) -> None:
        settings = self.settings_service.current
        application_active = (
            QApplication.applicationState() == Qt.ApplicationState.ApplicationActive
        )
        if not self._export_notification_allowed(
            settings, "failures", application_active,
        ):
            return
        korean = self.translator.language is Language.KOREAN
        if cancelled:
            title = "내보내기 취소" if korean else "Export cancelled"
            detail = (
                "진행 중인 내보내기를 안전하게 취소했습니다."
                if korean else "The active export was cancelled safely."
            )
        else:
            title = "내보내기 오류" if korean else "Export failed"
            detail = message.strip().replace("\n", " ")[:220]
        self._show_system_notification(
            title, detail, critical=not cancelled,
        )

    def _handle_export_render_progress(
        self,
        export_dialog: ExportProgressDialog,
        stage: str,
        fraction: float,
        message: str,
    ) -> None:
        """Keep progress UI, status activity, and notifications synchronized."""
        overall = (
            EXPORT_PREPARATION_PROGRESS_WEIGHT
            + (1.0 - EXPORT_PREPARATION_PROGRESS_WEIGHT) * fraction
        )
        export_dialog.update_progress(stage, overall, message)
        self.activity_progress.update(
            "export", overall, f"{stage} · {message}",
        )
        self._notify_export_stage(stage)

    @staticmethod
    def _format_bytes(count: int) -> str:
        value = float(max(0, count))
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024.0:
                return f"{value:.1f} {unit}"
            value /= 1024.0
        return f"{value:.1f} TB"

    def _start_export_storage_monitor(self, output_path: str | Path) -> None:
        """Track export-owned files without walking large folders on the UI thread."""
        self._stop_export_storage_monitor()
        monitor = ExportStorageMonitor(output_path, self)
        if self._export_frame_staging is not None:
            monitor.set_path("frames", self._export_frame_staging.name)
        monitor.snapshot_ready.connect(self._handle_export_storage_snapshot)
        self._export_storage_monitor = monitor
        monitor.start()
        dialog = self._export_dialog
        if dialog is not None:
            dialog.cancel_requested.connect(self._freeze_export_storage_monitor)

    def _freeze_export_storage_monitor(self) -> None:
        """Stop live storage sampling the instant a cancel is confirmed.

        Cancellation stops the encoders quickly, but a still-polling monitor kept
        showing the last few numbers and made the export look like it was still
        writing. Halt sampling immediately; cleanup still runs in _stop_...().
        """
        monitor = self._export_storage_monitor
        if monitor is not None:
            monitor.stop()

    def _handle_export_storage_path(
        self, kind: str, path: str | Path | None,
    ) -> None:
        monitor = self._export_storage_monitor
        if monitor is not None:
            monitor.set_path(kind, path)

    def _handle_export_storage_snapshot(self, snapshot: object) -> None:
        dialog = self._export_dialog
        if dialog is not None:
            dialog.update_storage_snapshot(snapshot)

    def _stop_export_storage_monitor(self) -> None:
        monitor = self._export_storage_monitor
        self._export_storage_monitor = None
        if monitor is None:
            return
        monitor.stop()
        if not monitor.wait(3000):
            LOGGER.warning("Export storage monitor did not stop within three seconds.")
        monitor.deleteLater()

    def _prepare_export_staging_space(
        self, render_settings: RenderSettings, duration_seconds: float,
        layer_count: int, use_streamed_visuals: bool, korean: bool,
    ) -> bool:
        """Redirect frame staging to the output drive if the temp drive is short,
        and warn before starting when neither drive has comfortable room.

        Returns ``False`` only when the user declines to continue anyway.
        """
        raw_frame_bytes = max(
            1, render_settings.output_width * render_settings.output_height * 3
        )
        seconds = max(0.0, duration_seconds)
        # ffv1 / libx264rgb (or deflated PNGs) on Canvas content: conservatively
        # ~40% of raw RGB, once per visual layer.
        intermediate = int(
            raw_frame_bytes * render_settings.fps * seconds
            * 0.4 * max(1, layer_count if use_streamed_visuals else 1)
        )
        final_video = int(raw_frame_bytes * render_settings.fps * seconds * 0.08)
        temp_need = int(intermediate * 1.3)
        output_need = int(intermediate * 0.4) + final_video

        def free_bytes(location: Path) -> int | None:
            try:
                return shutil.disk_usage(location).free
            except OSError:
                return None

        system_temp = Path(tempfile.gettempdir())
        output_parent = (
            self._active_export_output_path.parent
            if self._active_export_output_path is not None else None
        )
        staging_dir: Path | None = None
        temp_free = free_bytes(system_temp)
        if (
            temp_free is not None
            and temp_free < int(temp_need * 1.15)
            and output_parent is not None
        ):
            output_free = free_bytes(output_parent)
            if (
                output_free is not None
                and output_free > int((temp_need + output_need) * 1.2)
            ):
                staging_dir = output_parent
                LOGGER.info(
                    "Staging export frames on the output drive (%s); the system "
                    "temporary drive is short on space.", output_parent,
                )
        self._clear_export_frame_staging()
        self._export_frame_staging = TemporaryDirectory(
            prefix="playlist-video-frames-",
            dir=str(staging_dir) if staging_dir is not None else None,
        )
        self._export_frame_index = 0

        checks = [(Path(self._export_frame_staging.name), temp_need)]
        if output_parent is not None:
            checks.append((output_parent, output_need))
        shortfalls: list[str] = []
        for location, required in checks:
            free = free_bytes(location)
            if free is not None and free < int(required * 1.15):
                drive = location.anchor or str(location)
                shortfalls.append(
                    f"{drive}  —  {self._format_bytes(free)} free / "
                    f"~{self._format_bytes(required)} needed"
                )
        if not shortfalls:
            return True
        detail = "\n".join(shortfalls)
        answer = QMessageBox.warning(
            self,
            "저장 공간 부족 가능성" if korean else "Low disk space",
            (
                "무손실 중간 파일과 최종 영상을 저장할 임시/출력 공간이 부족할 수 "
                "있습니다. 내보내는 도중 공간이 모자라면 실패할 수 있습니다.\n\n"
                f"{detail}\n\n그래도 계속 진행할까요?"
                if korean else
                "The temporary or output drive may not have enough room for the "
                "lossless intermediate files and the final video, so the export "
                "could fail partway through.\n\n"
                f"{detail}\n\nContinue anyway?"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _report_export_preparation_progress(
        self, fraction: float, detail: str,
    ) -> None:
        """Show one Canvas-preparation progress update from the export session."""
        if self._export_dialog is None:
            return
        self._export_dialog.update_progress(
            "Preparing visual frames", fraction, detail,
        )
        self.activity_progress.update("export", fraction, detail)

    def _cancel_active_export_session(self) -> None:
        """Stop the running export session's encoders and PNG pipeline, if any."""
        session = self._active_export_session
        self._active_export_session = None
        if session is not None:
            session.cancel_streams()
        else:
            self._cancel_export_png_pipeline()

    def _resolve_export_render_settings(
        self, renderer: FFmpegRenderer, requested_app_settings: AppSettings,
        output: str, save_as_default: bool,
    ) -> tuple[list, RenderSettings, AppSettings, str, bool] | None:
        """Resolve the encoder and run the export preflight.

        Returns ``(active_tracks, render_settings, effective_app_settings,
        encoder_label, automatic)`` on success, or ``None`` when the user
        cancelled or the preflight failed (a dialog was already shown).
        """
        korean = self.translator.language is Language.KOREAN
        automatic_encoder = (
            requested_app_settings.video_codec == AUTO_VIDEO_ENCODER
        )
        effective_encoder = (
            VideoEncoderAdvisor.automatic_encoder()
            if automatic_encoder else requested_app_settings.video_codec
        )
        selected_app_settings = replace(
            requested_app_settings, video_codec=effective_encoder,
        )
        active_tracks = [track for track in self.playlist_service.tracks if track.enabled]
        if not active_tracks:
            QMessageBox.warning(
                self,
                "내보내기 오류" if korean else "Export error",
                "내보낼 음악을 하나 이상 선택하세요."
                if korean else "Select at least one music track to export.",
            )
            return None
        render_settings = selected_app_settings.render_settings()
        try:
            renderer.preflight_export(active_tracks, output, render_settings)
        except EncoderUnavailableError as error:
            if not (
                automatic_encoder
                and effective_encoder == NVIDIA_H264_ENCODER
            ):
                QMessageBox.critical(
                    self,
                    "내보내기 사전 검사 실패" if korean else "Export preflight failed",
                    str(error),
                )
                return None
            answer = QMessageBox.warning(
                self,
                "NVIDIA 인코더 사용 실패" if korean else "NVIDIA encoder failed",
                (
                    "NVIDIA GPU를 감지하여 NVENC 인코더를 자동으로 시도했지만 "
                    "사용할 수 없습니다. 그래픽 드라이버, 다른 프로그램의 GPU 인코딩 "
                    "사용 또는 FFmpeg 호환성 문제일 수 있습니다.\n\n"
                    f"오류 내용:\n{error}\n\n"
                    "CPU H.264 인코더로 다시 검사하고 내보내기를 계속할까요? "
                    "속도는 느릴 수 있지만 결과 영상의 해상도와 FPS는 유지됩니다."
                )
                if korean else
                (
                    "An NVIDIA GPU was detected and NVENC was tried automatically, "
                    "but it could not be used. The GPU driver, another application's "
                    "encoder session, or FFmpeg compatibility may be the cause.\n\n"
                    f"Error:\n{error}\n\n"
                    "Retry the preflight and continue export with CPU H.264? It may "
                    "be slower, but the output resolution and FPS will be preserved."
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return None
            effective_encoder = CPU_H264_ENCODER
            selected_app_settings = replace(
                requested_app_settings, video_codec=effective_encoder,
            )
            render_settings = selected_app_settings.render_settings()
            try:
                renderer.preflight_export(active_tracks, output, render_settings)
            except RenderError as cpu_error:
                QMessageBox.critical(
                    self,
                    "CPU 인코더 검사 실패" if korean else "CPU encoder check failed",
                    str(cpu_error),
                )
                return None
        except RenderError as error:
            QMessageBox.critical(
                self, "내보내기 사전 검사 실패" if korean else "Export preflight failed",
                str(error),
            )
            return None
        if save_as_default:
            # Preserve "Automatic" as the preference; the concrete encoder is
            # selected again for the hardware available at the next export.
            self.settings_service.save(requested_app_settings)
        encoder_name = next(
            (
                label for label, codec in VIDEO_ENCODERS.items()
                if codec == selected_app_settings.video_codec
            ),
            selected_app_settings.video_codec,
        )
        if automatic_encoder:
            encoder_name = (
                f"자동 선택 → {encoder_name}"
                if korean else f"Automatic → {encoder_name}"
            )
        return (
            active_tracks, render_settings, selected_app_settings,
            encoder_name, automatic_encoder,
        )

    def _export_video(self) -> None:
        """Render the static Canvas and enabled playlist tracks to an MP4 file."""
        korean = self.translator.language is Language.KOREAN
        try:
            configured_path = self.settings_service.current.ffmpeg_path or None
            renderer = FFmpegRenderer(configured_path)
        except FFmpegNotFoundError:
            QMessageBox.warning(
                self,
                "FFmpeg 필요" if korean else "FFmpeg required",
                (
                    "영상을 내보내려면 FFmpeg 설치가 필요합니다.\n\n"
                    "확인을 누르면 설정의 FFmpeg 설치 화면으로 이동합니다. "
                    "자동 설치를 사용하거나 기존 ffmpeg.exe를 선택해 주세요."
                )
                if korean else (
                    "FFmpeg is required to export a video.\n\n"
                    "Click OK to open the FFmpeg setup page. Use automatic "
                    "installation or select an existing ffmpeg executable."
                ),
            )
            self._show_settings(focus_ffmpeg=True)
            return
        active_tracks = [track for track in self.playlist_service.tracks if track.enabled]
        if not active_tracks:
            QMessageBox.warning(self, "Export error", "Select at least one music track to export.")
            return
        invalid_track = next(
            (track for track in active_tracks if track.duration_seconds <= 0.0), None
        )
        if invalid_track is not None:
            QMessageBox.warning(
                self,
                "음원 길이 오류" if korean else "Invalid audio duration",
                (f"'{invalid_track.title}' 곡의 길이를 확인할 수 없습니다. "
                 "FFmpeg 설정과 원본 음원을 확인한 뒤 다시 추가해 주세요.")
                if korean else
                (f"The duration of '{invalid_track.title}' could not be determined. "
                 "Check FFmpeg and the source audio, then add the track again."),
            )
            return
        output_directory = self.settings_service.current.output_directory
        default_directory = (
            Path(output_directory) if output_directory
            else self.settings_service.default_output_directory()
        )
        export_options = ExportSettingsDialog(
            self.settings_service.current,
            len(active_tracks),
            self._playlist_duration(active_tracks),
            self.translator,
            default_directory / "playlist.mp4",
            self,
            canvas_size=(
                round(self.canvas.scene_model.artboard_rect.width()),
                round(self.canvas.scene_model.artboard_rect.height()),
            ),
            estimated_layer_count=min(3, max(1, len(self.store.sources()))),
        )
        if export_options.exec() != export_options.DialogCode.Accepted:
            return
        requested_app_settings = export_options.app_settings
        quality_profile_name = export_options.quality_mode_combo.currentText()
        output = str(export_options.output_path)
        resolved = self._resolve_export_render_settings(
            renderer, requested_app_settings, output,
            export_options.save_as_default,
        )
        if resolved is None:
            return
        (
            active_tracks, render_settings, selected_app_settings,
            encoder_name, automatic_encoder,
        ) = resolved
        render_scale = canvas_render_scale(
            self.canvas.scene_model, render_settings,
        )
        upscale_warnings = self._export_upscale_warnings(
            active_tracks, render_settings, render_scale, korean,
        )
        if upscale_warnings:
            listed = "\n".join(upscale_warnings[:5])
            if QMessageBox.question(
                self,
                "낮은 해상도 이미지" if korean else "Low-resolution images",
                (
                    "다음 이미지 소스는 원본 해상도보다 크게 표시되어 흐릿할 수 "
                    f"있습니다:\n\n{listed}\n\n더 큰 이미지를 사용하면 화질이 "
                    "좋아집니다. 그대로 내보낼까요?"
                    if korean else
                    "These image sources are shown larger than their source "
                    f"resolution and may look soft:\n\n{listed}\n\nUsing larger "
                    "images improves quality. Export anyway?"
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                return
        work_mode_name = {
            WORK_MODE_STABLE: "안정" if korean else "Stable",
            WORK_MODE_AUTO: "자동" if korean else "Automatic",
            WORK_MODE_MAX_SPEED: "최대 속도" if korean else "Maximum speed",
        }.get(
            render_settings.work_mode,
            "자동" if korean else "Automatic",
        )
        settings_summary = (
            (
                f"해상도 {render_settings.output_width} × {render_settings.output_height}"
                f" · {selected_app_settings.fps} FPS\n"
                f"비디오 인코더 {encoder_name}\n"
                f"작업 모드 {work_mode_name} · "
                f"품질 모드 {quality_profile_name} · CRF {selected_app_settings.crf}"
                f" · 인코딩 속도 {selected_app_settings.preset}"
                f" · 오디오 AAC {selected_app_settings.audio_bitrate}"
            )
            if korean else
            (
                f"Resolution {render_settings.output_width} × {render_settings.output_height}"
                f" · {selected_app_settings.fps} FPS\n"
                f"Video encoder {encoder_name}\n"
                f"Work mode {work_mode_name} · "
                f"Quality mode {quality_profile_name} · CRF {selected_app_settings.crf}"
                f" · Encoding speed {selected_app_settings.preset}"
                f" · Audio AAC {selected_app_settings.audio_bitrate}"
            )
        )
        self._export_notified_steps.clear()
        self._active_export_output_path = Path(output).expanduser().resolve()
        self._pending_export_result = None
        preparation_cancel = threading.Event()
        self._export_preparation_cancel = preparation_cancel
        self._export_dialog = ExportProgressDialog(self)
        self._export_dialog.set_korean(korean)
        self._export_dialog.set_export_details(
            len(active_tracks), self._playlist_duration(active_tracks),
            settings_summary, output,
        )
        initial_storage_estimate = estimate_export_storage(
            render_settings.output_width, render_settings.output_height,
            render_settings.fps, self._playlist_duration(active_tracks),
            selected_app_settings.crf, selected_app_settings.audio_bitrate,
            min(3, max(1, len(self.store.sources()))),
        )
        self._export_dialog.set_storage_estimate(initial_storage_estimate)
        self._export_dialog.set_busy(
            "Preparing visual frames",
            "캔버스와 애니메이션 프레임을 준비하고 있습니다."
            if korean else "Capturing Canvas and animation frames.",
        )
        request_preparation_cancel = preparation_cancel.set
        self._export_dialog.cancel_requested.connect(request_preparation_cancel)
        self._export_dialog.minimize_requested.connect(
            self._minimize_during_export
        )
        self._export_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self._lock_main_form_for_export()
        self.activity_progress.begin(
            "export", "영상 내보내기" if korean else "Exporting video",
            detail="화면 프레임 준비 중" if korean else "Preparing visual frames",
        )
        self._notify_export_stage("Preparing visual frames")
        self.statusBar().showMessage(
            "화면 프레임 준비 중..." if korean else "Preparing visual frames..."
        )
        try:
            self._export_dialog.show()
            QApplication.processEvents()
            self._clear_export_frame_staging()
            self._export_frame_staging = TemporaryDirectory(
                prefix="playlist-video-frames-"
            )
            self._export_frame_index = 0
        except Exception as error:
            self._export_preparation_cancel = None
            self._clear_export_frame_staging()
            if self._export_dialog:
                self._export_dialog.complete(False)
                self._export_dialog = None
            self._unlock_main_form_after_export()
            self.activity_progress.finish("export")
            report_unexpected_error("Starting export preparation", error)
            self._notify_export_problem(str(error))
            self._active_export_output_path = None
            QMessageBox.critical(
                self, "내보내기 오류" if korean else "Export error", str(error)
            )
            self._resume_close_after_export_cancel()
            return
        self._active_export_session = None
        try:
            animation_fps = self._export_animation_sample_rate(render_settings.fps)
            playlist_duration = self._playlist_duration(active_tracks)
            visualizers = self._export_visualizers(active_tracks, render_scale)
            video_clips = self._export_video_clips(
                active_tracks, playlist_duration, render_settings.work_mode,
                render_scale,
            )
            plan = build_export_plan(
                self.canvas.scene_model, active_tracks, self.store.sources(),
                render_settings, playlist_duration, visualizers, video_clips,
                renderer, animation_fps,
            )
            if not self._prepare_export_staging_space(
                render_settings, playlist_duration, len(plan.z_bands),
                plan.use_streamed_visuals, korean,
            ):
                raise RenderCancelledError("Export cancelled at the disk-space check.")
            actual_storage_estimate = estimate_export_storage(
                render_settings.output_width, render_settings.output_height,
                render_settings.fps, playlist_duration,
                selected_app_settings.crf, selected_app_settings.audio_bitrate,
                max(1, len(plan.z_bands)),
            )
            self._export_dialog.set_storage_estimate(actual_storage_estimate)
            self._start_export_storage_monitor(output)
            stream_root = (
                Path(self._export_frame_staging.name)
                if plan.use_streamed_visuals and self._export_frame_staging is not None
                else None
            )
            export_session = ExportSession(
                scene=self.canvas.scene_model,
                renderer=renderer,
                plan=plan,
                render_settings=render_settings,
                active_tracks=active_tracks,
                stream_root=stream_root,
                preparation_cancel=preparation_cancel,
                korean=korean,
                staging=PngStaging(
                    stage_frame=self._stage_export_frame,
                    start_pipeline=lambda capacity: self._start_export_png_pipeline(
                        preparation_cancel, queue_capacity=capacity,
                    ),
                    finish_pipeline=self._finish_export_png_pipeline,
                    cancel_pipeline=self._cancel_export_png_pipeline,
                    pending_frames=lambda: (
                        self._export_png_pipeline.pending_frames
                        if self._export_png_pipeline is not None else 0
                    ),
                    queue_capacity=self._export_png_queue_capacity(render_settings),
                ),
                layer_worker_count=lambda count: self._export_layer_worker_count(
                    render_settings, count,
                ),
                report_progress=self._report_export_preparation_progress,
                pump_ui=QApplication.processEvents,
            )
            self._active_export_session = export_session
            artifacts = export_session.run()
            frames = artifacts.frames
            static_layers = artifacts.static_layers
        except RenderCancelledError:
            self._cancel_active_export_session()
            self._export_preparation_cancel = None
            self._clear_export_frame_staging()
            if self._export_dialog:
                self._export_dialog.complete(False)
                self._export_dialog = None
            self._unlock_main_form_after_export()
            self.activity_progress.finish("export")
            self._notify_export_problem(
                "Export preparation was cancelled.", cancelled=True,
            )
            self._active_export_output_path = None
            self.statusBar().showMessage(
                "내보내기를 취소했습니다." if korean else "Export cancelled.", 5000
            )
            self._resume_close_after_export_cancel()
            return
        except RenderError as error:
            self._cancel_active_export_session()
            self._export_preparation_cancel = None
            self._clear_export_frame_staging()
            if self._export_dialog:
                self._export_dialog.complete(False)
                self._export_dialog = None
            self._unlock_main_form_after_export()
            self.activity_progress.finish("export")
            self._notify_export_problem(str(error))
            self._active_export_output_path = None
            QMessageBox.critical(
                self, "내보내기 오류" if korean else "Export error", str(error)
            )
            self._resume_close_after_export_cancel()
            return
        except Exception as error:
            self._cancel_active_export_session()
            self._export_preparation_cancel = None
            self._clear_export_frame_staging()
            if self._export_dialog:
                self._export_dialog.complete(False)
                self._export_dialog = None
            self._unlock_main_form_after_export()
            self.activity_progress.finish("export")
            report_unexpected_error("Preparing export frames", error)
            self._notify_export_problem(str(error))
            self._active_export_output_path = None
            QMessageBox.critical(
                self, "내보내기 오류" if korean else "Export error", str(error)
            )
            self._resume_close_after_export_cancel()
            return
        self._export_preparation_cancel = None
        try:
            self._export_dialog.cancel_requested.disconnect(request_preparation_cancel)
        except (RuntimeError, TypeError):
            pass
        if korean:
            self._export_dialog.setWindowTitle("내보내기 진행 상황")
            self._export_dialog.cancel_button.setText("취소")
            self._export_dialog.stage_label.setText("내보내기 준비 중")
        project_title = self.project_settings.title.strip()
        export_metadata = ExportMetadata(
            title=(
                "" if project_title in ("", "Untitled Project", "제목 없는 프로젝트")
                else project_title
            ),
            artist=self.project_settings.author.strip(),
            comment=self.project_settings.description.strip(),
        )
        self._render_worker = RenderWorker(
            renderer,
            frames,
            self.playlist_service.tracks,
            output,
            render_settings,
            visualizers,
            static_layers,
            video_clips,
            export_metadata,
        )
        export_dialog = self._export_dialog
        self._render_worker.progress.connect(
            lambda stage, fraction, message: self._handle_export_render_progress(
                export_dialog, stage, fraction, message,
            )
        )
        storage_path_signal = getattr(
            self._render_worker, "storage_path_changed", None,
        )
        if storage_path_signal is not None:
            storage_path_signal.connect(self._handle_export_storage_path)
        self._render_worker.succeeded.connect(self._export_succeeded)
        self._render_worker.failed.connect(self._export_failed)
        self._render_worker.cancelled.connect(self._export_cancelled)
        self._render_worker.finished.connect(self._export_finished)
        self._export_dialog.cancel_requested.connect(self._render_worker.cancel)
        self.statusBar().showMessage("렌더링 중..." if korean else "Rendering...")
        self._render_worker.start()

    def _minimize_during_export(self) -> None:
        """Minimize the app while keeping preparation or rendering active."""
        if self._export_dialog is None or self._export_ui_lock_state is None:
            return
        self._export_restore_pending = True
        self._export_dialog.hide()
        self.showMinimized()

    def _restore_export_dialog_after_minimize(self) -> None:
        """Bring the modal progress UI back when the taskbar window is restored."""
        if not self._export_restore_pending or self.isMinimized():
            return
        self._export_restore_pending = False
        if self._export_dialog is None or self._export_ui_lock_state is None:
            return
        self._export_dialog.show()
        self._export_dialog.raise_()
        self._export_dialog.activateWindow()

    def changeEvent(self, event: QEvent) -> None:
        """React to major taskbar-window transitions after Qt lays them out."""
        super().changeEvent(event)
        if event.type() != QEvent.Type.WindowStateChange:
            return
        old_state = (
            event.oldState() if hasattr(event, "oldState")
            else Qt.WindowState.WindowNoState
        )
        new_state = self.windowState()
        self._handle_canvas_window_state_change(old_state, new_state)
        if self._export_restore_pending and not self.isMinimized():
            QTimer.singleShot(0, self._restore_export_dialog_after_minimize)

    def _handle_canvas_window_state_change(
        self, old_state: Qt.WindowState, new_state: Qt.WindowState,
    ) -> None:
        """Fit after maximize/restore without resetting zoom on ordinary resizes."""
        was_minimized = bool(old_state & Qt.WindowState.WindowMinimized)
        is_minimized = bool(new_state & Qt.WindowState.WindowMinimized)
        maximize_changed = bool(
            old_state & Qt.WindowState.WindowMaximized
        ) != bool(new_state & Qt.WindowState.WindowMaximized)
        fullscreen_changed = bool(
            old_state & Qt.WindowState.WindowFullScreen
        ) != bool(new_state & Qt.WindowState.WindowFullScreen)
        if is_minimized:
            self._canvas_fit_pending = True
            self._canvas_fit_timer.stop()
            return
        if was_minimized or maximize_changed or fullscreen_changed:
            self._schedule_canvas_fit(100)

    def _schedule_canvas_fit(self, delay_ms: int = 0) -> None:
        """Coalesce layout-driven fit requests until the Canvas is usable."""
        self._canvas_fit_pending = True
        if self.isMinimized():
            self._canvas_fit_timer.stop()
            return
        self._canvas_fit_timer.start(max(0, int(delay_ms)))

    def _apply_scheduled_canvas_fit(self) -> None:
        """Apply one pending fit without making the project look edited."""
        if not self._canvas_fit_pending:
            return
        if self.isMinimized() or self._inline_preview is not None:
            return
        self._canvas_fit_pending = False
        restoring = self._history_restoring
        self._history_restoring = True
        try:
            self.canvas.fit_artboard()
        finally:
            self._history_restoring = restoring

    def _open_playlist_preview(self) -> None:
        """Select the bottom Preview tab and start its embedded playback mode."""
        preview_index = 2
        if self.bottom_tabs.currentIndex() != preview_index:
            self._show_bottom_panel(preview_index)
            return
        if self._inline_preview is not None:
            self.canvas_stack.setCurrentWidget(self._inline_preview)
            self._inline_preview.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        tracks = [track for track in self.playlist_service.tracks if track.enabled]
        if not tracks:
            QMessageBox.warning(
                self,
                "미리보기" if self.translator.language is Language.KOREAN else "Preview",
                (
                    "미리보기를 시작하려면 활성화된 곡을 한 개 이상 추가해 주세요."
                    if self.translator.language is Language.KOREAN else
                    "Add at least one enabled track before opening Preview."
                ),
            )
            self._select_edit_bottom_tab(self._last_edit_bottom_tab)
            return
        self._show_export_preview(tracks)

    def _bottom_workspace_tab_changed(self, index: int) -> None:
        """Enter Preview from its tab and restore editing from either edit tab."""
        if self._bottom_tab_change_guard:
            return
        if index == 2:
            self._open_playlist_preview()
            return
        if index not in {0, 1}:
            return
        self._last_edit_bottom_tab = index
        QSettings().setValue("workspace/bottom_tab", index)
        if self._inline_preview is not None:
            self._finish_inline_preview()

    def _select_edit_bottom_tab(self, index: int | None = None) -> None:
        """Select one persisted editing tab without recursively changing modes."""
        selected = max(0, min(1, self._last_edit_bottom_tab if index is None else index))
        self._last_edit_bottom_tab = selected
        self._bottom_tab_change_guard = True
        try:
            self.bottom_tabs.setCurrentIndex(selected)
        finally:
            self._bottom_tab_change_guard = False
        QSettings().setValue("workspace/bottom_tab", selected)

    def _show_export_preview(self, tracks: list) -> None:
        """Show a track-aware playback preview in the main Canvas workspace."""
        if self._inline_preview is not None:
            self.canvas_stack.setCurrentWidget(self._inline_preview)
            self._inline_preview.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        executable = None
        try:
            executable = FFmpegRenderer(
                self.settings_service.current.ffmpeg_path or None
            ).executable
        except FFmpegNotFoundError:
            pass
        preview = ExportPreviewDialog(
            self.canvas.scene_model, tracks, self.translator,
            self._export_visualizers(tracks), executable, self, source_store=self.store,
            embedded=True,
            preferred_backend=self._preview_backend_for_session,
        )
        controls_page = preview.build_embedded_controls_page()
        self._inline_preview = preview
        self._inline_preview_controls = controls_page
        preview.finished.connect(self._finish_inline_preview)
        self.canvas_stack.addWidget(preview)
        self.preview_tab_layout.addWidget(controls_page)
        self.canvas_stack.setCurrentWidget(preview)
        self._lock_editor_for_inline_preview()
        track_panel = getattr(preview, "track_list_panel", None)
        if isinstance(track_panel, QWidget):
            self._inline_preview_track_panel = track_panel
            track_panel.setMinimumWidth(0)
            track_panel.setMaximumWidth(16_777_215)
            track_panel.setStyleSheet(controls_page.styleSheet())
            self.preview_track_inspector_layout.addWidget(track_panel)
        self.inspector_stack.setCurrentWidget(self.preview_track_inspector)
        self.activity_progress.begin(
            "inline_preview",
            "캔버스 미리보기" if self.translator.language is Language.KOREAN
            else "Canvas preview",
            detail=(
                "미리보기 중에는 편집 기능이 잠깁니다."
                if self.translator.language is Language.KOREAN else
                "Editing is locked during playback preview."
            ),
        )
        self.statusBar().showMessage(
            "캔버스에서 전체 미리보기를 재생합니다 · 편집 기능이 잠겼습니다."
            if self.translator.language is Language.KOREAN else
            "Playing the full preview on the Canvas · Editing is locked."
        )
        preview.show()
        preview.setFocus(Qt.FocusReason.OtherFocusReason)

    def _lock_editor_for_inline_preview(self) -> None:
        """Lock project mutation while keeping bottom mode tabs interactive."""
        if self._preview_ui_lock_state is not None:
            return
        widgets = (self.canvas,)
        actions = tuple(
            (action, action.isEnabled()) for action in self.findChildren(QAction)
        )
        self._preview_ui_lock_state = {
            "widgets": tuple((widget, widget.isEnabled()) for widget in widgets),
            "menu": self.menuBar().isEnabled(),
            "toolbar": self.toolbar.isEnabled(),
            "toolbar_visible": not self.toolbar.isHidden(),
            "drops": self.acceptDrops(),
            "actions": actions,
            "left_visible": not self.left_workspace.isHidden(),
            "inspector_visible": not self.inspector_stack.isHidden(),
            "inspector_page": self.inspector_stack.currentWidget(),
            "workspace_sizes": tuple(self.workspace_splitter.sizes()),
            "main_splitter_sizes": tuple(self.main_splitter.sizes()),
            "sidebar_open_width": max(
                180, self.left_workspace.width(), self._sidebar_open_width,
            ),
        }
        for widget in widgets:
            widget.setEnabled(False)
        for action, _enabled in actions:
            action.setEnabled(False)
        self.menuBar().setEnabled(False)
        self.toolbar.setEnabled(False)
        self._set_sidebar_visible(False, persist=False, sync_action=False)
        sizes = self.workspace_splitter.sizes()
        if len(sizes) == 2:
            total = max(600, sum(sizes))
            controls_height = min(250, max(210, round(total * 0.27)))
            self.workspace_splitter.setSizes([
                max(280, total - controls_height), controls_height,
            ])
        self.setAcceptDrops(False)

    def _finish_inline_preview(self, _result: int = 0) -> None:
        """Return from the embedded playback page to the editable Canvas."""
        preview = self._inline_preview
        if preview is None:
            return
        controls_page = self._inline_preview_controls
        track_panel = self._inline_preview_track_panel
        self._inline_preview = None
        self._inline_preview_controls = None
        self._inline_preview_track_panel = None
        preview._stop_preview()
        self.canvas_stack.setCurrentWidget(self.canvas)
        self.canvas_stack.removeWidget(preview)
        if track_panel is not None:
            self.preview_track_inspector_layout.removeWidget(track_panel)
            track_panel.setParent(preview)
        if controls_page is not None:
            self.preview_tab_layout.removeWidget(controls_page)
            controls_page.deleteLater()
        preview.deleteLater()
        self._unlock_editor_after_inline_preview()
        if self.bottom_tabs.currentIndex() == 2:
            self._select_edit_bottom_tab()
        self.activity_progress.finish("inline_preview")
        self.statusBar().showMessage(
            "미리보기를 종료하고 캔버스 편집으로 돌아왔습니다."
            if self.translator.language is Language.KOREAN else
            "Preview closed; returned to Canvas editing.",
            2500,
        )
        self.canvas.setFocus(Qt.FocusReason.OtherFocusReason)
        # The left workspace expands for 190 ms when Preview releases its UI
        # lock. Fit against the final layout, not the transient narrow Canvas.
        self._schedule_canvas_fit(230)

    def _unlock_editor_after_inline_preview(self) -> None:
        """Restore exactly the interaction state that preceded inline preview."""
        state = self._preview_ui_lock_state
        if state is None:
            return
        self._preview_ui_lock_state = None
        for widget, enabled in state["widgets"]:
            widget.setEnabled(enabled)
        for action, enabled in state["actions"]:
            action.setEnabled(enabled)
        self.menuBar().setEnabled(bool(state["menu"]))
        self.toolbar.setEnabled(bool(state["toolbar"]))
        self.toolbar.setVisible(bool(state["toolbar_visible"]))
        self._sidebar_open_width = max(
            180, int(state.get("sidebar_open_width", self._sidebar_open_width)),
        )
        self._set_sidebar_visible(
            bool(state["left_visible"]), persist=False, sync_action=False,
            restore_sizes=list(state.get("main_splitter_sizes", ())),
        )
        inspector_page = state.get("inspector_page", self.inspector)
        if isinstance(inspector_page, QWidget):
            self.inspector_stack.setCurrentWidget(inspector_page)
        else:
            self.inspector_stack.setCurrentWidget(self.inspector)
        self.inspector_stack.setVisible(bool(state["inspector_visible"]))
        workspace_sizes = list(state["workspace_sizes"])
        if len(workspace_sizes) == 2:
            self.workspace_splitter.setSizes(workspace_sizes)
        self.setAcceptDrops(bool(state["drops"]))
        self._sync_canvas_shortcut_actions(None, self.canvas)
        self._update_alignment_toolbar_actions()

    def _preview_source_animation(self, source_id: str) -> None:
        """Play one source's configured animation directly on the Canvas.

        The preview is non-blocking: the window stays interactive and the very
        next user action (a click, key press, selection change or edit) stops it
        and snaps the source back to its real position.
        """
        source = self.store.get(source_id)
        item = self.canvas._items.get(source_id)
        if source is None or item is None:
            return
        if self._animation_preview_active:
            self.animation_preview_controller.cancel()
        self._animation_preview_active = True
        self._animation_preview_cancel_armed = False
        korean = self.translator.language is Language.KOREAN
        self.statusBar().showMessage(
            "애니메이션 미리보기 재생 중 · 다른 동작을 하면 중단됩니다."
            if korean else
            "Playing animation preview · Any further action stops it.",
            2500,
        )
        if not self.animation_preview_controller.preview(item, source):
            self._finish_canvas_animation_preview()
            return
        self.canvas.viewport().installEventFilter(self)
        QTimer.singleShot(0, self._arm_animation_preview_cancel)

    def _arm_animation_preview_cancel(self) -> None:
        """Start honouring cancel triggers once the launching edit has settled."""
        if self._animation_preview_active:
            self._animation_preview_cancel_armed = True

    def _cancel_animation_preview(self, *_args: object) -> None:
        """Stop an armed preview in response to any further user action."""
        if self._animation_preview_active and self._animation_preview_cancel_armed:
            self.animation_preview_controller.cancel()

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        if self._animation_preview_cancel_armed and event.type() in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.KeyPress,
            QEvent.Type.Wheel,
        ):
            self.animation_preview_controller.cancel()
        return super().eventFilter(watched, event)

    def _finish_canvas_animation_preview(self) -> None:
        """Restore interaction after the Canvas preview returns to its source state."""
        if not self._animation_preview_active:
            return
        self._animation_preview_active = False
        self._animation_preview_cancel_armed = False
        self.canvas.viewport().removeEventFilter(self)
        self.canvas.setFocus(Qt.FocusReason.OtherFocusReason)

    @staticmethod
    def _playlist_duration(tracks: list) -> float:
        """Return the full timeline duration, including any user-created gaps."""
        cursor = 0.0
        end = 0.0
        for track in tracks:
            requested = track.start_time_seconds if track.start_time_seconds is not None else cursor
            start = max(cursor, requested)
            cursor = start + track.duration_seconds
            end = max(end, cursor)
        return end

    def _export_upscale_warnings(
        self, active_tracks: list, render_settings: RenderSettings,
        render_scale: float, korean: bool,
    ) -> list[str]:
        """List raster sources FFmpeg would visibly enlarge past their pixels.

        Vector content is now rasterised at the export resolution, so a
        low-resolution album cover or imported image is the remaining soft spot.
        A source is flagged once its on-screen area exceeds 1.5x its native area.
        """
        output_w = max(1, render_settings.output_width)
        output_h = max(1, render_settings.output_height)

        def native_size(path: str) -> tuple[int, int]:
            if not path:
                return (0, 0)
            size = QImageReader(path).size()
            return (max(0, size.width()), max(0, size.height()))

        # Every album-art source shares the track covers; rate the weakest one.
        cover_native = (0, 0)
        for track in active_tracks:
            pixmap = extract_track_cover(track.file_path, track.cover_path)
            if pixmap.isNull():
                continue
            current = (pixmap.width(), pixmap.height())
            cover_native = current if cover_native == (0, 0) else (
                min(cover_native[0], current[0]),
                min(cover_native[1], current[1]),
            )

        warnings: list[str] = []
        for source in self.store.sources():
            if not source.visible:
                continue
            stype = source.source_type
            element_shown = (
                round(source.width * source.scale * render_scale),
                round(source.height * source.scale * render_scale),
            )
            if stype in {SourceType.IMAGE, SourceType.LOGO, SourceType.WATERMARK}:
                native, shown = native_size(source.content_path), element_shown
            elif stype is SourceType.ALBUM_COVER:
                native = (
                    native_size(source.content_path)
                    if source.content_path else cover_native
                )
                shown = element_shown
            elif stype is SourceType.BACKGROUND and source.background_mode == "image":
                native, shown = native_size(source.content_path), (output_w, output_h)
            elif stype is SourceType.BACKGROUND and source.background_mode == "album_art":
                native, shown = cover_native, (output_w, output_h)
            else:
                continue
            if native[0] <= 0 or native[1] <= 0:
                continue
            scale_up = (
                shown[0] * shown[1] / (native[0] * native[1])
            ) ** 0.5
            if scale_up < 1.5:
                continue
            label = source.name or stype.value
            warnings.append(
                f"· {label}: {native[0]}×{native[1]} → "
                + (
                    f"약 {shown[0]}×{shown[1]} ({scale_up:.1f}배 확대)"
                    if korean else
                    f"about {shown[0]}×{shown[1]} ({scale_up:.1f}x upscale)"
                )
            )
        return warnings

    def _export_visualizers(
        self, tracks: list | None = None, render_scale: float = 1.0,
    ) -> list[VisualizerOverlay]:
        """Translate visible, axis-aligned Canvas visualizers into Python-rendered overlays.

        ``render_scale`` (>= 1.0) maps Canvas coordinates onto the export's final
        pixel grid so an overlay stays aligned with the up-rendered base stream.
        """
        overlays: list[VisualizerOverlay] = []
        visualizer_sources = [
            source for source in self.store.sources()
            if source.source_type in {
                SourceType.AUDIO_VISUALIZER, SourceType.AUDIO_WAVEFORM,
                SourceType.AUDIO_LEVEL_METER, SourceType.PARTICLE_OVERLAY,
            } and source.visible
        ]
        active_tracks = list(tracks) if tracks is not None else [
            track for track in self.playlist_service.tracks if track.enabled
        ]
        track_personal_colors = (
            tuple(
                extract_track_personal_color(track.file_path, track.cover_path)
                for track in active_tracks
            )
            if any(source.personal_color_enabled for source in visualizer_sources)
            else ()
        )
        for source in visualizer_sources:
            kind = {
                SourceType.AUDIO_VISUALIZER: "visualizer",
                SourceType.AUDIO_WAVEFORM: "waveform",
                SourceType.AUDIO_LEVEL_METER: "level_meter",
                SourceType.PARTICLE_OVERLAY: "particles",
            }[source.source_type]
            overlay_width = max(8, round(source.width * source.scale))
            overlay_height = max(8, round(source.height * source.scale))
            # QGraphicsItem scales around its centre.  Use the same transformed
            # top-left point for FFmpeg, otherwise scaled visualizers drift down
            # and right compared with the Canvas placement.  ``render_scale``
            # then lifts every measure onto the export's pixel grid (identity at
            # 1.0 since ``round(int * 1.0) == int``).
            overlay_x = round(source.x + (source.width - overlay_width) / 2.0)
            overlay_y = round(source.y + (source.height - overlay_height) / 2.0)
            overlays.append(VisualizerOverlay(
                x=round(overlay_x * render_scale),
                y=round(overlay_y * render_scale),
                width=max(8, round(overlay_width * render_scale)),
                height=max(8, round(overlay_height * render_scale)),
                style=source.visualizer_style,
                color=source.fill_color,
                personal_colors=(
                    tuple(
                        adjust_personal_color(
                            color,
                            source.fill_color,
                            brightness=source.personal_color_brightness,
                            saturation=source.personal_color_saturation,
                            hue_shift=source.personal_color_hue_shift,
                            strength=source.personal_color_strength,
                        )
                        for color in track_personal_colors
                    )
                    if source.personal_color_enabled else ()
                ),
                opacity=source.opacity,
                bar_count=source.visualizer_bars,
                line_width=source.visualizer_line_width * render_scale,
                sensitivity=source.visualizer_sensitivity,
                reactivity=source.visualizer_reactivity,
                noise_gate=source.visualizer_noise_gate,
                min_level=source.visualizer_min_level,
                max_level=source.visualizer_max_level,
                attack=source.visualizer_attack,
                release=source.visualizer_release,
                smoothing=source.visualizer_smoothing,
                curve=source.visualizer_curve,
                kind=kind,
                effect_style=(source.waveform_style if kind == "waveform" else
                              source.level_meter_style if kind == "level_meter" else
                              source.particle_style if kind == "particles" else source.visualizer_style),
                density=source.particle_density,
                speed=source.particle_speed,
                level_meter_mode=(
                    "stereo" if source.level_meter_mode == "led" else source.level_meter_mode
                ),
                level_meter_style=(
                    "led" if source.level_meter_mode == "led" else source.level_meter_style
                ),
                level_meter_orientation=source.level_meter_orientation,
                level_meter_sensitivity=source.level_meter_sensitivity,
                level_meter_attack=source.level_meter_attack,
                level_meter_release=source.level_meter_release,
                level_meter_min_level=source.level_meter_min_level,
                level_meter_max_level=source.level_meter_max_level,
                level_meter_segments=source.level_meter_segments,
                level_meter_gap=source.level_meter_gap * render_scale,
                level_meter_show_peak=source.level_meter_show_peak,
                level_meter_peak_hold=source.level_meter_peak_hold,
                level_meter_peak_decay=source.level_meter_peak_decay,
                level_meter_track_color=source.level_meter_track_color,
                level_meter_low_color=source.level_meter_low_color,
                level_meter_mid_color=source.level_meter_mid_color,
                level_meter_high_color=source.level_meter_high_color,
                particle_min_size=source.particle_min_size * render_scale,
                particle_max_size=source.particle_max_size * render_scale,
                particle_opacity=source.particle_opacity,
                particle_direction=source.particle_direction,
                particle_drift=source.particle_drift,
                particle_twinkle=source.particle_twinkle,
                particle_glow=source.particle_glow,
                particle_secondary_color=source.particle_secondary_color,
                particle_seed=source.particle_seed,
                rotation=source.rotation,
                z_index=source.z_index,
                timeline_start=source.timeline_start,
                timeline_duration=source.timeline_duration,
                animation_in=source.animation_in,
                animation_out=source.animation_out,
                animation_in_duration=source.animation_in_duration,
                animation_out_duration=source.animation_out_duration,
            ))
        # FFmpeg overlays later inputs on top.  Preserve the Canvas stacking
        # order when two reactive sources overlap.
        return sorted(overlays, key=lambda overlay: (overlay.z_index, overlay.y, overlay.x))

    @staticmethod
    def _export_layer_worker_count(
        render_settings: RenderSettings, stream_count: int,
    ) -> int:
        """Bound Canvas-layer encoders according to the selected work mode."""
        if stream_count <= 0:
            return 0
        mode = render_settings.work_mode
        if mode == WORK_MODE_STABLE:
            return 1
        if mode == WORK_MODE_MAX_SPEED:
            pixels = render_settings.output_width * render_settings.output_height
            cap = 2 if pixels >= 3840 * 2160 else 3
            return min(stream_count, cap)
        return min(stream_count, 2)

    @staticmethod
    def _export_png_queue_capacity(render_settings: RenderSettings) -> int:
        """Bound staged 4K QImages according to the selected work mode."""
        if render_settings.work_mode == WORK_MODE_STABLE:
            return 1
        if render_settings.work_mode == WORK_MODE_MAX_SPEED:
            pixels = render_settings.output_width * render_settings.output_height
            return 2 if pixels >= 3840 * 2160 else 5
        return 3

    def _export_video_clips(
        self, tracks: list, playlist_duration: float,
        work_mode: str = WORK_MODE_AUTO, render_scale: float = 1.0,
    ) -> list[VideoClipOverlay]:
        """Expand visible video elements into deterministic FFmpeg clip intervals.

        ``render_scale`` (>= 1.0) lifts clip geometry onto the export's final
        pixel grid to stay aligned with the up-rendered base Canvas stream.
        """
        clips: list[VideoClipOverlay] = []
        duration_cache: dict[str, float] = {}

        track_windows: list[tuple[object, float]] = []
        cursor = 0.0
        for track in tracks:
            requested = track.start_time_seconds if track.start_time_seconds is not None else cursor
            start = max(cursor, requested)
            track_windows.append((track, start))
            cursor = start + track.duration_seconds

        planned_sources: list[tuple[Source, list[tuple[list[str], float, float]]]] = []
        ordered_paths: list[str] = []
        seen_paths: set[str] = set()
        for source in self.store.sources():
            if source.source_type is not SourceType.VIDEO or not source.visible:
                continue
            raw_schedules: list[tuple[list[str], float, float]] = []
            if source.video_timing_mode == "track":
                raw_schedules.extend(
                    (list(track.video_paths), start, track.duration_seconds)
                    for track, start in track_windows if track.video_paths
                )
            else:
                start = min(playlist_duration, max(0.0, source.timeline_start))
                available = (
                    min(source.timeline_duration, playlist_duration - start)
                    if source.timeline_duration > 0.0 else playlist_duration - start
                )
                raw_schedules.append((list(source.video_paths), start, available))
            schedules: list[tuple[list[str], float, float]] = []
            for paths, start, available in raw_schedules:
                visibility_start = max(start, source.timeline_start)
                visibility_end = start + available
                if source.timeline_duration > 0.0:
                    visibility_end = min(
                        visibility_end,
                        source.timeline_start + source.timeline_duration,
                    )
                start = visibility_start
                available = max(0.0, visibility_end - visibility_start)
                if available <= 0.0 or not paths:
                    continue
                schedules.append((paths, start, available))
                for path in paths:
                    if path not in seen_paths:
                        seen_paths.add(path)
                        ordered_paths.append(path)
            if schedules:
                planned_sources.append((source, schedules))

        media_paths: list[Path] = []
        for path in ordered_paths:
            media_path = Path(path)
            if not media_path.is_file():
                raise RenderError(f"Video file is missing: {path}")
            media_paths.append(media_path)
        # FFprobe startup dominates projects with several per-track videos.
        # Probe independent files concurrently, while keeping the small global
        # cap used by the rest of the export pipeline.
        if work_mode == WORK_MODE_STABLE:
            worker_count = min(1, len(media_paths))
        elif work_mode == WORK_MODE_MAX_SPEED:
            worker_count = min(5, len(media_paths))
        else:
            worker_count = min(3, len(media_paths))
        if worker_count:
            with ThreadPoolExecutor(
                max_workers=worker_count, thread_name_prefix="export-video-probe",
            ) as executor:
                probed = list(executor.map(PlaylistService._probe_duration, media_paths))
            for path, probed_duration in zip(ordered_paths, probed, strict=True):
                if probed_duration <= 0.0:
                    raise RenderError(f"Video duration could not be determined: {path}")
                duration_cache[path] = probed_duration

        for source, schedules in planned_sources:
            for paths, start, available in schedules:
                durations = {path: duration_cache[path] for path in paths}
                for occurrence in build_video_occurrences(
                    source, paths, durations, start, available,
                ):
                    overlay_width = max(8, round(source.width * source.scale))
                    overlay_height = max(8, round(source.height * source.scale))
                    # ``round(int * 1.0) == int`` keeps this identical when the
                    # export matches the canvas resolution.
                    clip_x = round(source.x + (source.width - overlay_width) / 2.0)
                    clip_y = round(source.y + (source.height - overlay_height) / 2.0)
                    clips.append(VideoClipOverlay(
                        path=Path(occurrence.path),
                        timeline_start=occurrence.timeline_start,
                        duration_seconds=occurrence.duration_seconds,
                        media_start_seconds=occurrence.media_start_seconds,
                        x=round(clip_x * render_scale),
                        y=round(clip_y * render_scale),
                        width=max(8, round(overlay_width * render_scale)),
                        height=max(8, round(overlay_height * render_scale)),
                        z_index=source.z_index,
                        rotation=source.rotation,
                        opacity=source.opacity,
                        fit_mode=source.image_fit_mode,
                        fill_color=source.fill_color,
                        border_radius=source.border_radius * source.scale * render_scale,
                        brightness=source.brightness,
                        contrast=source.contrast,
                        saturation=source.video_saturation,
                        grayscale=source.video_grayscale,
                        blur=source.blur * render_scale,
                        speed=source.video_speed,
                        loop_input=occurrence.loop_media,
                    ))
        return sorted(clips, key=lambda clip: (clip.z_index, clip.timeline_start))

    def _show_shortcuts(self) -> None:
        """Open the dedicated, grouped keyboard shortcut reference."""
        ShortcutsDialog(self.translator, self).exec()

    def _show_help(self) -> None:
        """Open the searchable offline user guide."""
        HelpDialog(self.translator, self).exec()

    def _show_about(self) -> None:
        """Open program identity, runtime details, and support diagnostics."""
        ffmpeg_path: Path | None = None
        try:
            ffmpeg_path = FFmpegRenderer(
                self.settings_service.current.ffmpeg_path or None
            ).executable
        except FFmpegNotFoundError:
            pass
        AboutDialog(self.translator, ffmpeg_path, log_directory(), self).exec()

    def schedule_automatic_update_check(self) -> None:
        """Check once after startup without delaying project selection or first paint."""
        QTimer.singleShot(1200, lambda: self._check_for_updates(manual=False))

    def _check_for_updates(self, manual: bool) -> None:
        """Request the latest stable GitHub Release on a background thread."""
        if self._update_check_worker and self._update_check_worker.isRunning():
            if manual:
                self.statusBar().showMessage(
                    "이미 업데이트를 확인하고 있습니다."
                    if self.translator.language is Language.KOREAN else
                    "An update check is already in progress.",
                    4000,
                )
            return
        if self._update_download_worker and self._update_download_worker.isRunning():
            return
        self._update_check_manual = manual
        self.check_updates_action.setEnabled(False)
        if manual:
            self.statusBar().showMessage(
                "GitHub에서 최신 버전을 확인하는 중입니다…"
                if self.translator.language is Language.KOREAN else
                "Checking GitHub for the latest version…"
            )
        worker = UpdateCheckWorker(self._update_service)
        self._update_check_worker = worker
        worker.release_found.connect(self._update_release_found)
        worker.failed.connect(self._update_check_failed)
        worker.finished.connect(self._update_check_finished)
        korean = self.translator.language is Language.KOREAN
        self.activity_progress.begin(
            "update_check",
            "업데이트 확인" if korean else "Checking for updates",
            detail="GitHub 릴리즈 확인 중" if korean else "Checking GitHub releases",
        )
        worker.start()

    def _update_release_found(self, release: ReleaseInfo) -> None:
        """Compare versions, honor automatic dismissal, and show release notes."""
        manual = self._update_check_manual
        korean = self.translator.language is Language.KOREAN
        try:
            release_version = normalized_version(release.version)
            current_version = normalized_version(__version__)
        except Exception as error:
            self._update_check_failed(str(error))
            return
        if current_version > release_version:
            QMessageBox.warning(
                self,
                "버전 확인 경고" if korean else "Version check warning",
                (
                    f"현재 프로그램 버전({__version__})이 GitHub 최신 릴리즈"
                    f"({release.version})보다 높습니다.\n"
                    "개발 또는 아직 공개되지 않은 빌드일 수 있으며 업데이트는 실행하지 않습니다."
                )
                if korean else
                (
                    f"The installed version ({__version__}) is newer than the latest "
                    f"GitHub release ({release.version}).\n"
                    "This may be a development or not-yet-published build. No update will run."
                ),
            )
            return
        if current_version == release_version:
            if manual:
                UpdateAvailableDialog(
                    release, __version__, korean, False, self, up_to_date=True,
                ).exec()
            return

        settings = QSettings()
        skipped = str(settings.value("updates/skipped_release_tag", "") or "")
        if not manual and skipped == release.tag_name:
            return
        dialog = UpdateAvailableDialog(release, __version__, korean, not manual, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            if not manual:
                settings.setValue("updates/skipped_release_tag", release.tag_name)
                settings.sync()
            return
        settings.remove("updates/skipped_release_tag")
        settings.sync()
        self._start_update_download(release)

    def _update_check_failed(self, message: str) -> None:
        """Keep automatic network failures quiet but explain manual failures."""
        LOGGER.warning("Update check failed: %s", message)
        if self._update_check_manual:
            korean = self.translator.language is Language.KOREAN
            QMessageBox.warning(
                self,
                "업데이트 확인 실패" if korean else "Update check failed",
                f"최신 릴리즈를 확인하지 못했습니다.\n\n{message}"
                if korean else f"Could not check the latest release.\n\n{message}",
            )

    def _update_check_finished(self) -> None:
        self.activity_progress.finish("update_check")
        self.check_updates_action.setEnabled(True)
        self.statusBar().clearMessage()
        if self._update_check_worker:
            self._update_check_worker.deleteLater()
        self._update_check_worker = None

    def _start_update_download(self, release: ReleaseInfo) -> None:
        """Download the selected GitHub Setup into the per-user update directory."""
        if not release.can_install:
            return
        data_root = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppLocalDataLocation
        )
        target = Path(data_root or Path.cwd() / ".app-data") / "updates"
        korean = self.translator.language is Language.KOREAN
        dialog = UpdateDownloadDialog(release, korean, self)
        worker = UpdateDownloadWorker(self._update_service, release, target)
        self._update_download_dialog = dialog
        self._update_download_worker = worker
        self._downloaded_update_path = None
        worker.progress.connect(dialog.update_progress)
        worker.progress.connect(
            lambda fraction, message: self.activity_progress.update(
                "update_download", fraction, message,
            )
        )
        worker.succeeded.connect(self._update_download_succeeded)
        worker.failed.connect(self._update_download_failed)
        worker.cancelled.connect(self._update_download_cancelled)
        worker.finished.connect(self._update_download_finished)
        dialog.cancel_requested.connect(worker.cancel)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        QApplication.processEvents()
        self.activity_progress.begin(
            "update_download",
            "업데이트 다운로드" if korean else "Downloading update",
            detail=release.version,
        )
        worker.start()

    def _update_download_succeeded(self, path: Path) -> None:
        self._downloaded_update_path = Path(path)
        if self._update_download_dialog:
            self._update_download_dialog.complete(True)
            self._update_download_dialog = None

    def _update_download_failed(self, message: str) -> None:
        LOGGER.error("Update download failed: %s", message)
        if self._update_download_dialog:
            self._update_download_dialog.complete(False)
            self._update_download_dialog = None
        korean = self.translator.language is Language.KOREAN
        QMessageBox.critical(
            self,
            "업데이트 다운로드 실패" if korean else "Update download failed",
            f"Setup을 안전하게 다운로드하지 못했습니다.\n\n{message}"
            if korean else f"The Setup file could not be downloaded safely.\n\n{message}",
        )

    def _update_download_cancelled(self) -> None:
        if self._update_download_dialog:
            self._update_download_dialog.complete(False)
            self._update_download_dialog = None
        self.statusBar().showMessage(
            "업데이트 다운로드를 취소했습니다."
            if self.translator.language is Language.KOREAN else
            "Update download cancelled.",
            5000,
        )

    def _update_download_finished(self) -> None:
        self.activity_progress.finish("update_download")
        path = self._downloaded_update_path
        self._downloaded_update_path = None
        if self._update_download_worker:
            self._update_download_worker.deleteLater()
        self._update_download_worker = None
        if path is not None:
            QTimer.singleShot(0, lambda installer=path: self._launch_update_setup(installer))

    def _launch_update_setup(self, installer: Path) -> None:
        """Resolve unsaved work, start the verified Setup, and close the old version."""
        korean = self.translator.language is Language.KOREAN
        if self._animation_preview_active:
            self.animation_preview_controller.cancel()
        if ((self._render_worker and self._render_worker.isRunning())
                or (self._ffmpeg_install_worker and self._ffmpeg_install_worker.isRunning())):
            QMessageBox.warning(
                self,
                "업데이트 대기" if korean else "Update is waiting",
                "현재 작업을 마친 뒤 도움말 → 업데이트 확인에서 다시 실행해 주세요."
                if korean else
                "Finish the current operation, then use Help → Check for updates again.",
            )
            return
        response = QMessageBox.question(
            self,
            "업데이트 설치" if korean else "Install update",
            "다운로드와 SHA-256 검증을 완료했습니다.\n"
            "프로그램을 종료하고 Setup을 실행할까요?"
            if korean else
            "Download and SHA-256 verification are complete.\n"
            "Close Playlist Canvas and run Setup now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if response != QMessageBox.StandardButton.Yes:
            self.statusBar().showMessage(
                f"Setup 저장 위치: {installer}"
                if korean else f"Setup saved to: {installer}",
                10000,
            )
            return
        if self._project_dirty:
            save_response = QMessageBox.warning(
                self,
                "저장되지 않은 변경 사항" if korean else "Unsaved changes",
                "업데이트 전에 프로젝트를 저장할까요?"
                if korean else "Save the project before updating?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if save_response == QMessageBox.StandardButton.Save:
                self._save_project(wait_for_completion=True)
                if self._project_dirty:
                    return
            elif save_response != QMessageBox.StandardButton.Discard:
                return
        if not installer.is_file():
            QMessageBox.critical(
                self,
                "업데이트 오류" if korean else "Update error",
                "다운로드한 Setup 파일을 찾을 수 없습니다."
                if korean else "The downloaded Setup file could not be found.",
            )
            return
        if not QProcess.startDetached(str(installer), []):
            QMessageBox.critical(
                self,
                "업데이트 실행 실패" if korean else "Could not start update",
                "Setup 파일을 실행하지 못했습니다. 다운로드 폴더에서 직접 실행해 주세요."
                if korean else
                "Setup could not be started. Run it manually from the update folder.",
            )
            return
        self._update_install_authorized = True
        self.close()

    def _show_settings(self, focus_ffmpeg: bool = False) -> None:
        """Show and persist the Phase 4A application settings."""
        dialog = SettingsDialog(
            self.settings_service.current,
            self.translator.language,
            self.theme_service.preference,
            self.translator,
            self,
            active_preview_backend=self._preview_backend_for_session,
        )
        self._settings_dialog = dialog
        dialog.catalog_requested.connect(
            lambda force=False: self._load_ffmpeg_catalog(dialog, force=force)
        )
        dialog.download_requested.connect(
            lambda: self._start_ffmpeg_install(dialog)
        )
        dialog.update_requested.connect(
            lambda: self._start_ffmpeg_update(dialog)
        )
        dialog.reinstall_requested.connect(
            lambda: self._start_ffmpeg_reinstall(dialog)
        )
        dialog.delete_requested.connect(
            lambda: self._delete_managed_ffmpeg(dialog)
        )
        if focus_ffmpeg:
            dialog.open_ffmpeg_page()
        if dialog.exec() != dialog.DialogCode.Accepted:
            self._settings_dialog = None
            return
        selected_settings = dialog.app_settings
        self.settings_service.save(selected_settings)
        self.theme_service.set_preference(dialog.selected_theme)
        self.translator.set_language(dialog.selected_language)
        self._settings_dialog = None
        korean = self.translator.language is Language.KOREAN
        renderer_changed = (
            selected_settings.preview_backend != self._preview_backend_for_session
        )
        self.statusBar().showMessage(
            (
                "설정을 저장했습니다. 미리보기 렌더러는 프로그램 재시작 후 적용됩니다."
                if korean else
                "Settings saved. The preview renderer takes effect after restarting the program."
            )
            if renderer_changed else
            ("설정을 저장했습니다." if korean else "Settings saved."),
            7000 if renderer_changed else 4000,
        )

    def _load_ffmpeg_catalog(
        self, settings_dialog: SettingsDialog, *, force: bool = False,
    ) -> None:
        """Fetch selectable versions and managed state away from the UI thread."""
        if force:
            self._ffmpeg_catalog_cache = None
        if self._ffmpeg_catalog_cache is not None:
            releases, current = self._ffmpeg_catalog_cache
            settings_dialog.set_managed_installation(current)
            settings_dialog.set_ffmpeg_catalog(releases)
            return
        if self._ffmpeg_catalog_worker and self._ffmpeg_catalog_worker.isRunning():
            return
        worker = FFmpegCatalogWorker(ManagedFFmpegInstaller())
        self._ffmpeg_catalog_worker = worker
        worker.succeeded.connect(self._ffmpeg_catalog_succeeded)
        worker.failed.connect(self._ffmpeg_catalog_failed)
        worker.finished.connect(self._release_ffmpeg_catalog_worker)
        worker.start()

    def _ffmpeg_catalog_succeeded(
        self, payload: object,
    ) -> None:
        if not isinstance(payload, tuple) or len(payload) != 2:
            return
        releases, current = payload
        if not isinstance(releases, list) or not all(
            isinstance(release, FFmpegReleaseOption) for release in releases
        ):
            return
        installation = (
            current if isinstance(current, ManagedFFmpegInstallation) else None
        )
        self._ffmpeg_catalog_cache = (releases, installation)
        if self._settings_dialog is not None:
            self._settings_dialog.set_managed_installation(installation)
            self._settings_dialog.set_ffmpeg_catalog(releases)

    def _ffmpeg_catalog_failed(
        self, message: str, current: object,
    ) -> None:
        if self._settings_dialog is not None:
            self._settings_dialog.set_managed_installation(
                current if isinstance(current, ManagedFFmpegInstallation) else None
            )
            self._settings_dialog.set_ffmpeg_catalog_error(message)

    def _release_ffmpeg_catalog_worker(self) -> None:
        if self._ffmpeg_catalog_worker:
            self._ffmpeg_catalog_worker.deleteLater()
        self._ffmpeg_catalog_worker = None
        if self._close_after_ffmpeg_catalog_cancel:
            self._close_after_ffmpeg_catalog_cancel = False
            QTimer.singleShot(0, self.close)

    def _start_ffmpeg_install(
        self, settings_dialog: SettingsDialog,
        release: FFmpegReleaseOption | None = None, *, force: bool = False,
    ) -> None:
        """Install the selected checksum-verified build after required consent."""
        if self._ffmpeg_install_worker and self._ffmpeg_install_worker.isRunning():
            return
        release = release or settings_dialog.selected_ffmpeg_release
        korean = self.translator.language is Language.KOREAN
        if release is None:
            QMessageBox.warning(
                settings_dialog,
                "FFmpeg 버전 확인" if korean else "Select an FFmpeg version",
                "설치할 버전을 먼저 선택해 주세요."
                if korean else "Select a version to install first.",
            )
            return
        if not release.recommended and not self._confirm_ffmpeg_release(
            settings_dialog, release,
            "재설치" if korean and force else "설치" if korean else
            "Reinstall" if force else "Install",
        ):
            return
        settings_dialog.set_ffmpeg_installing(True)
        progress = FFmpegInstallProgressDialog(korean, settings_dialog)
        progress.set_busy(
            "Preparing download",
            f"GitHub에서 FFmpeg {release.series} ({release.build}) 설치를 준비하는 중입니다."
            if korean else
            f"Preparing FFmpeg {release.series} ({release.build}) from GitHub.",
        )
        worker = FFmpegInstallWorker(
            ManagedFFmpegInstaller(), release, force=force,
        )
        self._ffmpeg_install_dialog = progress
        self._ffmpeg_install_worker = worker
        worker.progress.connect(progress.update_progress)
        worker.progress.connect(
            lambda stage, fraction, message: self.activity_progress.update(
                "ffmpeg_install", fraction, f"{stage} · {message}",
            )
        )
        worker.succeeded.connect(self._ffmpeg_install_succeeded)
        worker.failed.connect(self._ffmpeg_install_failed)
        worker.cancelled.connect(self._ffmpeg_install_cancelled)
        worker.finished.connect(self._ffmpeg_install_finished)
        progress.cancel_requested.connect(worker.cancel)
        # Show the application-modal child before starting the worker so every
        # other window is disabled and the first network request is visible.
        # QDialog.open() forces WindowModal for parented dialogs. show() keeps
        # the explicit ApplicationModal setting from the installer dialog.
        progress.show()
        progress.raise_()
        progress.activateWindow()
        QApplication.processEvents()
        self.activity_progress.begin(
            "ffmpeg_install",
            (f"FFmpeg {release.series} 재설치" if force else
             f"FFmpeg {release.series} 다운로드 및 설치") if korean else
            (f"Reinstalling FFmpeg {release.series}" if force else
             f"Downloading and installing FFmpeg {release.series}"),
            detail=(f"선택한 빌드: {release.build}" if korean else
                    f"Selected build: {release.build}"),
        )
        worker.start()

    def _confirm_ffmpeg_release(
        self, parent: QWidget, release: FFmpegReleaseOption, operation: str,
    ) -> bool:
        """Warn for non-recommended or destructive version operations with notes."""
        korean = self.translator.language is Language.KOREAN
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(
            "비권장 FFmpeg 버전" if korean else "Non-recommended FFmpeg version"
        )
        box.setText(
            f"FFmpeg {release.series}은(는) 이 Playlist Canvas 패치의 권장 버전이 아닙니다."
            if korean else
            f"FFmpeg {release.series} is not recommended for this Playlist Canvas patch."
        )
        excerpt = release.notes[:1000]
        box.setInformativeText(
            f"호환성이나 내보내기 결과가 달라질 수 있습니다.\n\n패치/배포 내용:\n{excerpt}\n\n{operation}을 계속할까요?"
            if korean else
            f"Compatibility or export output may differ.\n\nPatch/release notes:\n{excerpt}\n\nContinue with {operation.lower()}?"
        )
        box.setDetailedText(release.notes)
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _start_ffmpeg_update(self, settings_dialog: SettingsDialog) -> None:
        release = settings_dialog.recommended_ffmpeg_release
        if release is not None:
            # Updating always targets the app-tested version, so it intentionally
            # skips the non-recommended warning.
            self._start_ffmpeg_install(settings_dialog, release)

    def _start_ffmpeg_reinstall(self, settings_dialog: SettingsDialog) -> None:
        current = settings_dialog.managed_installation
        release = next((
            entry for entry in settings_dialog.ffmpeg_releases
            if current is not None and entry.series == current.series
        ), None)
        if current is None or release is None:
            return
        korean = self.translator.language is Language.KOREAN
        response = QMessageBox.question(
            settings_dialog,
            "FFmpeg 재설치" if korean else "Reinstall FFmpeg",
            (f"현재 관리 중인 FFmpeg {current.series or current.version}을(를) "
             "다시 다운로드하고 검증한 뒤 교체할까요?")
            if korean else
            (f"Download, verify, and replace the managed FFmpeg "
             f"{current.series or current.version}?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if response == QMessageBox.StandardButton.Yes:
            self._start_ffmpeg_install(settings_dialog, release, force=True)

    def _delete_managed_ffmpeg(self, settings_dialog: SettingsDialog) -> None:
        current = settings_dialog.managed_installation
        if current is None:
            return
        korean = self.translator.language is Language.KOREAN
        response = QMessageBox.warning(
            settings_dialog,
            "FFmpeg 삭제" if korean else "Delete FFmpeg",
            "앱에서 관리하는 현재 FFmpeg를 삭제합니다. 삭제 후에는 다시 설치하기 전까지 영상을 내보낼 수 없습니다. 계속할까요?"
            if korean else
            "Delete the currently app-managed FFmpeg. Video export will be unavailable until it is installed again. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if response != QMessageBox.StandardButton.Yes:
            return
        try:
            removed = ManagedFFmpegInstaller().uninstall_current()
        except FFmpegInstallError as error:
            QMessageBox.critical(
                settings_dialog,
                "FFmpeg 삭제 오류" if korean else "Could not delete FFmpeg",
                str(error),
            )
            return
        if removed is None:
            return
        configured_text = self.settings_service.current.ffmpeg_path.strip()
        removed_was_active = False
        if configured_text:
            try:
                removed_was_active = (
                    Path(configured_text).resolve(strict=False)
                    == removed.executable.resolve(strict=False)
                )
            except OSError:
                removed_was_active = Path(configured_text) == removed.executable
        if removed_was_active:
            settings_dialog.ffmpeg_edit.clear()
            self.settings_service.save(replace(
                self.settings_service.current, ffmpeg_path=""
            ))
        settings_dialog.set_managed_installation(None)
        self._ffmpeg_catalog_cache = None
        QMessageBox.information(
            settings_dialog,
            "FFmpeg 삭제 완료" if korean else "FFmpeg deleted",
            "관리 설치본을 삭제했습니다. 필요하면 권장 버전을 다시 설치할 수 있습니다."
            if korean else
            "The managed installation was deleted. You can reinstall the recommended version when needed.",
        )

    def _ffmpeg_install_succeeded(self, installation: ManagedFFmpegInstallation) -> None:
        """Persist the verified managed executable as the active FFmpeg path."""
        if self._ffmpeg_install_dialog:
            self._ffmpeg_install_dialog.complete(True)
            self._ffmpeg_install_dialog = None
        self.settings_service.save(replace(
            self.settings_service.current, ffmpeg_path=str(installation.executable)
        ))
        if self._settings_dialog:
            self._settings_dialog.ffmpeg_edit.setText(str(installation.executable))
            self._settings_dialog.set_managed_installation(installation)
            self._settings_dialog.set_ffmpeg_installing(False)
            releases = list(self._settings_dialog.ffmpeg_releases)
            self._ffmpeg_catalog_cache = (
                (releases, installation) if releases else None
            )
        korean = self.translator.language is Language.KOREAN
        message = (
            f"FFmpeg {installation.version} 설치 및 검증 완료"
            if korean else f"FFmpeg {installation.version} installed and verified"
        )
        self.statusBar().showMessage(message, 7000)
        QMessageBox.information(
            self._settings_dialog or self,
            "FFmpeg 설치 완료" if korean else "FFmpeg installed", message
        )

    def _ffmpeg_install_failed(self, message: str) -> None:
        """Report a failed installation while retaining every prior installation intact."""
        LOGGER.error("Managed FFmpeg installation failed: %s", message)
        if self._ffmpeg_install_dialog:
            self._ffmpeg_install_dialog.complete(False)
            self._ffmpeg_install_dialog = None
        if self._settings_dialog:
            self._settings_dialog.set_ffmpeg_install_error(message)
        korean = self.translator.language is Language.KOREAN
        QMessageBox.warning(
            self._settings_dialog or self,
            "FFmpeg 설치 오류" if korean else "FFmpeg installation error", message
        )

    def _ffmpeg_install_cancelled(self) -> None:
        """Close the install UI after the worker has removed temporary download data."""
        if self._ffmpeg_install_dialog:
            self._ffmpeg_install_dialog.complete(False)
            self._ffmpeg_install_dialog = None
        if self._settings_dialog:
            self._settings_dialog.set_ffmpeg_installing(False)
        self.statusBar().showMessage(
            "FFmpeg 설치를 취소했습니다."
            if self.translator.language is Language.KOREAN else "FFmpeg installation cancelled.",
            5000,
        )

    def _ffmpeg_install_finished(self) -> None:
        """Release completed installer resources after queued outcome signals are handled."""
        self.activity_progress.finish("ffmpeg_install")
        QTimer.singleShot(0, self._release_ffmpeg_install_worker)

    def _release_ffmpeg_install_worker(self) -> None:
        if self._ffmpeg_install_worker:
            self._ffmpeg_install_worker.deleteLater()
        self._ffmpeg_install_worker = None

    def _export_playlist_files(self) -> None:
        """Create YouTube description and CSV files without requiring FFmpeg."""
        default_directory = (
            self.current_project_path.parent
            if self.current_project_path is not None
            else Path.cwd()
        )
        dialog = PlaylistExportDialog(default_directory, self.translator, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        korean = self.translator.language is Language.KOREAN
        existing = [
            path.name for path in (
                dialog.output_directory / "description.txt",
                dialog.output_directory / "playlist.csv",
            ) if path.exists()
        ]
        overwrite = False
        if existing:
            answer = QMessageBox.question(
                self,
                "파일 덮어쓰기" if korean else "Overwrite files",
                (f"이미 존재하는 파일을 덮어쓸까요?\n{', '.join(existing)}")
                if korean else
                f"Overwrite the existing files?\n{', '.join(existing)}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            overwrite = True
        try:
            result = self.playlist_export_service.export(
                self.playlist_service.tracks,
                dialog.output_directory,
                dialog.timestamp_format,
                overwrite=overwrite,
            )
        except PlaylistExportError as error:
            QMessageBox.warning(
                self,
                "파일 만들기 오류" if korean else "File creation error",
                str(error),
            )
            return
        if dialog.copy_description:
            QApplication.clipboard().setText(result.description_text)
        message = (
            f"description.txt와 playlist.csv를 만들었습니다.\n{result.description_path.parent}"
            if korean else
            f"Created description.txt and playlist.csv.\n{result.description_path.parent}"
        )
        self.statusBar().showMessage(message.replace("\n", " "), 7000)
        QMessageBox.information(
            self,
            "플레이리스트 파일 완료" if korean else "Playlist files complete",
            message,
        )

    def _export_succeeded(self, result: RenderResult) -> None:
        """Present a completed background export."""
        if self._export_dialog:
            self._export_dialog.complete(True)
            self._export_dialog = None
        korean = self.translator.language is Language.KOREAN
        message = f"영상 생성 완료: {result.output_path}" if korean else f"Video created: {result.output_path}"
        self.statusBar().showMessage(message, 7000)
        self._active_export_output_path = result.output_path
        self._pending_export_result = result
        self._notify_export_stage("Complete")

    def _export_failed(self, message: str) -> None:
        """Show FFmpeg failure details reported by the worker thread."""
        LOGGER.error("Video export failed: %s", message)
        if self._export_dialog:
            self._export_dialog.complete(False)
            self._export_dialog = None
        korean = self.translator.language is Language.KOREAN
        self._pending_export_result = None
        self._notify_export_problem(message)
        QMessageBox.critical(self, "내보내기 오류" if korean else "Export error", message)
        self.statusBar().showMessage(message, 7000)

    def _export_cancelled(self) -> None:
        """Close the progress window after safe cancellation and temp cleanup."""
        if self._export_dialog:
            self._export_dialog.complete(False)
            self._export_dialog = None
        message = "내보내기를 취소했습니다." if self.translator.language is Language.KOREAN else "Export cancelled."
        self._pending_export_result = None
        self._notify_export_problem(message, cancelled=True)
        self.statusBar().showMessage(message, 5000)

    def _export_finished(self) -> None:
        """Release the UI export lock after any worker completion path."""
        completed_result = self._pending_export_result
        self._pending_export_result = None
        self.activity_progress.finish("export")
        self._unlock_main_form_after_export()
        self._clear_export_frame_staging()
        QTimer.singleShot(0, self._release_render_worker)
        self._resume_close_after_export_cancel()
        if completed_result is not None:
            QTimer.singleShot(
                0,
                lambda result=completed_result: self._show_export_complete_dialog(
                    result,
                ),
            )
        else:
            self._active_export_output_path = None

    def _show_export_complete_dialog(self, result: RenderResult) -> None:
        """Offer useful next actions only after the editor has been unlocked."""
        dialog = ExportCompleteDialog(result.output_path, self.translator, self)
        dialog.exec()
        export_again = dialog.export_again_requested
        self._active_export_output_path = None
        if export_again:
            QTimer.singleShot(0, self._export_video)

    def _resume_close_after_export_cancel(self) -> None:
        """Continue a window-close request only after export resources stop."""
        if not self._close_after_export_cancel:
            return
        self._close_after_export_cancel = False
        QTimer.singleShot(0, self.close)

    def _release_render_worker(self) -> None:
        """Delete the completed worker after its queued result signal is delivered."""
        if self._render_worker:
            self._render_worker.deleteLater()
        self._render_worker = None

    def _choose_preset(self) -> None:
        """Select and apply a complete visual canvas preset to this project."""
        dialog = DesignPresetDialog(self.translator, self)
        if not dialog.exec() or dialog.selected_preset is None:
            return
        preset = dialog.selected_preset
        korean = self.translator.language is Language.KOREAN
        answer = QMessageBox.warning(
            self,
            "프리셋 적용 경고" if korean else "Apply preset warning",
            (
                f"'{preset.name('ko')}' 프리셋을 적용하면 현재 캔버스의 모든 요소가 "
                "프리셋 요소로 교체됩니다.\n\n재생목록은 유지되며, 적용 후 Ctrl+Z로 되돌릴 수 있습니다.\n"
                "계속 적용할까요?"
                if korean else
                f"Applying '{preset.name('en')}' will replace every current Canvas "
                "source with the preset sources.\n\nThe playlist will be preserved, and "
                "you can undo afterward with Ctrl+Z.\nApply this preset?"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._apply_preset(preset)

    def _show_ai_project_builder(self) -> None:
        """Open the standalone configurable AI Project Builder."""
        AIProjectBuilderDialog(self.translator, self).exec()

    def _apply_preset(self, preset: PresetDefinition) -> None:
        """Replace canvas sources with a preset while preserving the playlist."""
        artboard = self.canvas.scene_model.artboard_rect
        self.store.replace(self._preset_sources_for_canvas(
            preset, artboard.width(), artboard.height(),
        ))
        message = "프리셋을 적용했습니다. Ctrl+Z로 되돌릴 수 있습니다."
        if self.translator.language is not Language.KOREAN:
            message = "Preset applied. Press Ctrl+Z to undo."
        self.statusBar().showMessage(message, 4000)

    def _save_current_as_preset(self) -> None:
        """Save every current canvas source as a reusable user preset."""
        korean = self.translator.language is Language.KOREAN
        sources = self.store.sources()
        if not sources:
            QMessageBox.information(
                self,
                "프리셋으로 저장" if korean else "Save as preset",
                "캔버스에 저장할 요소가 없습니다."
                if korean else "The canvas has no sources to save.",
            )
            return
        name, accepted = QInputDialog.getText(
            self,
            "프리셋으로 저장" if korean else "Save as preset",
            "프리셋 이름" if korean else "Preset name",
        )
        if not accepted or not name.strip():
            return
        artboard = self.canvas.scene_model.artboard_rect
        try:
            preset = UserPresetService.save(
                name, sources, artboard.width(), artboard.height(),
            )
        except (UserPresetError, OSError) as error:
            QMessageBox.warning(
                self,
                "저장 실패" if korean else "Save failed",
                str(error),
            )
            return
        self.statusBar().showMessage(
            f"'{preset.name('ko')}' 프리셋을 저장했습니다. 디자인 프리셋 창에서 다시 사용할 수 있습니다."
            if korean else
            f"Saved preset '{preset.name('en')}'. Reuse it from the Design Presets dialog.",
            5000,
        )

    @staticmethod
    def _preset_sources_for_canvas(
        preset: PresetDefinition, canvas_width: float, canvas_height: float,
    ) -> list[Source]:
        """Adapt a preset's authored layout to the project without changing its ratio."""
        return MainWindow._adapt_sources_to_canvas(
            preset.builder(), preset.source_width, preset.source_height,
            canvas_width, canvas_height,
        )

    @staticmethod
    def _adapt_sources_to_canvas(
        sources: list[Source], source_width: float, source_height: float,
        target_width: float, target_height: float,
    ) -> list[Source]:
        """Scale source sizes uniformly and distribute their centres by canvas ratio."""
        if min(source_width, source_height, target_width, target_height) <= 0:
            return [Source.from_dict(source.to_dict()) for source in sources]
        adapted = [Source.from_dict(source.to_dict()) for source in sources]
        x_ratio = target_width / source_width
        y_ratio = target_height / source_height
        factor = min(x_ratio, y_ratio)
        scalable_fields = (
            "border_radius", "outline_width", "font_size",
            "visualizer_line_width", "track_list_row_spacing",
            "track_list_item_padding", "subtitle_line_spacing",
            "subtitle_previous_blur", "level_meter_gap",
            "particle_min_size", "particle_max_size",
        )
        for source in adapted:
            if source.source_type is SourceType.BACKGROUND:
                source.x = 0.0
                source.y = 0.0
                source.width = target_width
                source.height = target_height
            else:
                center_x = (source.x + source.width / 2.0) * x_ratio
                center_y = (source.y + source.height / 2.0) * y_ratio
                source.width *= factor
                source.height *= factor
                source.x = max(
                    0.0, min(target_width - source.width, center_x - source.width / 2.0)
                )
                source.y = max(
                    0.0, min(target_height - source.height, center_y - source.height / 2.0)
                )
            for field in scalable_fields:
                setattr(source, field, getattr(source, field) * factor)
            source.shadow.blur_radius *= factor
            source.shadow.offset_x *= factor
            source.shadow.offset_y *= factor
        return adapted

    def _new_project(self, *, confirm_unsaved: bool = True) -> bool:
        if confirm_unsaved and not self._confirm_unsaved_changes():
            return False
        dialog = NewProjectDialog(self.translator, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        canvas_width, canvas_height = dialog.canvas_size
        selected_preset = dialog.selected_design_preset
        previous_project_path = self.current_project_path
        self._history_restoring = True
        try:
            self.current_project_path = None
            self._legacy_project_path = None
            if hasattr(self, "upgrade_project_action"):
                self.upgrade_project_action.setEnabled(False)
            self.project_settings = ProjectSettings()
            self._project_theme_metadata = self.theme_service.preference.value
            self._project_language_metadata = self.translator.language.value
            self.project_content_service.replace([])
            self.store.replace([])
            self.playlist_service.replace([])
            self.canvas.scene_model.set_artboard_size(canvas_width, canvas_height)
            self.grid_action.setChecked(True)
            self.snap_action.setChecked(True)
            self.canvas.scene_model.snap_enabled = True
            if selected_preset is None:
                self._add_welcome_sources()
            else:
                self.store.replace(self._preset_sources_for_canvas(
                    selected_preset, canvas_width, canvas_height,
                ))
            self.canvas.fit_artboard()
        finally:
            self._history_restoring = False
        try:
            self.autosave.clear(previous_project_path)
            self.autosave.clear(None)
        except ProjectError as error:
            self.statusBar().showMessage(str(error), 5000)
        if self._history_ready:
            self.history.reset(self._project_document().to_dict())
            self._project_dirty = True
            self._autosave_debounce_timer.start()
        else:
            self._project_dirty = False
        self._update_project_status()
        return True

    def _show_project_start_dialog(self) -> bool:
        """Reuse the launch project chooser for File > New and the toolbar action."""
        if not self._confirm_unsaved_changes():
            return False
        while True:
            dialog = StartupDialog(self.translator, self.recent_projects, self)
            if dialog.exec() != dialog.DialogCode.Accepted:
                return False
            if dialog.action == StartupDialog.NEW_PROJECT:
                if self._new_project(confirm_unsaved=False):
                    return True
                continue
            if dialog.project_path is not None and self._load_project_path(dialog.project_path):
                return True

    def _project_document(self) -> ProjectDocument:
        """Collect current UI and domain state into a portable document."""
        artboard = self.canvas.scene_model.artboard_rect
        canvas = CanvasSettings(
            width=artboard.width(), height=artboard.height(),
            show_grid=self.canvas.scene_model.show_grid,
            snap_enabled=self.canvas.scene_model.snap_enabled,
            zoom=self.canvas.transform().m11(),
        )
        return ProjectDocument(
            sources=self.store.sources(), playlist=self.playlist_service.tracks,
            groups=self.store.groups(),
            canvas=canvas, theme=self._project_theme_metadata,
            language=self._project_language_metadata,
            settings=self.project_settings,
            content_library=self.project_content_service.items,
        )

    def _connect_history(self) -> None:
        """Observe all editable Phase 1/2 state and coalesce snapshot commits."""
        self.store.source_added.connect(lambda _source: self._schedule_history())
        self.store.source_added.connect(lambda _source: self._synchronize_content_library())
        self.store.source_removed.connect(lambda _source_id: self._schedule_history())
        self.store.source_changed.connect(lambda _source: self._schedule_history())
        self.store.source_changed.connect(lambda _source: self._synchronize_content_library())
        self.store.sources_replaced.connect(self._schedule_history)
        self.store.groups_changed.connect(self._schedule_history)
        self.playlist_service.playlist_changed.connect(self._schedule_history)
        self.playlist_service.playlist_changed.connect(self._synchronize_content_library)
        self.project_content_service.changed.connect(self._schedule_history)
        # Canvas zoom is view state, not document state: it must not mark the
        # project unsaved or land on the undo stack. Its value is still captured
        # in _project_document() whenever a real edit or an explicit save runs.
        self.history.changed.connect(self._update_history_actions)
        self.history.changed.connect(
            lambda _can_undo, _can_redo: self.canvas.prune_retired_items(
                self.history.retained_source_ids()
            )
        )

    def _finish_initialization(self) -> None:
        """Set the initial canvas view and establish the first undo snapshot."""
        self.canvas.fit_artboard()
        self.history.reset(self._project_document().to_dict())
        self._history_ready = True
        self._project_dirty = False
        self._update_project_status()
        self.motion.fade_in(self.centralWidget())

    def _synchronize_content_library(self) -> None:
        """Register newly referenced Inspector and playlist media in the library."""
        self.project_content_service.synchronize(self._project_document())

    def show_startup_dialog(self) -> bool:
        """Block the editor until the user chooses how to start the session."""
        # Recovery belongs before the project choice.  Requiring the user to click
        # "New project" first meant a newer snapshot could be silently skipped when
        # they opened the older saved project from Recents.
        if self._offer_recovery():
            return True
        while self.isVisible():
            dialog = StartupDialog(self.translator, self.recent_projects, self)
            if dialog.exec() != dialog.DialogCode.Accepted:
                return False
            if dialog.action == StartupDialog.NEW_PROJECT:
                if self._new_project(confirm_unsaved=False):
                    return True
                continue
            if dialog.project_path is not None and self._load_project_path(dialog.project_path):
                return True
        return False

    def _on_theme_changed(self, preference: str, effective: str) -> None:
        """Apply a selected theme and softly transition the refreshed workspace."""
        self.current_theme = preference
        selected = Theme(preference)
        if selected in self.theme_actions:
            self.theme_actions[selected].setChecked(True)
        self._apply_style()
        if self._inline_preview is not None:
            self._inline_preview.refresh_theme()
            if (
                self._inline_preview_track_panel is not None
                and self._inline_preview_controls is not None
            ):
                self._inline_preview_track_panel.setStyleSheet(
                    self._inline_preview_controls.styleSheet()
                )
        if self.centralWidget() is not None:
            self.motion.fade_in(self.centralWidget(), 140)

    def _set_sidebar_visible(
        self, visible: bool, *, persist: bool = True, sync_action: bool = True,
        restore_sizes: list[int] | None = None,
    ) -> None:
        """Collapse or reveal the editing sidebar with a short width animation."""
        if persist:
            QSettings().setValue("workspace/left_panel_visible", bool(visible))
        self._panel_transition_serial["left"] += 1
        serial = self._panel_transition_serial["left"]
        self._sidebar_transition = True
        if sync_action:
            self.panels_action.setEnabled(False)
        if visible:
            target_width = max(180, self._sidebar_open_width)
            start_width = max(0, self.left_workspace.width())
            if self.left_workspace.isHidden():
                start_width = 0
            self.left_workspace.setVisible(True)
            self.left_workspace.setMinimumWidth(0)
            self.left_workspace.setMaximumWidth(start_width)

            def finish_expand() -> None:
                if self._panel_transition_serial["left"] != serial:
                    return
                self.left_workspace.setMaximumWidth(16_777_215)
                if restore_sizes is not None and len(restore_sizes) == 3:
                    # During Preview the right-hand page can temporarily change
                    # splitter constraints. Pin the remembered left edge for one
                    # layout pass so Qt does not proportionally shrink it while
                    # the normal inspector page is being restored.
                    restored_left = max(180, int(restore_sizes[0]))
                    self.left_workspace.setMinimumWidth(restored_left)
                    self.main_splitter.setSizes(restore_sizes)
                    self.left_workspace.setMinimumWidth(180)
                    self._sidebar_open_width = restored_left
                else:
                    self.left_workspace.setMinimumWidth(180)
                self._sidebar_transition = False
                self.main_splitter.lock_edge_sizes()
                if sync_action:
                    self.panels_action.setEnabled(True)
                self.motion.fade_in(self.left_workspace, 120)

            self.motion.animate_width(
                self.left_workspace, start_width, target_width,
                on_finished=finish_expand,
            )
            return
        start_width = max(0, self.left_workspace.width())
        if persist or sync_action:
            self._sidebar_open_width = max(180, start_width)
        self.left_workspace.setMinimumWidth(0)
        self.left_workspace.setMaximumWidth(start_width)

        def finish_collapse() -> None:
            if self._panel_transition_serial["left"] != serial:
                return
            self.left_workspace.setVisible(False)
            self.left_workspace.setMaximumWidth(16_777_215)
            self._sidebar_transition = False
            self.main_splitter.lock_edge_sizes()
            if sync_action:
                self.panels_action.setEnabled(True)

        self.motion.animate_width(
            self.left_workspace, start_width, 0,
            on_finished=finish_collapse,
        )

    def _set_inspector_panel_visible(
        self, visible: bool, *, persist: bool = True, sync_action: bool = True,
    ) -> None:
        """Show or hide the right properties workspace independently."""
        if persist:
            QSettings().setValue("workspace/right_panel_visible", bool(visible))
        self._panel_transition_serial["right"] += 1
        serial = self._panel_transition_serial["right"]
        if sync_action:
            self.inspector_panel_action.setEnabled(False)
        sizes = self.main_splitter.sizes()
        if not visible:
            if len(sizes) == 3:
                self._inspector_open_width = max(
                    220, sizes[2], self.inspector_stack.width(),
                )
            start_width = max(220, self.inspector_stack.width())
            self.inspector_stack.setMinimumWidth(0)
            self.inspector_stack.setMaximumWidth(start_width)

            def finish_collapse() -> None:
                if self._panel_transition_serial["right"] != serial:
                    return
                self.inspector_stack.setVisible(False)
                self.inspector_stack.setMaximumWidth(16_777_215)
                self.main_splitter.lock_edge_sizes()
                if sync_action:
                    self.inspector_panel_action.setEnabled(True)

            self.motion.animate_width(
                self.inspector_stack, start_width, 0,
                on_finished=finish_collapse,
            )
            return
        start_width = max(0, self.inspector_stack.width())
        if self.inspector_stack.isHidden():
            start_width = 0
        self.inspector_stack.setVisible(True)
        self.inspector_stack.setMinimumWidth(0)
        self.inspector_stack.setMaximumWidth(start_width)
        current = self.main_splitter.sizes()
        total = max(900, sum(current))
        left = current[0] if len(current) == 3 else 0
        maximum = max(220, total - left - 300)
        target = min(maximum, max(220, self._inspector_open_width))

        def finish_expand() -> None:
            if self._panel_transition_serial["right"] != serial:
                return
            self.inspector_stack.setMaximumWidth(16_777_215)
            self.main_splitter.lock_edge_sizes()
            if sync_action:
                self.inspector_panel_action.setEnabled(True)
            self.motion.fade_in(self.inspector_stack, 120)

        self.motion.animate_width(
            self.inspector_stack, start_width, target,
            on_finished=finish_expand,
        )

    def _set_bottom_panel_visible(
        self, visible: bool, *, persist: bool = True, sync_action: bool = True,
    ) -> None:
        """Show or hide the shared Playlist, Timeline, and Preview workspace."""
        if persist:
            QSettings().setValue("workspace/bottom_panel_visible", bool(visible))
        self._panel_transition_serial["bottom"] += 1
        serial = self._panel_transition_serial["bottom"]
        if sync_action:
            self.bottom_panel_action.setEnabled(False)
        sizes = self.workspace_splitter.sizes()
        if not visible:
            if len(sizes) == 2:
                self._bottom_open_height = max(
                    180, sizes[1], self.bottom_workspace_stack.height(),
                )
            start_height = max(180, self.bottom_workspace_stack.height())
            self.bottom_workspace_stack.setMinimumHeight(0)
            self.bottom_workspace_stack.setMaximumHeight(start_height)

            def finish_collapse() -> None:
                if self._panel_transition_serial["bottom"] != serial:
                    return
                self.bottom_workspace_stack.setVisible(False)
                self.bottom_workspace_stack.setMaximumHeight(16_777_215)
                self.workspace_splitter.lock_edge_sizes()
                if sync_action:
                    self.bottom_panel_action.setEnabled(True)

            self.motion.animate_height(
                self.bottom_workspace_stack, start_height, 0,
                on_finished=finish_collapse,
            )
            return
        start_height = max(0, self.bottom_workspace_stack.height())
        if self.bottom_workspace_stack.isHidden():
            start_height = 0
        self.bottom_workspace_stack.setVisible(True)
        self.bottom_workspace_stack.setMinimumHeight(0)
        self.bottom_workspace_stack.setMaximumHeight(start_height)
        current = self.workspace_splitter.sizes()
        total = max(500, sum(current))
        target = min(
            max(180, self._bottom_open_height), max(180, total - 300),
        )

        def finish_expand() -> None:
            if self._panel_transition_serial["bottom"] != serial:
                return
            self.bottom_workspace_stack.setMaximumHeight(16_777_215)
            self.workspace_splitter.lock_edge_sizes()
            if sync_action:
                self.bottom_panel_action.setEnabled(True)
            self.motion.fade_in(self.bottom_workspace_stack, 120)

        self.motion.animate_height(
            self.bottom_workspace_stack, start_height, target,
            on_finished=finish_expand,
        )

    def _schedule_history(self) -> None:
        """Coalesce rapid property edits such as dragging into a single undo entry."""
        if self._history_ready and not self._history_restoring:
            self._project_change_serial += 1
            self._project_dirty = True
            self._update_project_status()
            self._history_timer.start()
            self._autosave_debounce_timer.start()

    def _update_project_status(self) -> None:
        """Keep a compact, non-modal project/save-state indicator in the toolbar."""
        if not hasattr(self, "project_status_label"):
            return
        korean = self.translator.language is Language.KOREAN
        name = self.project_settings.title or (
            "새 프로젝트" if korean else "New project"
        )
        saving = (
            self._project_save_worker is not None
            and self._project_save_worker.isRunning()
        )
        state = (
            "저장 중" if korean and saving else
            "Saving" if saving else
            "저장됨" if korean and not self._project_dirty else
            "저장 필요" if korean else
            "Saved" if not self._project_dirty else "Unsaved"
        )
        legacy = " · 레거시 JSON" if korean and self._legacy_project_path else (
            " · Legacy JSON" if self._legacy_project_path else ""
        )
        self.project_status_label.setText(f"{name}  ·  {state}{legacy}")
        self.project_status_label.setToolTip(
            "프로젝트 스냅샷을 백그라운드에서 저장하고 있습니다."
            if korean and saving else
            "The project snapshot is being saved in the background."
            if saving else
            "이 프로젝트는 레거시 JSON입니다. 프로젝트 메뉴에서 .pvsproj로 업그레이드할 수 있습니다."
            if korean and self._legacy_project_path else
            "This is a legacy JSON project. Upgrade it to .pvsproj from the Project menu."
            if self._legacy_project_path else
            "프로젝트를 저장하려면 Ctrl+S를 누르세요." if korean and self._project_dirty else
            "프로젝트가 저장되어 있습니다." if korean else
            "Press Ctrl+S to save this project." if self._project_dirty else
            "This project is saved."
        )

    def _commit_history(self) -> None:
        if self._history_ready and not self._history_restoring:
            self.history.commit(self._project_document().to_dict())

    def _update_history_actions(self, can_undo: bool, can_redo: bool) -> None:
        self.undo_action.setEnabled(can_undo)
        self.redo_action.setEnabled(can_redo)

    def _undo(self) -> None:
        if self._history_applying:
            return
        self._flush_pending_history()
        selected_source_ids = self.store.selected_ids
        active_source_id = self.store.selected.id if self.store.selected else None
        snapshot = self.history.undo()
        if snapshot is not None:
            self._restore_history_snapshot(snapshot, selected_source_ids, active_source_id)

    def _redo(self) -> None:
        if self._history_applying:
            return
        self._flush_pending_history()
        selected_source_ids = self.store.selected_ids
        active_source_id = self.store.selected.id if self.store.selected else None
        snapshot = self.history.redo()
        if snapshot is not None:
            self._restore_history_snapshot(snapshot, selected_source_ids, active_source_id)

    def _flush_pending_history(self) -> None:
        """Commit a just-made edit before Undo can navigate past it."""
        if self._history_timer.isActive():
            self._history_timer.stop()
            self._commit_history()

    def _restore_history_snapshot(
        self, snapshot: dict, selected_source_ids: object = (),
        active_source_id: str | None = None,
    ) -> None:
        """Restore history and preserve the shared Canvas/Layer selection."""
        if self._history_applying:
            return
        self._history_applying = True
        self._history_restoring = True
        self.setUpdatesEnabled(False)
        try:
            self._apply_project(ProjectDocument.from_dict(snapshot))
            valid_ids = [
                source_id for source_id in selected_source_ids
                if self.store.get(source_id) is not None
            ] if isinstance(selected_source_ids, (tuple, list)) else []
            if active_source_id not in valid_ids:
                active_source_id = valid_ids[-1] if valid_ids else None
            self.store.select_many(valid_ids, active_source_id)
        except Exception as error:
            LOGGER.exception("Undo/redo restore failed")
            report_unexpected_error("Undo/redo restore", error)
            QMessageBox.critical(
                self,
                "실행 취소 오류" if self.translator.language is Language.KOREAN else "Undo/redo error",
                str(error),
            )
        finally:
            self.setUpdatesEnabled(True)
            self._history_restoring = False
            self._history_applying = False
            self.canvas.viewport().update()

    def _autosave_project(self) -> None:
        """Periodically write a recovery document without changing the active project.

        The live models are snapshotted here on the GUI thread; the JSON encode
        and atomic file write run on an AutosaveWorker so a large project does
        not stutter the editor every few seconds.
        """
        if (not self._history_ready or not self._project_dirty
                or self._project_save_worker is not None
                or self._autosave_worker is not None):
            return
        korean = self.translator.language is Language.KOREAN
        try:
            document_data = self._project_document().to_dict()
        except (TypeError, ValueError) as error:
            self.statusBar().showMessage(str(error), 5000)
            return
        self.activity_progress.begin(
            "autosave", "자동 저장" if korean else "Autosaving",
            detail=(self.project_settings.title or "Untitled Project"),
        )
        worker = AutosaveWorker(
            self.autosave, document_data, self.current_project_path,
        )
        worker.succeeded.connect(self._autosave_succeeded)
        worker.failed.connect(self._autosave_failed)
        worker.finished.connect(lambda: self._autosave_thread_finished(worker))
        self._autosave_worker = worker
        worker.start()

    def _autosave_succeeded(self, _path: object) -> None:
        self._update_project_status()
        message = (
            "자동 저장됨" if self.translator.language is Language.KOREAN else "Autosaved"
        )
        self.statusBar().showMessage(message, 2500)

    def _autosave_failed(self, message: str) -> None:
        self.statusBar().showMessage(message, 5000)

    def _autosave_thread_finished(self, worker: AutosaveWorker) -> None:
        self.activity_progress.finish("autosave")
        if self._autosave_worker is worker:
            self._autosave_worker = None
        worker.deleteLater()

    def _offer_recovery(self) -> bool:
        """Offer recovery of the most recently autosaved workspace on startup."""
        try:
            snapshot = None
            for candidate in self.autosave.recoveries():
                project_path = candidate.project_path
                if project_path is not None and project_path.is_file():
                    try:
                        recovery_is_newer = (
                            candidate.saved_at.timestamp() > project_path.stat().st_mtime
                        )
                    except OSError:
                        recovery_is_newer = True
                    if not recovery_is_newer:
                        # A normal successful save should already remove this file,
                        # but stale recoveries can remain after antivirus/file-lock
                        # interference. Never offer one over a newer project file.
                        try:
                            self.autosave.clear_snapshot(candidate)
                        except ProjectError as error:
                            # A locked stale file should not hide a different,
                            # genuinely recoverable workspace.
                            self.statusBar().showMessage(str(error), 5000)
                        continue
                snapshot = candidate
                break
        except ProjectError as error:
            self.statusBar().showMessage(str(error), 5000)
            return False
        if snapshot is None:
            return False
        korean = self.translator.language is Language.KOREAN
        answer = QMessageBox.question(
            self,
            "자동 저장 복구" if korean else "Autosave recovery",
            "저장되지 않은 작업을 복구할까요?" if korean
            else "Restore your most recently autosaved work?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            media_reference = snapshot.project_path or snapshot.path
            if not self._resolve_project_media(snapshot.document, media_reference):
                return False
            self._history_restoring = True
            try:
                self._apply_project(snapshot.document)
                self.current_project_path = snapshot.project_path
                self._legacy_project_path = (
                    snapshot.project_path
                    if snapshot.project_path is not None
                    and snapshot.project_path.suffix.lower() == ".json" else None
                )
                self.upgrade_project_action.setEnabled(self._legacy_project_path is not None)
                self.history.reset(self._project_document().to_dict())
            finally:
                self._history_restoring = False
            self._project_dirty = True
            self._autosave_debounce_timer.start()
            if snapshot.project_path is not None:
                self.recent_projects.add(snapshot.project_path)
            return True
        try:
            self.autosave.clear_snapshot(snapshot)
        except ProjectError as error:
            self.statusBar().showMessage(str(error), 5000)
        return False

    def _clear_recovery(self) -> None:
        try:
            self.autosave.clear(self.current_project_path)
        except ProjectError as error:
            self.statusBar().showMessage(str(error), 5000)

    def _save_project(
        self, force_choose: bool = False, *, wait_for_completion: bool = False,
    ) -> bool:
        """Start a background save and optionally wait in a responsive event loop."""
        active_worker = self._project_save_worker
        if active_worker is not None:
            self.statusBar().showMessage(
                "이미 프로젝트를 저장하고 있습니다."
                if self.translator.language is Language.KOREAN
                else "The project is already being saved.",
                3000,
            )
            if wait_for_completion:
                self._wait_for_project_save(active_worker)
                return self._project_save_succeeded is True
            return False
        target = None if force_choose else self.current_project_path
        if target is None:
            safe_title = "".join(
                character if character.isalnum() or character in " _-" else "_"
                for character in self.project_settings.title
            ).strip() or "playlist"
            default = str(Path.cwd() / f"{safe_title}.pvsproj")
            selected, _ = QFileDialog.getSaveFileName(
                self,
                "프로젝트 저장" if self.translator.language is Language.KOREAN else "Save project",
                default,
                "Playlist Canvas Project (*.pvsproj);;Legacy JSON Project (*.project.json *.json)",
            )
            if not selected:
                return False
            target = Path(selected)
        try:
            document_data = self._project_document().to_dict()
            thumbnail = QImage(self._project_thumbnail_image())
        except (TypeError, ValueError) as error:
            self._show_project_error(
                ProjectError(f"Could not prepare project save: {error}")
            )
            return False

        previous_project_path = self.current_project_path
        worker = ProjectSaveWorker(target, document_data, thumbnail)
        self._project_save_worker = worker
        self._project_save_context = (
            self._project_change_serial, previous_project_path,
        )
        self._project_save_succeeded = None
        worker.succeeded.connect(self._project_save_finished_successfully)
        worker.failed.connect(self._project_save_failed)
        worker.finished.connect(lambda: self._project_save_thread_finished(worker))
        self.save_action.setEnabled(False)
        self.save_as_action.setEnabled(False)
        self._autosave_debounce_timer.stop()
        self.statusBar().showMessage(
            "프로젝트 저장 중..." if self.translator.language is Language.KOREAN
            else "Saving project..."
        )
        korean = self.translator.language is Language.KOREAN
        self.activity_progress.begin(
            "project_save", "프로젝트 저장" if korean else "Saving project",
            detail=Path(target).name,
        )
        worker.start()
        self._update_project_status()
        if wait_for_completion:
            self._wait_for_project_save(worker)
            return self._project_save_succeeded is True
        return True

    def _wait_for_project_save(self, worker: ProjectSaveWorker) -> None:
        """Wait for a required save while continuing to process Qt events."""
        if worker.isRunning():
            event_loop = QEventLoop(self)
            worker.finished.connect(event_loop.quit)
            event_loop.exec()
        QApplication.processEvents()

    def _project_save_finished_successfully(self, saved_path: object) -> None:
        """Commit saved state without hiding edits made during the save."""
        context = self._project_save_context
        if context is None:
            return
        saved_serial, previous_project_path = context
        try:
            self.current_project_path = Path(saved_path)
            self._legacy_project_path = (
                self.current_project_path
                if self.current_project_path.suffix.lower() == ".json" else None
            )
            self.upgrade_project_action.setEnabled(self._legacy_project_path is not None)
            self.recent_projects.add(self.current_project_path)
            unchanged_since_snapshot = saved_serial == self._project_change_serial
            self._project_dirty = not unchanged_since_snapshot
            if unchanged_since_snapshot:
                self.autosave.clear(previous_project_path)
                self.autosave.clear(self.current_project_path)
            else:
                self._autosave_debounce_timer.start()
            self._update_project_status()
            korean = self.translator.language is Language.KOREAN
            message = (
                "프로젝트를 저장했습니다. 저장 중 변경된 내용은 아직 저장되지 않았습니다."
                if korean and not unchanged_since_snapshot else
                "Project saved. Changes made during saving remain unsaved."
                if not unchanged_since_snapshot else
                "프로젝트를 저장했습니다." if korean else "Project saved."
            )
            self.statusBar().showMessage(message, 4000)
        except ProjectError as error:
            self._show_project_error(error)
            self._project_save_succeeded = False
            return
        self._project_save_succeeded = True

    def _project_save_failed(self, message: str) -> None:
        self._project_save_succeeded = False
        if self._project_dirty:
            self._autosave_debounce_timer.start()
        self._show_project_error(ProjectError(message))

    def _project_save_thread_finished(self, worker: ProjectSaveWorker) -> None:
        self.activity_progress.finish("project_save")
        if self._project_save_worker is worker:
            self._project_save_worker = None
            self._project_save_context = None
        self.save_action.setEnabled(True)
        self.save_as_action.setEnabled(True)
        self._update_project_status()
        worker.deleteLater()

    def _open_project(self) -> None:
        """Choose a portable package or legacy JSON project."""
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "프로젝트 열기" if self.translator.language is Language.KOREAN else "Open project",
            "",
            "Playlist Canvas Project (*.pvsproj *.project.json *.json)",
        )
        if not selected:
            return
        self._open_project_with_confirmation(Path(selected))

    def _open_project_with_confirmation(self, path: Path) -> bool:
        """Replace the workspace after safely resolving unsaved changes."""
        previous_project_path = self.current_project_path
        had_unsaved_changes = self._project_dirty
        if not self._confirm_unsaved_changes():
            return False
        loaded = self._load_project_path(Path(path))
        if loaded and had_unsaved_changes:
            try:
                self.autosave.clear(previous_project_path)
            except ProjectError as error:
                self.statusBar().showMessage(str(error), 5000)
        return loaded

    def open_project_path(self, path: Path) -> bool:
        """Open a project requested by Explorer or another external launcher."""
        return self._load_project_path(Path(path))

    def _confirm_unsaved_changes(self) -> bool:
        """Save, explicitly discard, or keep the active unsaved workspace."""
        # Opening/replacing the workspace while an older snapshot is still
        # saving would let its completion overwrite the new active path.
        if self._project_save_worker is not None:
            self._wait_for_project_save(self._project_save_worker)
        if not self._project_dirty:
            return True
        korean = self.translator.language is Language.KOREAN
        response = QMessageBox.warning(
            self,
            "저장되지 않은 변경 사항" if korean else "Unsaved changes",
            "현재 프로젝트에 저장되지 않은 변경 사항이 있습니다. 계속하기 전에 저장할까요?"
            if korean else
            "The current project has unsaved changes. Save before continuing?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if response == QMessageBox.StandardButton.Save:
            self._save_project(wait_for_completion=True)
            return not self._project_dirty
        return response == QMessageBox.StandardButton.Discard

    def _load_project_path(self, path: Path) -> bool:
        """Restore a selected project path through the normal safe load workflow."""
        korean = self.translator.language is Language.KOREAN
        stage = "프로젝트 파일 읽기" if korean else "Reading project file"
        self.activity_progress.begin(
            "project_load", "프로젝트 불러오기" if korean else "Loading project",
            detail=f"{stage} · {path.name}",
        )
        QApplication.processEvents()
        previous_document = ProjectDocument.from_dict(self._project_document().to_dict())
        previous_path = self.current_project_path
        previous_legacy_path = self._legacy_project_path
        previous_dirty = self._project_dirty
        previous_selection = self.store.selected_ids
        previous_active = self.store.selected.id if self.store.selected is not None else None
        apply_started = False
        try:
            document = ProjectService.load(path)
            if document.app_version and document.app_version != __version__:
                korean = self.translator.language is Language.KOREAN
                QMessageBox.warning(
                    self,
                    "프로젝트 버전 차이" if korean else "Project version differs",
                    (
                        "이 프로젝트는 다른 버전의 Playlist Canvas에서 저장되었습니다.\n\n"
                        f"프로젝트 저장 버전: {document.app_version}\n"
                        f"현재 프로그램 버전: {__version__}\n\n"
                        "일부 기능이나 표시 결과가 달라질 수 있습니다. 프로젝트를 계속 엽니다."
                        if korean else
                        "This project was saved with a different version of Playlist Canvas.\n\n"
                        f"Project version: {document.app_version}\n"
                        f"Current app version: {__version__}\n\n"
                        "Some features or visual results may differ. The project will continue opening."
                    ),
                    QMessageBox.StandardButton.Ok,
                    QMessageBox.StandardButton.Ok,
                )
            if (document.settings.title == "Untitled Project"
                    and path.suffix.lower() == ".json"):
                document.settings.title = path.stem.removesuffix(".project")
            stage = "누락된 미디어 확인" if korean else "Validating project media"
            self.activity_progress.update("project_load", detail=stage)
            QApplication.processEvents()
            if not self._resolve_project_media(document, path):
                return False
            stage = "프로젝트 작업공간 적용" if korean else "Applying project workspace"
            self.activity_progress.update("project_load", 0.8, stage)
            QApplication.processEvents()
            self._history_restoring = True
            apply_started = True
            try:
                self._apply_project(document)
                self.history.reset(self._project_document().to_dict())
            finally:
                self._history_restoring = False
            self.current_project_path = path.resolve()
            self._legacy_project_path = (
                self.current_project_path
                if self.current_project_path.suffix.lower() == ".json" else None
            )
            self.upgrade_project_action.setEnabled(self._legacy_project_path is not None)
            self.recent_projects.add(self.current_project_path)
            self._project_dirty = False
            self._update_project_status()
            message = "프로젝트를 불러왔습니다." if self.translator.language is Language.KOREAN else "Project loaded."
            self.statusBar().showMessage(message, 4000)
            if self._legacy_project_path is not None:
                QTimer.singleShot(0, self._offer_legacy_upgrade)
            return True
        except Exception as error:
            rollback_error: Exception | None = None
            if apply_started:
                try:
                    self._history_restoring = True
                    self._apply_project(previous_document)
                    valid_selection = [
                        source_id for source_id in previous_selection
                        if self.store.get(source_id) is not None
                    ]
                    active = previous_active if previous_active in valid_selection else None
                    self.store.select_many(valid_selection, active)
                    self.history.reset(previous_document.to_dict())
                except Exception as restore_error:  # pragma: no cover - last-resort diagnostics
                    rollback_error = restore_error
                    LOGGER.exception("Failed to restore the workspace after project load failure")
                finally:
                    self._history_restoring = False
            self.current_project_path = previous_path
            self._legacy_project_path = previous_legacy_path
            self.upgrade_project_action.setEnabled(previous_legacy_path is not None)
            self._project_dirty = previous_dirty
            self._update_project_status()
            self._show_project_load_crash(path, stage, error, rollback_error)
            return False
        finally:
            self.activity_progress.finish("project_load")

    def _show_project_load_crash(
        self, path: Path, stage: str, error: Exception,
        rollback_error: Exception | None = None,
    ) -> None:
        """Show detailed, copyable diagnostics for a recoverable load failure."""
        LOGGER.exception("Project load failed during %s: %s", stage, path)
        cause: BaseException = error
        while cause.__cause__ is not None:
            cause = cause.__cause__
        korean = self.translator.language is Language.KOREAN
        guidance = self._project_load_guidance(cause, korean)
        traceback_text = "".join(
            traceback_module.format_exception(type(error), error, error.__traceback__)
        )
        rollback_text = ""
        if rollback_error is not None:
            rollback_text = (
                "\n\nWORKSPACE RESTORE ERROR\n" + "".join(
                    traceback_module.format_exception(
                        type(rollback_error), rollback_error, rollback_error.__traceback__
                    )
                )
            )
        resolved_path = path.expanduser().resolve()
        report = (
            f"Playlist Canvas {__version__}\n"
            f"Project: {resolved_path}\n"
            f"Stage: {stage}\n"
            f"Exception: {type(error).__name__}: {error}\n"
            f"Root cause: {type(cause).__name__}: {cause}\n"
            f"Workspace restored: {'no' if rollback_error else 'yes'}\n\n"
            f"TRACEBACK\n{traceback_text}{rollback_text}"
        )
        ProjectCrashReportDialog(
            project_path=str(resolved_path),
            stage=stage,
            exception_type=type(error).__name__,
            exception_message=str(error),
            cause_type=type(cause).__name__,
            cause_message=str(cause),
            guidance=guidance,
            report_text=report,
            log_path=str(log_directory()),
            korean=korean,
            parent=self,
        ).exec()

    @staticmethod
    def _project_load_guidance(error: BaseException, korean: bool) -> str:
        """Return a practical explanation based on the deepest load exception."""
        name = type(error).__name__
        message = str(error).casefold()
        if name == "JSONDecodeError":
            return ("프로젝트 JSON 구조가 손상되었습니다. 백업 또는 자동 복구 파일을 사용해 보세요."
                    if korean else "The project JSON is malformed. Try a backup or autosave recovery file.")
        if name == "BadZipFile" or "zip" in message:
            return ("프로젝트 패키지가 손상되었거나 올바른 .pvsproj 파일이 아닙니다. 다시 복사하거나 백업을 사용해 보세요."
                    if korean else "The project package is damaged or is not a valid .pvsproj file. Copy it again or use a backup.")
        if name in {"PermissionError", "FileNotFoundError"}:
            return ("파일 위치와 읽기 권한을 확인한 뒤 다시 시도하세요."
                    if korean else "Check the file location and read permission, then try again.")
        if name in {"UnicodeDecodeError", "UnicodeError"}:
            return ("프로젝트 문자가 UTF-8 형식이 아닙니다. 원본 프로그램에서 다시 저장해 보세요."
                    if korean else "The project text is not valid UTF-8. Save it again from the original application.")
        if name in {"ValueError", "TypeError", "KeyError"}:
            return ("프로젝트 데이터가 지원 형식과 맞지 않습니다. 상세 보고서에서 잘못된 항목을 확인하세요."
                    if korean else "The project data does not match the supported format. Check the detailed report for the invalid field.")
        return ("상세 보고서를 복사해 문제 파일과 함께 개발자에게 전달하세요."
                if korean else "Copy the detailed report and provide it with the problematic file to the developer.")

    def _resolve_project_media(self, document: ProjectDocument, project_path: Path) -> bool:
        """Relink missing project assets before the document changes the live workspace."""
        missing = ProjectMediaService.validate(document, project_path)
        if not missing:
            return True
        dialog = MissingMediaDialog(missing, self.translator, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return False
        ProjectMediaService.apply_replacements(document, missing)
        return True

    def _apply_project(self, document: ProjectDocument) -> None:
        """Restore project content while preserving app-wide UI preferences."""
        self.project_settings = document.settings
        self.project_content_service.replace(document.content_library)
        self._project_theme_metadata = document.theme
        self._project_language_metadata = document.language
        self.canvas.scene_model.set_artboard_size(
            document.canvas.width, document.canvas.height
        )
        self.grid_action.setChecked(document.canvas.show_grid)
        self.snap_action.setChecked(document.canvas.snap_enabled)
        self.canvas.scene_model.snap_enabled = document.canvas.snap_enabled
        self.canvas.set_zoom(document.canvas.zoom)
        self.store.replace(document.sources, document.groups)
        self.playlist_service.replace(document.playlist)
        self._synchronize_content_library()

    def _project_thumbnail_image(self) -> QImage:
        """Return the custom thumbnail or a clean raster of the current artboard."""
        if (self.project_settings.thumbnail_mode == "custom"
                and self.project_settings.thumbnail_path):
            image = QImage(self.project_settings.thumbnail_path)
            if not image.isNull():
                return image.scaled(
                    480, 270, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
        image = CanvasSnapshot.capture(self.canvas.scene_model, output_scale=0.5)
        return image.scaled(
            480, 270, Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def _show_project_settings(self) -> None:
        """Edit project-scoped identity, content policy, and thumbnail."""
        artboard = self.canvas.scene_model.artboard_rect
        original_canvas_size = (round(artboard.width()), round(artboard.height()))
        thumbnail = QPixmap.fromImage(self._project_thumbnail_image())
        dialog = ProjectSettingsDialog(
            self.project_settings, self.translator, thumbnail, self,
            canvas_size=original_canvas_size,
        )
        if dialog.exec() == dialog.DialogCode.Accepted:
            self.project_settings = dialog.selected_settings
            if dialog.selected_canvas_size != original_canvas_size:
                self._resize_project_canvas(
                    original_canvas_size,
                    dialog.selected_canvas_size,
                    dialog.scale_canvas_content,
                )
            self._schedule_history()
            self._update_project_status()

    def _resize_project_canvas(
        self, old_size: tuple[int, int], new_size: tuple[int, int],
        scale_content: bool,
    ) -> None:
        """Resize the artboard while preserving or ratio-adapting current sources."""
        old_width, old_height = old_size
        new_width, new_height = new_size
        selected_ids = self.store.selected_ids
        active_id = self.store.selected.id if self.store.selected is not None else None
        sources = self.store.sources()
        if scale_content:
            resized = self._adapt_sources_to_canvas(
                sources, old_width, old_height, new_width, new_height,
            )
        else:
            resized = [Source.from_dict(source.to_dict()) for source in sources]
            for source in resized:
                if (
                    source.source_type is SourceType.BACKGROUND
                    and abs(source.x) < 0.01 and abs(source.y) < 0.01
                    and abs(source.width - old_width) < 0.01
                    and abs(source.height - old_height) < 0.01
                ):
                    source.width = new_width
                    source.height = new_height
        self._history_restoring = True
        try:
            self.canvas.scene_model.set_artboard_size(new_width, new_height)
            self.store.replace(resized, self.store.groups())
            valid_selection = [
                source_id for source_id in selected_ids
                if self.store.get(source_id) is not None
            ]
            self.store.select_many(
                valid_selection,
                active_id if active_id in valid_selection else None,
            )
            self.canvas.fit_artboard()
        finally:
            self._history_restoring = False

    def _show_lrc_generator(self) -> None:
        """Open the audio-assisted LRC authoring workflow."""
        dialog = LrcGeneratorDialog(
            self.project_content_service.items,
            self.translator,
            self,
            playlist_tracks=self.playlist_service.tracks,
        )
        dialog.exec()
        if dialog.saved_paths and dialog.add_saved_files_to_project:
            added = self.project_content_service.add_paths(dialog.saved_paths)
            if added:
                korean = self.translator.language is Language.KOREAN
                self.statusBar().showMessage(
                    f"저장한 LRC 파일 {added}개를 프로젝트 콘텐츠에 추가했습니다."
                    if korean else
                    f"Added {added} saved LRC file(s) to project content.",
                    5000,
                )

    def _offer_legacy_upgrade(self) -> None:
        """Offer a non-destructive package upgrade after a legacy JSON load."""
        if (self._legacy_project_path is None
                or self.current_project_path != self._legacy_project_path):
            return
        korean = self.translator.language is Language.KOREAN
        response = QMessageBox.question(
            self,
            "레거시 프로젝트" if korean else "Legacy project",
            "이 프로젝트는 레거시 JSON 형식입니다. 원본 JSON은 유지하면서 콘텐츠와 "
            "썸네일을 포함할 수 있는 .pvsproj 형식으로 업그레이드할까요?"
            if korean else
            "This project uses the legacy JSON format. Upgrade it to a .pvsproj package "
            "that can contain content and a thumbnail? The original JSON will be kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if response == QMessageBox.StandardButton.Yes:
            self._upgrade_legacy_project()

    def _upgrade_legacy_project(self) -> None:
        """Save the active legacy JSON as a validated portable package."""
        legacy_path = self._legacy_project_path
        if legacy_path is None or not legacy_path.is_file():
            self.upgrade_project_action.setEnabled(False)
            return
        base_name = legacy_path.stem.removesuffix(".project")
        default = legacy_path.with_name(f"{base_name}.pvsproj")
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "업그레이드 프로젝트 저장"
            if self.translator.language is Language.KOREAN else "Save upgraded project",
            str(default),
            "Playlist Canvas Project (*.pvsproj)",
        )
        if not selected:
            return
        target = Path(selected).expanduser()
        if target.suffix.lower() != ProjectService.PACKAGE_SUFFIX:
            target = target.with_suffix(ProjectService.PACKAGE_SUFFIX)
        try:
            upgraded = ProjectService.save(
                target, self._project_document(), self._project_thumbnail_image()
            )
            # Reloading verifies both the package container and manifest before the
            # editor switches its active project away from the original JSON.
            ProjectService.load(upgraded)
            self.recent_projects.remove(legacy_path)
            self.recent_projects.add(upgraded)
            self.autosave.clear(legacy_path)
            self.current_project_path = upgraded
            self._legacy_project_path = None
            self.upgrade_project_action.setEnabled(False)
            self._project_dirty = False
            self._update_project_status()
            QMessageBox.information(
                self,
                "업그레이드 완료" if self.translator.language is Language.KOREAN
                else "Upgrade complete",
                f"새 프로젝트 패키지를 저장했습니다.\n{upgraded}"
                if self.translator.language is Language.KOREAN else
                f"Saved the upgraded project package.\n{upgraded}",
            )
        except ProjectError as error:
            self._show_project_error(error)

    def _show_project_error(self, error: ProjectError) -> None:
        """Display a concise persistence failure without crashing the application."""
        LOGGER.error("Project operation failed: %s", error)
        QMessageBox.critical(
            self,
            "프로젝트 오류" if self.translator.language is Language.KOREAN else "Project error",
            str(error),
        )

    def _toggle_grid(self, visible: bool) -> None:
        self.canvas.scene_model.show_grid = visible
        self.canvas.scene_model.update()
        self._schedule_history()

    def retranslate(self) -> None:
        """Refresh all user-interface strings for the active language."""
        text = self.translator.text
        korean = self.translator.language is Language.KOREAN
        self.setWindowTitle(text("app_title"))
        self.new_action.setText(text("new"))
        self.open_action.setText(text("open"))
        self.save_action.setText(text("save"))
        self.save_as_action.setText(
            "다른 이름으로 저장" if self.translator.language is Language.KOREAN else "Save As"
        )
        self.undo_action.setText("실행 취소" if self.translator.language is Language.KOREAN else "Undo")
        self.redo_action.setText("다시 실행" if self.translator.language is Language.KOREAN else "Redo")
        self.center_horizontal_action.setText("가로 중앙" if korean else "Center H")
        self.center_vertical_action.setText("세로 중앙" if korean else "Center V")
        horizontal_help = (
            "선택 요소를 캔버스의 가로 중앙에 배치합니다. (Ctrl+Shift+H)"
            if korean else
            "Center selected sources horizontally on the canvas. (Ctrl+Shift+H)"
        )
        vertical_help = (
            "선택 요소를 캔버스의 세로 중앙에 배치합니다. (Ctrl+Shift+V)"
            if korean else
            "Center selected sources vertically on the canvas. (Ctrl+Shift+V)"
        )
        self.center_horizontal_action.setToolTip(horizontal_help)
        self.center_horizontal_action.setStatusTip(horizontal_help)
        self.center_vertical_action.setToolTip(vertical_help)
        self.center_vertical_action.setStatusTip(vertical_help)
        self.zoom_out_button.setToolTip(
            "축소 (Ctrl+-)" if korean else "Zoom out (Ctrl+-)"
        )
        self.zoom_in_button.setToolTip(
            "확대 (Ctrl+=)" if korean else "Zoom in (Ctrl+=)"
        )
        self.zoom_reset_button.setToolTip(
            "100% ↔ 화면 맞춤" if korean else "100% ↔ fit to view"
        )
        self.cut_action.setText("잘라내기" if korean else "Cut")
        self.copy_action.setText("복사" if korean else "Copy")
        self.paste_action.setText("붙여넣기" if korean else "Paste")
        self.duplicate_action.setText(
            "복제" if self.translator.language is Language.KOREAN else "Duplicate"
        )
        self.select_all_action.setText(
            "전체 선택" if self.translator.language is Language.KOREAN else "Select all"
        )
        self.presets_action.setText(
            "디자인 프리셋" if self.translator.language is Language.KOREAN
            else "Design Presets"
        )
        self.save_preset_action.setText(
            "현재 캔버스를 프리셋으로 저장…"
            if self.translator.language is Language.KOREAN
            else "Save current canvas as preset…"
        )
        self.ai_project_builder_action.setText(
            "AI 프로젝트 빌더" if self.translator.language is Language.KOREAN
            else "AI Project Builder"
        )
        self.fit_action.setText(text("fit_canvas"))
        self.grid_action.setText(text("grid"))
        self.canvas.scene_model.set_placeholder_language(korean)
        self.delete_action.setText(text("delete"))
        self.export_action.setText(text("export"))
        self.preview_action.setText("미리보기" if self.translator.language is Language.KOREAN else "Preview")
        self.settings_action.setText("설정" if self.translator.language is Language.KOREAN else "Settings")
        self.lrc_generator_action.setText(
            "LRC 파일 생성기" if self.translator.language is Language.KOREAN
            else "LRC File Generator"
        )
        self.file_menu.setTitle("파일" if self.translator.language is Language.KOREAN else "File")
        self.recent_projects_menu.setTitle(
            "최근 프로젝트" if korean else "Recent projects"
        )
        self._rebuild_recent_projects_menu()
        self.project_menu.setTitle("프로젝트" if self.translator.language is Language.KOREAN else "Project")
        self.edit_menu.setTitle("편집" if self.translator.language is Language.KOREAN else "Edit")
        self.insert_menu.setTitle("추가" if self.translator.language is Language.KOREAN else "Add")
        self.view_menu.setTitle("보기" if self.translator.language is Language.KOREAN else "View")
        self.tools_menu.setTitle("도구" if self.translator.language is Language.KOREAN else "Tools")
        self.help_menu.setTitle("도움말" if self.translator.language is Language.KOREAN else "Help")
        self.exit_action.setText("종료" if self.translator.language is Language.KOREAN else "Exit")
        self.clear_selection_action.setText(
            "선택 해제" if self.translator.language is Language.KOREAN else "Clear selection"
        )
        self.help_action.setText(
            "사용 설명서" if self.translator.language is Language.KOREAN
            else "User Guide"
        )
        self.shortcuts_action.setText(
            "단축키 안내" if self.translator.language is Language.KOREAN else "Keyboard shortcuts"
        )
        self.check_updates_action.setText(
            "업데이트 확인" if self.translator.language is Language.KOREAN
            else "Check for updates"
        )
        self.about_action.setText(
            "프로그램 정보" if self.translator.language is Language.KOREAN
            else "About Playlist Canvas"
        )
        self.project_settings_action.setText(
            "프로젝트 설정" if self.translator.language is Language.KOREAN else "Project settings"
        )
        self.upgrade_project_action.setText(
            "레거시 프로젝트 업그레이드…"
            if self.translator.language is Language.KOREAN else "Upgrade legacy project…"
        )
        self.show_playlist_action.setText(
            "플레이리스트 열기" if self.translator.language is Language.KOREAN else "Show Playlist"
        )
        self.show_timeline_action.setText(
            "타임라인 열기" if self.translator.language is Language.KOREAN else "Show Timeline"
        )
        self.playlist_files_action.setText(
            "목록 파일" if self.translator.language is Language.KOREAN else "Playlist files"
        )
        category_titles = {
            "basic": "기본 요소" if korean else "Basic sources",
            "playback": "재생 정보" if korean else "Playback information",
            "branding": "브랜딩 및 배경" if korean else "Branding and background",
            "audio_effects": "오디오 시각 효과" if korean else "Audio visuals",
        }
        for category, menu in self.insert_category_menus.items():
            menu.setTitle(category_titles[category])
        for source_type, action in self.source_insert_actions.items():
            label = self._source_type_label(source_type)
            action.setText(label)
            description, settings = self._source_hover_help(source_type)
            action.setToolTip(f"{description} {settings}")
            action.setStatusTip(description)
        self.language_menu.setTitle(text("language"))
        self.theme_menu.setTitle("테마" if korean else "Theme")
        self.theme_actions[Theme.LIGHT].setText("라이트" if korean else "Light")
        self.theme_actions[Theme.DARK].setText("다크" if korean else "Dark")
        self.theme_actions[Theme.AUTO].setText("자동" if korean else "Auto")
        self.panels_action.setText(
            "왼쪽 패널 표시" if korean else "Show left panel"
        )
        self.inspector_panel_action.setText(
            "오른쪽 속성 패널 표시" if korean else "Show right properties panel"
        )
        self.bottom_panel_action.setText(
            "하단 작업 패널 표시" if korean else "Show bottom workspace"
        )
        self.panels_action.setToolTip(
            "요소·프로젝트 콘텐츠·레이어 패널을 표시하거나 숨깁니다."
            if korean else
            "Show or hide the Sources, Project content, and Layers panel."
        )
        self.inspector_panel_action.setToolTip(
            "오른쪽 속성 패널을 표시하거나 숨깁니다."
            if korean else "Show or hide the right properties panel."
        )
        self.bottom_panel_action.setToolTip(
            "플레이리스트·타임라인·미리보기 패널을 표시하거나 숨깁니다."
            if korean else
            "Show or hide the Playlist, Timeline, and Preview workspace."
        )
        self.sidebar_title.setText(text("add_to_canvas"))
        self.left_tabs.setTabText(
            0, "요소" if self.translator.language is Language.KOREAN else "Sources"
        )
        self.left_tabs.setTabText(
            1, "프로젝트 콘텐츠" if self.translator.language is Language.KOREAN else "Project content"
        )
        self.left_tabs.setTabText(
            2, "레이어" if self.translator.language is Language.KOREAN else "Layers"
        )
        self.left_tabs.setTabToolTip(
            0, "캔버스에 추가할 요소" if korean else "Sources to add to the Canvas"
        )
        self.left_tabs.setTabToolTip(
            1, "프로젝트 콘텐츠" if korean else "Project content"
        )
        self.left_tabs.setTabToolTip(
            2, "캔버스 레이어와 그룹" if korean else "Canvas layers and groups"
        )
        self.bottom_tabs.setTabText(
            0, "플레이리스트" if self.translator.language is Language.KOREAN else "Playlist"
        )
        self.bottom_tabs.setTabText(
            1, "타임라인" if self.translator.language is Language.KOREAN else "Timeline"
        )
        self.bottom_tabs.setTabText(
            2, "미리보기" if self.translator.language is Language.KOREAN else "Preview"
        )
        self.source_search.setPlaceholderText(
            "요소 검색…" if korean else "Search sources…"
        )
        category_titles = {
            "basic": "기본 요소" if korean else "BASIC",
            "playback": "재생 정보" if korean else "PLAYBACK",
            "audio": "오디오 효과" if korean else "AUDIO EFFECTS",
            "scene": "장면 꾸미기" if korean else "SCENE",
        }
        for category, label in self._source_category_titles.items():
            label.setText(category_titles[category])
        tab_titles = {
            "all": "전체" if korean else "All",
            "basic": "기본" if korean else "Basic",
            "playback": "재생" if korean else "Playback",
            "audio": "오디오" if korean else "Audio",
            "scene": "장면" if korean else "Scene",
        }
        for index, category in enumerate(self._source_tab_categories):
            self.source_tab_bar.setTabText(index, tab_titles[category])
        for source_type, button in self._source_buttons.items():
            label = self._source_type_label(source_type)
            is_variant = source_type in self._source_variant_parents
            title = (
                f"{label} 템플릿" if korean and is_variant else
                f"{label} template" if is_variant else label
            )
            button.set_card_text(
                title, self._source_palette_summary(source_type),
                variant=is_variant,
            )
            self._update_source_button_help(source_type, button, label)
        for parent_type, toggle in self._source_variant_toggles.items():
            expanded = toggle.isChecked()
            toggle.setText("▾" if expanded else "▸")
            parent_label = self._source_type_label(parent_type)
            toggle.setToolTip(
                f"{parent_label} 변형 템플릿 펼치기/접기"
                if korean else f"Expand or collapse {parent_label} templates"
            )
            toggle.setAccessibleName(toggle.toolTip())
        self._filter_source_cards(self.source_search.text())
        self._update_project_status()
        self.snap_action.setToolTip(
            "객체를 그리드와 정렬 가이드에 맞춥니다."
            if self.translator.language is Language.KOREAN
            else "Snap objects to the grid and alignment guides."
        )
        self.grid_action.setToolTip(
            "캔버스 작업 공간 전체에 40px 간격의 그리드를 표시합니다. 영상에는 포함되지 않습니다."
            if self.translator.language is Language.KOREAN else
            "Show a 40 px grid across the complete Canvas workspace. It is not included in video output."
        )
        self.export_action.setToolTip(
            "현재 Canvas와 Playlist를 MP4 파일로 렌더링합니다."
            if self.translator.language is Language.KOREAN
            else "Render the current Canvas and Playlist as an MP4 file."
        )
        self.preview_action.setToolTip(
            "하단 미리보기 탭에서 전체 플레이리스트를 실제 음원과 함께 확인합니다."
            if self.translator.language is Language.KOREAN
            else "Open the bottom Preview tab with the complete playlist and actual audio."
        )
        self.playlist_files_action.setToolTip(
            "YouTube 설명문과 CSV 목록 파일을 만듭니다."
            if self.translator.language is Language.KOREAN
            else "Create YouTube description and playlist CSV files."
        )
        self.settings_action.setToolTip(
            "FFmpeg, 출력, 렌더링 기본값을 설정합니다."
            if self.translator.language is Language.KOREAN
            else "Configure FFmpeg, output, and rendering defaults."
        )

    def _toggle_snap(self, enabled: bool) -> None:
        self.canvas.scene_model.snap_enabled = enabled
        self.canvas.scene_model.clear_alignment_guides()
        self._schedule_history()

    def _delete_selected_sources(self) -> None:
        """Remove each selected, non-locked source from the canvas."""
        selected_ids = [
            item.source.id
            for item in self.canvas.scene_model.selectedItems()
            if hasattr(item, "source") and not item.source.locked
        ]
        for source_id in selected_ids:
            self.store.remove(source_id)

    def canvas_fit(self) -> None:
        self.canvas.fit_artboard()

    def _show_bottom_panel(self, index: int) -> None:
        """Reveal and focus a bottom workspace tab."""
        if self.bottom_workspace_stack.isHidden():
            self.bottom_panel_action.setChecked(True)
        selected = max(0, min(self.bottom_tabs.count() - 1, index))
        self.bottom_tabs.setCurrentIndex(selected)
        sizes = self.workspace_splitter.sizes()
        if len(sizes) == 2 and sizes[1] < 180:
            total = max(500, sum(sizes))
            self.workspace_splitter.setSizes([max(300, total - 280), 280])
        if selected == 0:
            self.playlist_editor.list_widget.setFocus(
                Qt.FocusReason.ShortcutFocusReason
            )
        elif selected == 1:
            self.timeline_panel.track_table.setFocus(
                Qt.FocusReason.ShortcutFocusReason
            )

    def closeEvent(self, event: QCloseEvent) -> None:
        """Avoid destroying a running FFmpeg thread during application shutdown."""
        if self._project_save_worker is not None:
            message = (
                "프로젝트 저장이 완료된 후 종료해 주세요."
                if self.translator.language is Language.KOREAN
                else "Wait for the project save to finish before closing."
            )
            self.statusBar().showMessage(message, 5000)
            event.ignore()
            return
        if self._animation_preview_active:
            self.animation_preview_controller.cancel()
        if self._inline_preview is not None:
            self._finish_inline_preview()
        if self._export_preparation_cancel is not None:
            if self._export_preparation_cancel.is_set():
                cancellation_requested = True
            elif self._export_dialog is not None:
                cancellation_requested = self._export_dialog.request_cancel()
            else:
                self._export_preparation_cancel.set()
                cancellation_requested = True
            if cancellation_requested:
                self._close_after_export_cancel = True
                message = (
                    "내보내기를 안전하게 취소한 뒤 종료합니다."
                    if self.translator.language is Language.KOREAN else
                    "Closing after export is cancelled safely..."
                )
                self.statusBar().showMessage(message, 5000)
            event.ignore()
            return
        if self._render_worker and self._render_worker.isRunning():
            if self._export_dialog is not None:
                cancellation_requested = (
                    self._export_dialog.is_cancelling
                    or self._export_dialog.request_cancel()
                )
            else:
                self._render_worker.cancel()
                cancellation_requested = True
            if cancellation_requested:
                self._close_after_export_cancel = True
                message = (
                    "내보내기를 안전하게 취소한 뒤 종료합니다."
                    if self.translator.language is Language.KOREAN else
                    "Closing after export is cancelled safely..."
                )
                self.statusBar().showMessage(message, 5000)
            event.ignore()
            return
        if self._ffmpeg_install_worker and self._ffmpeg_install_worker.isRunning():
            self._ffmpeg_install_worker.cancel()
            message = "FFmpeg 다운로드를 취소하는 중입니다." if self.translator.language is Language.KOREAN else "Cancelling FFmpeg download..."
            self.statusBar().showMessage(message, 5000)
            event.ignore()
            return
        if self._ffmpeg_catalog_worker and self._ffmpeg_catalog_worker.isRunning():
            self._ffmpeg_catalog_worker.cancel()
            self._close_after_ffmpeg_catalog_cancel = True
            message = (
                "FFmpeg 버전 확인을 중단한 뒤 종료합니다."
                if self.translator.language is Language.KOREAN else
                "Closing after the FFmpeg version check stops..."
            )
            self.statusBar().showMessage(message, 5000)
            event.ignore()
            return
        if self._update_download_worker and self._update_download_worker.isRunning():
            self._update_download_worker.cancel()
            message = "업데이트 다운로드를 취소하는 중입니다." if self.translator.language is Language.KOREAN else "Cancelling update download..."
            self.statusBar().showMessage(message, 5000)
            event.ignore()
            return
        if self._project_dirty and not self._update_install_authorized:
            korean = self.translator.language is Language.KOREAN
            response = QMessageBox.warning(
                self,
                "저장되지 않은 변경 사항" if korean else "Unsaved changes",
                "프로젝트에 저장되지 않은 변경 사항이 있습니다. 종료하기 전에 저장할까요?"
                if korean else "This project has unsaved changes. Save before closing?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if response == QMessageBox.StandardButton.Save:
                self._save_project(wait_for_completion=True)
                if self._project_dirty:
                    # The user cancelled Save As or saving failed; stay in the editor.
                    event.ignore()
                    return
            elif response != QMessageBox.StandardButton.Discard:
                # Fail closed: Cancel, Escape, or an unrecognized response keeps
                # the unsaved workspace open. Only explicit Discard may exit.
                event.ignore()
                return
        if self._workspace_settings_timer.isActive():
            self._workspace_settings_timer.stop()
        self._autosave_timer.stop()
        self._autosave_debounce_timer.stop()
        # Let a running recovery write finish before _clear_recovery(), otherwise
        # its atomic replace re-creates the file we just deleted and the next
        # launch offers a stale recovery after a clean exit.
        if self._autosave_worker is not None and self._autosave_worker.isRunning():
            self._autosave_worker.wait(3000)
        self._save_workspace_layout()
        QSettings().sync()
        self._clear_recovery()
        self.canvas.release_video_decoders()
        self.smooth_scroll.uninstall()
        application = QApplication.instance()
        if application is not None:
            try:
                application.focusChanged.disconnect(
                    self._sync_canvas_shortcut_actions
                )
            except (RuntimeError, TypeError):
                pass
        event.accept()

    def _apply_style(self) -> None:
        dark = self.theme_service.effective_theme is Theme.DARK
        colors = {
            "window": "#14181F" if dark else "#F4F7FB",
            "panel": "#1C222C" if dark else "#FFFFFF",
            "field": "#131820" if dark else "#F7F9FC",
            "button": "#293241" if dark else "#EEF2F7",
            "hover": "#354258" if dark else "#E0EAF5",
            "text": "#E7EDF5" if dark else "#18212D",
            "muted": "#9BA9BA" if dark else "#64748B",
            "border": "#303947" if dark else "#D7E0EA",
            "disabled": "#202733" if dark else "#E6EBF1",
            "alternate": "#202733" if dark else "#F0F4F8",
            "shadow": "#000000" if dark else "#7C8A9A",
        }
        application = QApplication.instance()
        effective_name = "dark" if dark else "light"
        palette_changed = (
            application is not None
            and application.property("playlistCanvasEffectiveTheme") != effective_name
        )
        if application is not None and palette_changed:
            palette = QPalette()
            palette.setColor(QPalette.ColorRole.Window, QColor(colors["window"]))
            palette.setColor(QPalette.ColorRole.WindowText, QColor(colors["text"]))
            palette.setColor(QPalette.ColorRole.Base, QColor(colors["field"]))
            palette.setColor(QPalette.ColorRole.AlternateBase, QColor(colors["alternate"]))
            palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(colors["panel"]))
            palette.setColor(QPalette.ColorRole.ToolTipText, QColor(colors["text"]))
            palette.setColor(QPalette.ColorRole.Text, QColor(colors["text"]))
            palette.setColor(QPalette.ColorRole.Button, QColor(colors["button"]))
            palette.setColor(QPalette.ColorRole.ButtonText, QColor(colors["text"]))
            palette.setColor(QPalette.ColorRole.BrightText, QColor("#FFFFFF"))
            palette.setColor(QPalette.ColorRole.Highlight, QColor("#1685D1"))
            palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
            palette.setColor(QPalette.ColorRole.Link, QColor("#1685D1"))
            palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(colors["muted"]))
            palette.setColor(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,
                QColor(colors["muted"]),
            )
            palette.setColor(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText,
                QColor(colors["muted"]),
            )
            palette.setColor(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText,
                QColor(colors["muted"]),
            )
            application.setPalette(palette)
            application.setProperty("playlistCanvasEffectiveTheme", effective_name)

        bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))

        spin_up_path = (bundle_root / "assets" / "icons" / "spin_up.svg").as_posix()
        spin_down_path = (bundle_root / "assets" / "icons" / "spin_down.svg").as_posix()
        
        style_sheet = (
            f"""
            QMainWindow {{ background: {colors['window']}; color: {colors['text']}; }}
            QDialog, QMessageBox {{ background: {colors['window']}; color: {colors['text']}; }}
            QMenuBar {{ background: {colors['panel']}; color: {colors['text']}; border-bottom: 1px solid {colors['border']}; }}
            QMenuBar::item:selected {{ background: {colors['hover']}; border-radius: 5px; }}
            QMenu {{ background: {colors['panel']}; color: {colors['text']}; border: 1px solid {colors['border']}; padding: 5px; }}
            QMenu::item {{ padding: 7px 30px 7px 24px; border-radius: 5px; }}
            QMenu::item:selected {{ background: #1685D1; color: #FFFFFF; }}
            QMenu::item:disabled {{ color: {colors['muted']}; }}
            QMenu::separator {{ height: 1px; background: {colors['border']}; margin: 4px 8px; }}
            QStatusBar {{ background: {colors['panel']}; color: {colors['muted']}; border-top: 1px solid {colors['border']}; }}
            QLabel#activityProgressLabel {{ color: {colors['text']}; font-weight: 600; }}
            QProgressBar#activityProgressBar {{ background: {colors['field']}; color: {colors['text']}; border: 1px solid {colors['border']}; border-radius: 7px; text-align: center; font-size: 10px; }}
            QProgressBar#activityProgressBar::chunk {{ background: #1685D1; border-radius: 6px; }}
            QDialog#startupDialog {{ background: {colors['window']}; color: {colors['text']}; }}
            QDialog#startupDialog QFrame#card {{ background: {colors['panel']}; border: 1px solid {colors['border']}; border-radius: 12px; }}
            #startupTitle {{ color: {colors['text']}; }}
            #recentProjectList {{ background: {colors['field']}; border: 1px solid {colors['border']}; border-radius: 8px; padding: 5px; }}
            #recentProjectList::item {{ padding: 5px; margin: 2px; border-radius: 6px; }}
            #recentProjectThumbnail, #thumbnailPreview {{ background: {colors['alternate']}; color: {colors['muted']}; border: 1px solid {colors['border']}; border-radius: 7px; font-weight: 700; }}
            #leftProjectTabs::pane {{ border: 0; background: {colors['panel']}; }}
            #leftProjectTabs QTabBar::tab {{ background: {colors['button']}; color: {colors['muted']}; padding: 7px 10px; border: 1px solid {colors['border']}; }}
            #leftProjectTabs QTabBar::tab:selected {{ background: {colors['panel']}; color: {colors['text']}; border-bottom-color: {colors['panel']}; }}
            #sourceCategoryTabs {{ qproperty-drawBase: 0; }}
            #sourceCategoryTabs QTabBar::tab {{ background: {colors['button']}; color: {colors['muted']}; border: 1px solid {colors['border']}; border-radius: 7px; padding: 5px 10px; margin-right: 3px; }}
            #sourceCategoryTabs QTabBar::tab:selected {{ background: #1685D1; color: #FFFFFF; border-color: #1685D1; font-weight: 700; }}
            #sourceCategoryTabs QTabBar::tab:hover:!selected {{ background: {colors['hover']}; color: {colors['text']}; }}
            QToolBar {{ background: {colors['panel']}; border: 0; border-bottom: 1px solid {colors['border']}; spacing: 6px; padding: 7px 10px; }}
            QToolButton, QPushButton {{ background: {colors['button']}; color: {colors['text']}; border: 1px solid {colors['border']}; border-radius: 8px; padding: 7px 10px; }}
            QToolButton[contentViewButton="true"] {{ padding: 4px 6px; border-radius: 6px; }}
            QToolButton:hover, QPushButton:hover {{ background: {colors['hover']}; border-color: #55B8FF; }}
            QToolTip {{ background: {colors['panel']}; color: {colors['text']}; border: 1px solid #55B8FF; border-radius: 7px; padding: 8px; }}
            QToolButton:checked {{ background: #1685D1; color: #FFFFFF; }}
            QToolButton:disabled {{ color: {colors['muted']}; background: {colors['disabled']}; }}
            QPushButton:disabled {{ color: {colors['muted']}; background: {colors['disabled']}; border-color: {colors['border']}; }}
            QToolButton#exportButton {{ background: #1685D1; color: #FFFFFF; border-color: #1685D1; font-weight: 700; padding-left: 14px; padding-right: 14px; }}
            QToolButton#exportButton:hover {{ background: #0D72B8; border-color: #0D72B8; }}
            QToolButton#exportButton:disabled {{ background: {colors['disabled']}; border-color: {colors['border']}; color: {colors['muted']}; }}
            #dialogTitle {{ color: {colors['text']}; font-size: 21px; font-weight: 750; padding: 0; }}
            #settingsStatusCard {{ background: {colors['field']}; border: 1px solid {colors['border']}; border-radius: 10px; }}
            #aboutHeader, #aboutDetailsCard {{ background: {colors['field']}; border: 1px solid {colors['border']}; border-radius: 11px; }}
            #aboutProductName {{ color: {colors['text']}; font-size: 22px; font-weight: 750; padding: 0; }}
            #aboutVersion {{ color: #1685D1; font-size: 13px; font-weight: 700; padding: 0; }}
            QPushButton#primaryButton {{ background: #1685D1; color: #FFFFFF; border-color: #1685D1; font-weight: 700; }}
            QPushButton#primaryButton:hover {{ background: #0D72B8; border-color: #0D72B8; }}
            #settingsTabs::pane {{ background: {colors['panel']}; border: 1px solid {colors['border']}; border-radius: 9px; top: -1px; }}
            #settingsTabs QTabBar::tab {{ background: {colors['button']}; color: {colors['muted']}; border: 1px solid {colors['border']}; padding: 9px 22px; margin-right: 3px; }}
            #settingsTabs QTabBar::tab:selected {{ background: {colors['panel']}; color: {colors['text']}; border-bottom-color: {colors['panel']}; font-weight: 700; }}
            #projectStatusChip {{ color: {colors['muted']}; background: {colors['field']}; border: 1px solid {colors['border']}; border-radius: 8px; padding: 6px 10px; margin-right: 6px; font-size: 12px; }}
            #sidePanel, #layerPanel {{ background: {colors['panel']}; border: 1px solid {colors['border']}; border-radius: 10px; }}
            #playlistStrip, #timelineStrip {{ background: {colors['panel']}; border: 1px solid {colors['border']}; border-radius: 10px; }}
            #bottomWorkspaceTabs::pane {{ background: {colors['panel']}; border: 1px solid {colors['border']}; border-radius: 10px; top: -1px; }}
            #bottomWorkspaceTabs QTabBar::tab {{ background: {colors['button']}; color: {colors['muted']}; border: 1px solid {colors['border']}; padding: 8px 20px; margin-right: 3px; }}
            #bottomWorkspaceTabs QTabBar::tab:selected {{ background: {colors['panel']}; color: {colors['text']}; border-bottom-color: {colors['panel']}; font-weight: 700; }}
            #timelineStrip {{ border-radius: 0; border-left: 0; border-right: 0; border-bottom: 0; }}
            #timelineTabs::pane {{ border: 1px solid {colors['border']}; border-radius: 8px; top: -1px; background: {colors['field']}; }}
            #timelineTabs QTabBar::tab {{ background: transparent; color: {colors['muted']}; border: 0; padding: 7px 16px; margin-right: 4px; }}
            #timelineTabs QTabBar::tab:selected {{ color: {colors['text']}; border-bottom: 2px solid #1685D1; font-weight: 700; }}
            #timelineTrackTable, #timelineSourceTable {{ background: {colors['field']}; border: 0; alternate-background-color: {colors['alternate']}; selection-background-color: #1685D1; }}
            #timelineTrackTable::item, #timelineSourceTable::item {{ padding: 5px 8px; border: 0; }}
            #timelineTrackTable::item:selected, #timelineSourceTable::item:selected {{ background: #1685D1; color: #FFFFFF; }}
            #timelineMoveButton {{ min-width: 28px; max-width: 28px; min-height: 28px; padding: 0; font-size: 15px; font-weight: 700; }}
            #panelTitle {{ color: {colors['text']}; font-size: 15px; font-weight: 700; }}
            #mutedLabel {{ color: {colors['muted']}; font-size: 12px; }}
            #sourceSearch {{ padding-left: 7px; min-height: 28px; border-radius: 8px; }}
            #sourceResultCount {{ color: {colors['muted']}; background: {colors['alternate']}; border: 1px solid {colors['border']}; border-radius: 9px; padding: 2px 7px; font-size: 10px; font-weight: 650; }}
            #sourceCategoryTitle {{ color: {colors['muted']}; font-size: 10px; font-weight: 750; padding: 5px 3px 2px 3px; }}
            #sourceTemplateGroup {{ background: transparent; border: 0; }}
            QPushButton#sourceTemplateButton {{ background: {colors['field']}; border: 1px solid {colors['border']}; border-radius: 10px; padding: 0; text-align: left; }}
            QPushButton#sourceTemplateButton:hover {{ background: {colors['hover']}; border-color: #55B8FF; }}
            QPushButton#sourceTemplateButton:pressed {{ background: #1685D1; border-color: #1685D1; }}
            QPushButton#sourceTemplateButton:pressed QLabel {{ color: #FFFFFF; }}
            QPushButton#sourceTemplateButton[variant="true"] {{ background: {colors['alternate']}; border-radius: 8px; }}
            #sourceTemplateTitle {{ color: {colors['text']}; font-size: 12px; font-weight: 700; padding: 0; background: transparent; }}
            #sourceTemplateDescription {{ color: {colors['muted']}; font-size: 10px; padding: 0; background: transparent; }}
            #sourceTemplateIcon {{ background: {colors['alternate']}; border: 1px solid {colors['border']}; border-radius: 7px; padding: 2px; }}
            QPushButton#sourceTemplateButton:hover #sourceTemplateIcon {{ border-color: #55B8FF; }}
            #sourceVariantToggle {{ min-width: 28px; max-width: 28px; min-height: 42px; padding: 0; border-radius: 8px; font-size: 14px; }}
            #sourceVariantContainer {{ border-left: 2px solid {colors['border']}; }}
            QScrollArea, QListWidget, QTreeWidget, QTableWidget {{ background: {colors['panel']}; color: {colors['text']}; border: 0; }}
            #sourceInspector, #sourceInspector::viewport, #inspectorContent {{ background: {colors['panel']}; color: {colors['text']}; }}
            #inspectorPropertyTabs::pane {{ background: {colors['panel']}; border: 1px solid {colors['border']}; border-radius: 9px; top: -1px; }}
            #inspectorPropertyTabs QTabBar::tab {{ background: {colors['button']}; color: {colors['muted']}; border: 1px solid {colors['border']}; padding: 7px 11px; margin-right: 2px; }}
            #inspectorPropertyTabs QTabBar::tab:selected {{ background: {colors['panel']}; color: {colors['text']}; border-bottom-color: {colors['panel']}; font-weight: 700; }}
            #inspectorPropertyTabs QTabBar::tab:hover:!selected {{ background: {colors['hover']}; color: {colors['text']}; }}
            #inspectorSectionHeader {{ background: {colors['button']}; color: {colors['text']}; border: 1px solid {colors['border']}; border-radius: 7px; padding: 6px 10px; text-align: left; font-weight: 700; font-size: 11px; }}
            #inspectorSectionHeader:hover {{ background: {colors['hover']}; }}
            #inspectorSectionHeader:checked {{ border-bottom-left-radius: 0; border-bottom-right-radius: 0; }}
            #inspectorSectionBody {{ background: {colors['field']}; border: 1px solid {colors['border']}; border-top: 0; border-bottom-left-radius: 7px; border-bottom-right-radius: 7px; }}
            #inspectorEmptyState {{ color: {colors['muted']}; font-size: 14px; background: {colors['panel']}; }}
            QHeaderView::section {{ background: {colors['alternate']}; color: {colors['text']}; border: 0; border-bottom: 1px solid {colors['border']}; padding: 5px; }}
            QTreeWidget::item:selected, QListWidget::item:selected {{ background: #1685D1; color: #FFFFFF; border-radius: 5px; }}
            #playlistList[dropActive="true"] {{ border: 1px solid #1685D1; border-radius: 8px; }}
            #trackRow {{ background: {colors['field']}; border: 1px solid {colors['border']}; border-radius: 8px; }}
            #trackRow:hover {{ background: {colors['hover']}; }}
            #trackRow[trackDisabled="true"] {{ background: {colors['panel']}; border: 1px dashed {colors['border']}; }}
            #trackRow[dropTarget="true"] {{ background: {colors['hover']}; border: 2px solid #1685D1; }}
            QGroupBox {{ color: {colors['text']}; font-weight: 600; border: 1px solid {colors['border']}; border-radius: 8px; margin-top: 10px; padding: 10px 7px 7px 7px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; }}
            QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {{ background: {colors['field']}; color: {colors['text']}; border: 1px solid {colors['border']}; border-radius: 6px; padding: 5px; min-height: 18px; selection-background-color: #1685D1; selection-color: #FFFFFF; }}
            QLineEdit:focus, QComboBox:focus {{ border: 2px solid #1685D1; padding: 4px; }}
            QSpinBox, QDoubleSpinBox {{ background: {colors['field']}; color: {colors['text']}; border: 1px solid {colors['border']}; border-radius: 6px; padding: 0px 28px 0px 5px; min-height: 30px; max-height: 30px; selection-background-color: #1685D1; selection-color: #FFFFFF; }}
            QSpinBox:focus, QDoubleSpinBox:focus {{ border: 2px solid #1685D1; }}
            QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; width: 24px; height: 15px; margin: 0px; background: {colors['button']}; border-left: 1px solid {colors['border']}; border-bottom: 1px solid {colors['border']}; border-top-right-radius: 5px; }}
            QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; width: 24px; height: 15px; margin: 0px; background: {colors['button']}; border-left: 1px solid {colors['border']}; border-bottom-right-radius: 5px; }}
            QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url("{spin_up_path}"); width: 9px; height: 6px; }}
            QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url("{spin_down_path}"); width: 9px; height: 6px; }}
            QSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{ background: {colors['hover']}; }}
            QSpinBox::up-button:pressed, QSpinBox::down-button:pressed, QDoubleSpinBox::up-button:pressed, QDoubleSpinBox::down-button:pressed {{ background: #1685D1; }}
            QCheckBox, QLabel {{ color: {colors['text']}; padding: 3px; }}
            QCheckBox:disabled, QLabel:disabled {{ color: {colors['muted']}; }}
            QComboBox QAbstractItemView {{ background: {colors['panel']}; color: {colors['text']}; border: 1px solid {colors['border']}; selection-background-color: #1685D1; selection-color: #FFFFFF; outline: 0; }}
            QScrollBar:vertical {{ background: transparent; width: 10px; margin: 3px; }}
            QScrollBar::handle:vertical {{ background: {colors['border']}; min-height: 28px; border-radius: 5px; }}
            QScrollBar::handle:vertical:hover {{ background: {colors['muted']}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 3px; }}
            QScrollBar::handle:horizontal {{ background: {colors['border']}; min-width: 28px; border-radius: 5px; }}
            QScrollBar::handle:horizontal:hover {{ background: {colors['muted']}; }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
            QSplitter::handle {{ background: {colors['border']}; width: 2px; height: 5px; }}
            QSplitter::handle:hover {{ background: #1685D1; }}
            """
        )
        self.setStyleSheet("")
        if application is not None:
            style_changed = application.styleSheet() != style_sheet
            if style_changed:
                application.setStyleSheet(style_sheet)
        else:
            style_changed = True
            self.setStyleSheet(style_sheet)
        for panel in (self.source_sidebar, self.layer_panel, self.playlist_editor):
            effect = QGraphicsDropShadowEffect(panel)
            effect.setBlurRadius(18)
            effect.setOffset(0, 4)
            shadow_color = QColor(colors["shadow"])
            shadow_color.setAlpha(85)
            effect.setColor(shadow_color)
            panel.setGraphicsEffect(effect)
        if dark:
            self.canvas.set_theme_colors(
                QColor("#171B22"), QColor("#202733"), QColor(255, 255, 255, 18),
                QColor("#5F6B7A"),
            )
        else:
            self.canvas.set_theme_colors(
                QColor("#E6EBF1"), QColor("#FFFFFF"), QColor(72, 91, 112, 30),
                QColor("#9AA9BA"),
            )
        if application is not None and (palette_changed or style_changed):
            for widget in application.topLevelWidgets():
                widget.setPalette(application.palette())
                refresh_theme = getattr(widget, "refresh_theme", None)
                if callable(refresh_theme):
                    refresh_theme()
                widget.style().unpolish(widget)
                widget.style().polish(widget)
                widget.update()
