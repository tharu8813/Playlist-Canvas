"""Primary application window and Phase 1A workspace composition."""

from __future__ import annotations

from dataclasses import replace
import json
import logging
from pathlib import Path
import shutil
import threading
from tempfile import TemporaryDirectory

from PySide6.QtCore import QByteArray, QMimeData, QSettings, QStandardPaths, Qt, QTimer
from PySide6.QtGui import (QAction, QActionGroup, QColor, QCloseEvent, QDragEnterEvent,
                           QDropEvent, QFontDatabase, QIcon, QImage, QImageWriter,
                           QKeySequence, QPainter, QPen, QPixmap)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QFileDialog,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyle,
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
from app.dialogs.ffmpeg_install_progress_dialog import FFmpegInstallProgressDialog
from app.dialogs.export_preview_dialog import ExportPreviewDialog
from app.dialogs.export_settings_dialog import ExportSettingsDialog
from app.dialogs.missing_media_dialog import MissingMediaDialog
from app.dialogs.new_project_dialog import NewProjectDialog
from app.dialogs.playlist_export_dialog import PlaylistExportDialog
from app.dialogs.preset_dialog import DesignPresetDialog
from app.dialogs.ai_project_builder_dialog import AIProjectBuilderDialog
from app.dialogs.project_settings_dialog import ProjectSettingsDialog
from app.dialogs.settings_dialog import SettingsDialog
from app.dialogs.startup_dialog import StartupDialog
from app.dialogs.track_details_dialog import TrackDetailsDialog
from app.dialogs.shortcuts_dialog import ShortcutsDialog
from app.dialogs.about_dialog import AboutDialog
from app.dialogs.help_dialog import HelpDialog
from app.ffmpeg.install_worker import FFmpegInstallWorker
from app.ffmpeg.managed_installer import ManagedFFmpegInstallation, ManagedFFmpegInstaller
from app.inspector.source_inspector import SourceInspector
from app.layers.layer_panel import LayerPanel
from app.models.playlist import PlaylistTrack
from app.models.source import Source, SourceType
from app.models.project import CanvasSettings, ProjectDocument, ProjectSettings
from app.services.project_service import ProjectError, ProjectService
from app.services.project_media_service import ProjectMediaService
from app.services.project_content_service import ProjectContentService
from app.services.recent_projects_service import RecentProjectsService
from app.services.autosave_service import AutosaveService
from app.services.history_service import HistoryService
from app.services.lyrics_service import LyricsError, LyricsService
from app.services.theme_service import Theme, ThemeService
from app.services.source_store import SourceStore
from app.services.playlist_service import AUDIO_EXTENSIONS, PlaylistService
from app.services.playlist_export_service import PlaylistExportError, PlaylistExportService
from app.services.app_settings_service import AppSettingsService
from app.services.smooth_scroll_service import SmoothScrollService
from app.presets.preset_service import PresetDefinition
from app.preview.canvas_snapshot import CanvasSnapshot
from app.renderer.ffmpeg_renderer import (
    FFmpegNotFoundError,
    FFmpegRenderer,
    RenderCancelledError,
    RenderError,
    RenderFrame,
    RenderResult,
    StaticOverlayLayer,
    VisualizerOverlay,
)
from app.renderer.render_worker import RenderWorker
from app.timeline.timeline_panel import TimelinePanel
from app.utils.i18n import Language, Translator
from app.utils.image_loader import load_pixmap
from app.utils.logging_setup import log_directory, report_unexpected_error
from app.widgets.playlist_editor import PlaylistEditor
from app.widgets.content_library_panel import ContentLibraryPanel


LOGGER = logging.getLogger(__name__)

SOURCE_CLIPBOARD_MIME = "application/x-playlist-video-studio-sources+json"


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
        self.recent_projects = RecentProjectsService(self)
        self.translator = Translator(self)
        self.theme_service = ThemeService(self)
        self.motion = MotionController(self)
        self.animation_preview_controller = CanvasAnimationPreviewController(self)
        self.animation_preview_controller.finished.connect(
            self._finish_canvas_animation_preview
        )
        self._animation_preview_active = False
        self.history = HistoryService(self)
        recovery_directory = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppLocalDataLocation
        )
        self.autosave = AutosaveService(Path(recovery_directory or Path.cwd() / ".app-data"))
        self._history_ready = False
        self._history_restoring = False
        self._history_applying = False
        self._project_dirty = False
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
        self._sidebar_open_width = 220
        self._sidebar_transition = False
        self._render_worker: RenderWorker | None = None
        self._export_frame_staging: TemporaryDirectory[str] | None = None
        self._export_frame_index = 0
        self._export_capture_count = 0
        self._export_frame_cache: dict[str, tuple[QImage, Path]] = {}
        self._export_dialog: ExportProgressDialog | None = None
        self._export_ui_lock_state: tuple[bool, bool, bool, bool, bool] | None = None
        self._clipboard_paste_serial = 0
        self._ffmpeg_install_worker: FFmpegInstallWorker | None = None
        self._ffmpeg_install_dialog: FFmpegInstallProgressDialog | None = None
        self._settings_dialog: SettingsDialog | None = None
        self._source_buttons: dict[SourceType, QPushButton] = {}
        self.setAcceptDrops(True)
        self.resize(1560, 920)
        self.setMinimumSize(1100, 680)
        self._build_toolbar()
        self._build_workspace()
        self._build_canvas_edit_actions()
        self._build_menu_bar()
        self._apply_style()
        self._add_welcome_sources()
        self.translator.language_changed.connect(self.retranslate)
        self.theme_service.theme_changed.connect(self._on_theme_changed)
        self.retranslate()
        self._connect_history()
        self._autosave_timer.start()
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
        self.new_action.triggered.connect(self._new_project)
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
        self.delete_action.setShortcut(QKeySequence.StandardKey.Delete)
        self.delete_action.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon)
        )
        self.delete_action.triggered.connect(self._delete_contextual_selection)
        toolbar.addAction(self.delete_action)
        toolbar.addSeparator()
        language_menu = QMenu(self)
        self.language_menu = language_menu
        group = QActionGroup(self)
        group.setExclusive(True)
        self.language_actions: dict[Language, QAction] = {}
        for language, label in ((Language.KOREAN, "한국어"), (Language.ENGLISH, "English")):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(language is self.translator.language)
            action.triggered.connect(lambda checked=False, value=language: self.translator.set_language(value))
            group.addAction(action)
            language_menu.addAction(action)
            self.language_actions[language] = action
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
        self.panels_action.toggled.connect(self._set_sidebar_visible)
        self.panels_action.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarMenuButton)
        )
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
        self.preview_action.triggered.connect(self._open_playlist_preview)
        toolbar.addAction(self.preview_action)
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
        toolbar.addAction(self.preview_action)
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
        register("Alt+Left", lambda: self._nudge_selected_sources(-1, 0))
        register("Alt+Right", lambda: self._nudge_selected_sources(1, 0))
        register("Alt+Up", lambda: self._nudge_selected_sources(0, -1))
        register("Alt+Down", lambda: self._nudge_selected_sources(0, 1))
        register("Alt+Shift+Left", lambda: self._nudge_selected_sources(-10, 0))
        register("Alt+Shift+Right", lambda: self._nudge_selected_sources(10, 0))
        register("Alt+Shift+Up", lambda: self._nudge_selected_sources(0, -10))
        register("Alt+Shift+Down", lambda: self._nudge_selected_sources(0, 10))
        register("Shift+Left", lambda: self._jump_selected_to_grid(-1, 0))
        register("Shift+Right", lambda: self._jump_selected_to_grid(1, 0))
        register("Shift+Up", lambda: self._jump_selected_to_grid(0, -1))
        register("Shift+Down", lambda: self._jump_selected_to_grid(0, 1))
        register("Ctrl+Shift+H", lambda: self._center_selected_sources(horizontal=True))
        register("Ctrl+Shift+V", lambda: self._center_selected_sources(horizontal=False))
        register("Ctrl+]", lambda: self._move_selected_to_edge(front=True))
        register("Ctrl+[", lambda: self._move_selected_to_edge(front=False))
        register("Ctrl+G", self._group_selected_sources)
        register("Ctrl+Shift+G", self._ungroup_selected_sources)
        register("Ctrl+L", self._toggle_selected_lock)
        register("Ctrl+0", self.canvas_fit)
        register("Ctrl+=", lambda: self._adjust_canvas_zoom(1.15))
        register("Ctrl+-", lambda: self._adjust_canvas_zoom(1.0 / 1.15))
        register("Home", self.canvas_fit)
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
        """Apply a bounded keyboard zoom around the current Canvas view."""
        self.canvas.set_zoom(self.canvas.transform().m11() * factor)

    def _nudge_selected_sources(self, x_delta: float, y_delta: float) -> None:
        """Move selected sources by an exact keyboard increment."""
        for source in self._selected_editable_sources():
            self.store.update(source.id, x=source.x + x_delta, y=source.y + y_delta)

    def _jump_selected_to_grid(self, horizontal_direction: int, vertical_direction: int) -> None:
        """Jump selected sources to the next 10px grid path in the requested direction."""
        grid = 10.0
        for source in self._selected_editable_sources():
            if horizontal_direction:
                value = source.x / grid
                target = (int(value // 1) + 1) * grid if horizontal_direction > 0 else (
                    int(-(-value // 1)) - 1
                ) * grid
                self.store.update(source.id, x=target)
            if vertical_direction:
                value = source.y / grid
                target = (int(value // 1) + 1) * grid if vertical_direction > 0 else (
                    int(-(-value // 1)) - 1
                ) * grid
                self.store.update(source.id, y=target)

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
            "group": self._group_selected_sources,
            "ungroup": self._ungroup_selected_sources,
            "toggle_visible": self._toggle_selected_visibility,
            "toggle_lock": self._toggle_selected_lock,
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
            ("basic", (SourceType.IMAGE, SourceType.TEXT, SourceType.SHAPE)),
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
        self.about_action = QAction(self)
        self.about_action.setMenuRole(QAction.MenuRole.AboutRole)
        self.about_action.triggered.connect(self._show_about)
        self.help_menu.addAction(self.about_action)
        self._sync_canvas_shortcut_actions(None, QApplication.focusWidget())

    def _build_workspace(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        top_splitter = QSplitter(Qt.Orientation.Horizontal)
        top_splitter.setChildrenCollapsible(False)
        self.main_splitter = top_splitter
        left_splitter = QSplitter(Qt.Orientation.Vertical)
        self.left_workspace = left_splitter
        left_splitter.setChildrenCollapsible(False)
        self.source_sidebar = self._make_source_sidebar()
        self.content_library_panel = ContentLibraryPanel(
            self.project_content_service, self.translator
        )
        self.content_library_panel.add_requested.connect(self._add_library_content)
        self.left_tabs = QTabWidget()
        self.left_tabs.setObjectName("leftProjectTabs")
        self.left_tabs.addTab(self.source_sidebar, "")
        self.left_tabs.addTab(self.content_library_panel, "")
        left_splitter.addWidget(self.left_tabs)
        self.layer_panel = LayerPanel(self.store, self.translator)
        left_splitter.addWidget(self.layer_panel)
        left_splitter.setSizes([345, 420])
        top_splitter.addWidget(left_splitter)
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(10, 10, 10, 8)
        self.canvas = LiveCanvas(self.store, self.translator)
        self.canvas.files_dropped.connect(self._handle_dropped_files)
        self.canvas.cut_requested.connect(self._cut_selected_sources)
        self.canvas.copy_requested.connect(self._copy_selected_sources)
        self.canvas.paste_requested.connect(self._paste_sources)
        self.canvas.command_requested.connect(self._handle_canvas_context_command)
        center_layout.addWidget(self.canvas, 1)
        top_splitter.addWidget(center)
        self.inspector = SourceInspector(self.store, self.translator)
        self.inspector.animation_preview_requested.connect(
            self._preview_source_animation
        )
        top_splitter.addWidget(self.inspector)
        top_splitter.setSizes([220, 1030, 300])
        self.workspace_splitter = QSplitter(Qt.Orientation.Vertical)
        self.workspace_splitter.setObjectName("workspaceSplitter")
        self.workspace_splitter.setChildrenCollapsible(False)
        self.workspace_splitter.addWidget(top_splitter)
        self.bottom_tabs = QTabWidget()
        self.bottom_tabs.setObjectName("bottomWorkspaceTabs")
        self.bottom_tabs.setDocumentMode(True)
        self.playlist_editor = PlaylistEditor(self.playlist_service, self.translator)
        self.playlist_editor.request_files.connect(self._choose_audio_files)
        self.playlist_editor.files_dropped.connect(self._handle_dropped_files)
        self.playlist_editor.track_double_clicked.connect(self._show_track_details)
        self.bottom_tabs.addTab(self.playlist_editor, "")
        self.timeline_panel = TimelinePanel(
            self.playlist_service, self.store, self.translator
        )
        self.bottom_tabs.addTab(self.timeline_panel, "")
        self.workspace_splitter.addWidget(self.bottom_tabs)
        self.workspace_splitter.setStretchFactor(0, 1)
        self.workspace_splitter.setStretchFactor(1, 0)
        saved_sizes = QSettings().value("workspace/vertical_splitter", [650, 270])
        try:
            sizes = [max(120, int(value)) for value in saved_sizes]
        except (TypeError, ValueError):
            sizes = [650, 270]
        self.workspace_splitter.setSizes(sizes if len(sizes) == 2 else [650, 270])
        try:
            saved_tab = int(QSettings().value("workspace/bottom_tab", 0))
        except (TypeError, ValueError):
            saved_tab = 0
        self.bottom_tabs.setCurrentIndex(max(0, min(1, saved_tab)))
        self.bottom_tabs.currentChanged.connect(
            lambda index: QSettings().setValue("workspace/bottom_tab", index)
        )
        self._workspace_settings_timer = QTimer(self)
        self._workspace_settings_timer.setSingleShot(True)
        self._workspace_settings_timer.setInterval(250)
        self._workspace_settings_timer.timeout.connect(self._save_workspace_layout)
        self.workspace_splitter.splitterMoved.connect(
            lambda _position, _index: self._workspace_settings_timer.start()
        )
        root_layout.addWidget(self.workspace_splitter, 1)
        self.setCentralWidget(root)

    def _save_workspace_layout(self) -> None:
        """Persist splitter geometry after resizing settles."""
        QSettings().setValue(
            "workspace/vertical_splitter", self.workspace_splitter.sizes()
        )

    def _make_source_sidebar(self) -> QWidget:
        """Build the source palette with a scrollable source-card area."""
        panel = QFrame()
        panel.setObjectName("sidePanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 16, 14, 14)
        layout.setSpacing(8)
        self.sidebar_title = QLabel()
        self.sidebar_title.setObjectName("panelTitle")
        layout.addWidget(self.sidebar_title)

        self.source_search = QLineEdit()
        self.source_search.setObjectName("sourceSearch")
        self.source_search.setClearButtonEnabled(True)
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
        cards_layout.setSpacing(8)
        descriptions = [
            ("image", SourceType.IMAGE), ("text", SourceType.TEXT), ("shape", SourceType.SHAPE),
            ("progress_bar", SourceType.PROGRESS_BAR), ("album_cover", SourceType.ALBUM_COVER),
            ("time", SourceType.TIME), ("logo", SourceType.LOGO), ("watermark", SourceType.WATERMARK),
            ("background", SourceType.BACKGROUND), ("audio_visualizer", SourceType.AUDIO_VISUALIZER),
            ("lyrics", SourceType.LYRICS),
            ("track_list", SourceType.TRACK_LIST), ("now_playing", SourceType.NOW_PLAYING),
            ("audio_waveform", SourceType.AUDIO_WAVEFORM),
            ("audio_level_meter", SourceType.AUDIO_LEVEL_METER),
            ("particle_overlay", SourceType.PARTICLE_OVERLAY),
        ]
        self._source_search_terms: dict[SourceType, str] = {}
        for _key, source_type in descriptions:
            button = QPushButton()
            button.clicked.connect(lambda checked=False, kind=source_type: self._add_source(kind))
            self._source_buttons[source_type] = button
            self._source_search_terms[source_type] = f"{_key} {source_type.value}".lower()
            cards_layout.addWidget(button)
        cards_layout.addStretch(1)
        self.source_cards_scroll.setWidget(cards_widget)
        layout.addWidget(self.source_cards_scroll, 1)
        return panel

    def _filter_source_cards(self, query: str) -> None:
        """Show only palette sources matching a user-facing name or source type."""
        normalized = query.strip().lower()
        for source_type, button in self._source_buttons.items():
            searchable = f"{self._source_search_terms.get(source_type, '')} {button.text()}".lower()
            button.setVisible(not normalized or normalized in searchable)

    def _source_hover_help(self, source_type: SourceType) -> tuple[str, str]:
        """Return a concise purpose and Inspector-setting summary for a source."""
        korean = self.translator.language is Language.KOREAN
        korean_help = {
            SourceType.IMAGE: (
                "사진이나 그래픽 파일을 캔버스에 표시합니다.",
                "이미지 파일 · 맞춤 방식 · 밝기 · 대비 · 흐림 · 그림자",
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
            SourceType.TEXT: ("Display a title, description, or dynamic track information.", "Text · font · size · alignment · wrapping · color · outline"),
            SourceType.SHAPE: ("Add a shape or color surface to the design.", "Shape · fill · gradient · outline · corner radius"),
            SourceType.PROGRESS_BAR: ("Show current-track or playlist progress.", "Progress mode · style · value · track color · fill color"),
            SourceType.ALBUM_COVER: ("Automatically show the current track's album artwork.", "Image fit · frame style · brightness · contrast · shadow"),
            SourceType.TIME: ("Dynamically show clock or playback time.", "Time format · font · size · alignment · color · outline"),
            SourceType.LOGO: ("Place a brand or channel logo image.", "Logo file · image fit · opacity · brightness · contrast · shadow"),
            SourceType.WATERMARK: ("Display a watermark image over the video.", "Image file · fit mode · opacity · position · size · shadow"),
            SourceType.BACKGROUND: ("Create a full-Canvas color, image, or album-art background.", "Background mode · image fit · ambient effect · brightness · contrast · blur"),
            SourceType.AUDIO_VISUALIZER: ("Show a visual effect that reacts to music frequencies.", "Style · bars · line width · sensitivity · reactivity · attack · release · smoothing"),
            SourceType.LYRICS: ("Show lyrics or subtitles synchronized to playback.", "Subtitle style · transition · context lines · spacing · previous-line effect · timing offset"),
            SourceType.TRACK_LIST: ("Show playlist entries around the current track.", "Track count · list style · window · metadata · spacing · highlight colors"),
            SourceType.NOW_PLAYING: ("Show current-track information as a card.", "Card style · duration · exit effect · font · alignment · colors"),
            SourceType.AUDIO_WAVEFORM: ("Show the audio waveform together with playback progress.", "Waveform style · fill color · size · opacity · animation"),
            SourceType.AUDIO_LEVEL_METER: ("Show audio levels and peaks as a live meter.", "Mode · style · direction · sensitivity · attack · release · segments · peak · colors"),
            SourceType.PARTICLE_OVERLAY: ("Add moving particles or noise over the Canvas.", "Style · density · speed · size · direction · twinkle · glow · colors · seed"),
        }
        return (korean_help if korean else english_help)[source_type]

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
            "클릭하면 캔버스에 추가됩니다."
            if korean else "Click to add it to the Canvas."
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
                              fill_color="#263042", locked=True, z_index=-10, text=""))
        self.store.add(Source(SourceType.TEXT, "Playlist title", x=margin_x, y=title_y,
                              width=title_width,
                              height=100, fill_color="#7C3AED", border_radius=16,
                              text="Late Night Playlist", z_index=1))
        self.store.add(Source(SourceType.PROGRESS_BAR, "Progress", x=margin_x,
                              y=progress_y, width=progress_width,
                              height=14, fill_color="#27D17F", border_radius=7, z_index=2))

    def _add_source(self, source_type: SourceType) -> None:
        count = len(self.store.sources())
        name = source_type.value.replace("_", " ").title()
        dimensions = (260.0, 90.0)
        if source_type in {SourceType.ALBUM_COVER, SourceType.LOGO}:
            dimensions = (180.0, 180.0)
        if source_type in {SourceType.AUDIO_VISUALIZER, SourceType.AUDIO_WAVEFORM}:
            dimensions = (460.0, 100.0)
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
        if source_type is SourceType.TIME:
            default_text = "%current_time% / %total_time%"
        elif source_type is SourceType.LYRICS:
            default_text = "Lyrics are not available for this track."
        elif source_type is SourceType.TRACK_LIST:
            default_text = "▶ 01. Current track\n  02. Next track"
        elif source_type is SourceType.NOW_PLAYING:
            default_text = "NOW PLAYING\nTrack title\nArtist"
        source = Source(
            source_type=source_type,
            name=name,
            x=170 + (count % 4) * 35,
            y=190 + (count % 3) * 35,
            width=dimensions[0], height=dimensions[1],
            border_radius=12 if source_type is not SourceType.PROGRESS_BAR else 8,
            fill_color="#1685D1" if source_type is not SourceType.BACKGROUND else "#263042",
            text=default_text,
            z_index=count,
            locked=source_type is SourceType.BACKGROUND,
        )
        self.store.add(source)

    def _show_track_details(self, track_id: str) -> None:
        """Open track metadata and timed-lyrics editing for a playlist card."""
        track = next((entry for entry in self.playlist_service.tracks if entry.id == track_id), None)
        if track is None:
            return
        dialog = TrackDetailsDialog(track, self.translator, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.playlist_service.update_track(
                track_id,
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
            self.playlist_service.add_files(paths)
            self.project_content_service.add_paths(paths)

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
            self.playlist_service.add_files([content_path])
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
            try:
                cues = LyricsService.load(content_path)
            except LyricsError as error:
                QMessageBox.warning(self, "Lyrics", str(error))
                return
            self.playlist_service.update_track(
                target.id, lyrics_path=str(content_path.resolve()), lyrics=cues
            )
            if not any(source.source_type is SourceType.LYRICS for source in self.store.sources()):
                self._add_source(SourceType.LYRICS)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """Accept local file URLs dropped anywhere outside a specialized child widget."""
        if event.mimeData().hasUrls() and any(
            url.isLocalFile() for url in event.mimeData().urls()
        ):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event: QDragEnterEvent) -> None:
        """Keep the whole application window available as a drop target."""
        self.dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        """Route project, image, and audio file drops from the overall workspace."""
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if not paths:
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
        audio_paths = [path for path in paths if path.suffix.lower() in AUDIO_EXTENSIONS]
        image_count = self._add_dropped_images(image_paths, position)
        audio_count = self.playlist_service.add_files(audio_paths)
        self.project_content_service.add_paths([*image_paths, *audio_paths])
        if image_count or audio_count:
            korean = self.translator.language is Language.KOREAN
            message = (
                f"이미지 {image_count}개, 음악 {audio_count}개를 추가했습니다."
                if korean else f"Added {image_count} image(s) and {audio_count} music file(s)."
            )
            self.statusBar().showMessage(message, 6000)
            return
        korean = self.translator.language is Language.KOREAN
        self.statusBar().showMessage(
            "지원되는 이미지, 음악 또는 프로젝트 파일을 놓아 주세요."
            if korean else "Drop supported image, music, or project files.",
            5000,
        )

    def _add_dropped_images(self, paths: list[Path], position: object | None) -> int:
        """Create Canvas image sources at the drop point while preserving aspect ratio."""
        if not paths:
            return 0
        artboard = self.canvas.scene_model.artboard_rect
        point = position if hasattr(position, "x") and hasattr(position, "y") else artboard.center()
        count = 0
        last_source: Source | None = None
        for index, path in enumerate(paths):
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
        if last_source is not None:
            self.store.select(last_source.id)
        return count

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
        if response is QMessageBox.StandardButton.Yes:
            self._load_project_path(path)

    def _stage_export_frame(
        self, image: QImage, duration_seconds: float, stream_key: str = "base",
    ) -> RenderFrame:
        """Move a captured Canvas frame to temporary disk instead of retaining its pixels in RAM."""
        if self._export_frame_staging is None:
            raise RenderError("Export frame staging has not been initialized.")
        if image.isNull():
            raise RenderError("Could not stage an empty export frame on disk.")
        self._export_capture_count += 1
        previous = self._export_frame_cache.get(stream_key)
        if previous is not None and image == previous[0]:
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
        writer = QImageWriter(str(path), b"png")
        # Compression level 1 trades a little temporary disk space for much
        # faster preparation. FFmpeg output quality is unaffected.
        writer.setCompression(1)
        writer.setOptimizedWrite(False)
        if not writer.write(image):
            raise RenderError(
                f"Could not stage an export frame on disk: {writer.errorString()}"
            )
        self._export_frame_cache[stream_key] = (image.copy(), path)
        return RenderFrame(path, max(0.001, duration_seconds))

    @staticmethod
    def _export_animation_sample_rate(output_fps: int) -> int:
        """Cap preparation samples while FFmpeg still emits the requested output FPS."""
        return max(15, min(30, int(output_fps)))

    def _clear_export_frame_staging(self) -> None:
        """Release disk-backed captured frames after every export completion path."""
        if self._export_frame_staging is not None:
            self._export_frame_staging.cleanup()
            self._export_frame_staging = None
        self._export_frame_index = 0
        self._export_capture_count = 0
        self._export_frame_cache.clear()

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
                "FFmpeg를 찾을 수 없습니다. FFmpeg를 설치하고 시스템 PATH에 추가한 뒤 다시 시도하세요."
                if korean else "FFmpeg was not found. Install it and add it to your system PATH, then try again.",
            )
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
        )
        if export_options.exec() != export_options.DialogCode.Accepted:
            return
        selected_app_settings = export_options.app_settings
        output = str(export_options.output_path)
        if export_options.save_as_default:
            self.settings_service.save(selected_app_settings)
        active_tracks = [track for track in self.playlist_service.tracks if track.enabled]
        if not active_tracks:
            QMessageBox.warning(
                self,
                "내보내기 오류" if korean else "Export error",
                "내보낼 음악을 하나 이상 선택하세요."
                if korean else "Select at least one music track to export.",
            )
            return
        render_settings = selected_app_settings.render_settings()
        settings_summary = (
            f"{selected_app_settings.resolution_name} · {selected_app_settings.fps} FPS · "
            f"{selected_app_settings.video_codec} · CRF {selected_app_settings.crf} · "
            f"AAC {selected_app_settings.audio_bitrate}"
        )
        preparation_cancel = threading.Event()
        self._export_dialog = ExportProgressDialog(self)
        self._export_dialog.set_korean(korean)
        self._export_dialog.set_export_details(
            len(active_tracks), self._playlist_duration(active_tracks),
            settings_summary,
        )
        self._export_dialog.set_busy(
            "Preparing visual frames",
            "캔버스와 애니메이션 프레임을 준비하고 있습니다."
            if korean else "Capturing Canvas and animation frames.",
        )
        request_preparation_cancel = preparation_cancel.set
        self._export_dialog.cancel_requested.connect(request_preparation_cancel)
        self._export_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self._lock_main_form_for_export()
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
            self._clear_export_frame_staging()
            if self._export_dialog:
                self._export_dialog.complete(False)
                self._export_dialog = None
            self._unlock_main_form_after_export()
            report_unexpected_error("Starting export preparation", error)
            QMessageBox.critical(
                self, "내보내기 오류" if korean else "Export error", str(error)
            )
            return
        try:
            animation_fps = self._export_animation_sample_rate(render_settings.fps)
            playlist_duration = self._playlist_duration(active_tracks)
            visualizers = self._export_visualizers()
            dynamic_visualizer_ids = {source.id for source in self.store.sources()
                                      if source.source_type in {
                                          SourceType.AUDIO_VISUALIZER, SourceType.AUDIO_WAVEFORM,
                                          SourceType.AUDIO_LEVEL_METER, SourceType.PARTICLE_OVERLAY,
                                      } and source.visible}
            frames: list[RenderFrame] = []
            z_bands = CanvasSnapshot.z_bands(self.canvas.scene_model, dynamic_visualizer_ids)
            base_z_max = z_bands[0][1]
            static_band_frames: list[list[RenderFrame]] = [[] for _band in z_bands[1:]]

            def pump_preparation_ui(track_number: int) -> None:
                """Keep the preparation dialog responsive during PNG staging."""
                if self._export_capture_count % 4 != 0 or self._export_dialog is None:
                    return
                self._export_dialog.set_busy(
                    "Preparing visual frames",
                    (
                        f"{track_number}번 곡 화면 준비 중 · 임시 프레임 "
                        f"{self._export_frame_index:,}개"
                        if korean else
                        f"Preparing track {track_number} · "
                        f"{self._export_capture_count:,} capture(s) · "
                        f"{self._export_frame_index:,} temporary file(s)"
                    ),
                )
                QApplication.processEvents()
                if preparation_cancel.is_set():
                    raise RenderCancelledError("Export preparation was cancelled.")

            def capture_composed_frame(
                capture_track: PlaylistTrack, capture_number: int, capture_start: float,
                frame_duration: float, **state: object,
            ) -> RenderFrame:
                """Stage one lower Canvas frame plus every transparent foreground Z band."""
                if preparation_cancel.is_set():
                    raise RenderCancelledError("Export preparation was cancelled.")
                elapsed = float(state.pop("elapsed_seconds", 0.0))
                common = dict(
                    elapsed_seconds=elapsed,
                    hide_visualizers=dynamic_visualizer_ids,
                    playlist_duration_seconds=playlist_duration,
                    playlist_tracks=active_tracks,
                    timeline_seconds=float(
                        state.pop("timeline_seconds", capture_start + elapsed)
                    ),
                )
                phase = state.pop("animation_phase", None)
                progress = float(state.pop("animation_progress", 1.0))
                if phase is not None:
                    common["animation_phase"] = phase
                    common["animation_progress"] = progress
                    common["animation_phase_duration"] = float(
                        state.pop("animation_phase_duration", 0.0)
                    )
                base = self._stage_export_frame(CanvasSnapshot.capture_track(
                    self.canvas.scene_model, capture_track, capture_number, len(active_tracks), capture_start,
                    z_max=base_z_max, **common,
                ), frame_duration, "base")
                pump_preparation_ui(capture_number)
                for index, (z_min, z_max) in enumerate(z_bands[1:]):
                    if preparation_cancel.is_set():
                        raise RenderCancelledError("Export preparation was cancelled.")
                    static_band_frames[index].append(self._stage_export_frame(CanvasSnapshot.capture_track(
                        self.canvas.scene_model, capture_track, capture_number, len(active_tracks), capture_start,
                        z_min=z_min, z_max=z_max, transparent=True, **common,
                    ), frame_duration, f"layer:{index}"))
                    pump_preparation_ui(capture_number)
                return base
            cursor = 0.0
            previous_track: PlaylistTrack | None = None
            previous_start = 0.0
            previous_number = 1
            for number, track in enumerate(active_tracks, start=1):
                requested = track.start_time_seconds if track.start_time_seconds is not None else cursor
                start = max(cursor, requested)
                sources = self.store.sources()
                gap = max(0.0, start - cursor)
                if gap > 0.001:
                    # Explicit-frame exports need a visual frame for every silent
                    # timeline gap as well.  Without it FFmpeg kept the previous
                    # frame while audio advanced, shifting all later metadata.
                    gap_track = previous_track or track
                    gap_elapsed = gap_track.duration_seconds if previous_track else 0.0
                    gap_points = {cursor, start}
                    for source in sources:
                        for boundary in (
                            source.timeline_start,
                            source.timeline_start + source.timeline_duration,
                        ):
                            if cursor < boundary < start and (
                                boundary == source.timeline_start or source.timeline_duration > 0.0
                            ):
                                gap_points.add(boundary)
                    ordered_gap_points = sorted(gap_points)
                    for point, next_point in zip(
                        ordered_gap_points, ordered_gap_points[1:], strict=True
                    ):
                        frames.append(capture_composed_frame(
                            gap_track, previous_number if previous_track else number,
                            previous_start if previous_track else start,
                            max(0.001, next_point - point),
                            elapsed_seconds=gap_elapsed,
                            timeline_seconds=min(next_point - 0.0005, point + 0.0005),
                        ))
                intro = min(track.duration_seconds / 2, max((source.animation_duration for source in sources if source.animation_in != "none"), default=0.0))
                outro = min((track.duration_seconds - intro) / 2, max((source.animation_duration for source in sources if source.animation_out != "none"), default=0.0))

                def capture_animation_frames(
                    phase: str, duration: float,
                ) -> list[RenderFrame]:
                    """Capture animation frames only when their timeline segment is due.

                    Foreground Z bands are staged as a side effect of each capture.
                    Capturing outro frames before the stable segment therefore shifts
                    those bands to the track start even if their base frames are held
                    in a temporary list.  Keeping capture and append order identical
                    prevents the base and transparent streams from drifting apart.
                    """
                    captured: list[RenderFrame] = []
                    if duration <= 0:
                        return captured
                    steps = max(2, round(duration * animation_fps))
                    for step in range(steps):
                        progress = (step + 1) / steps if phase == "in" else step / steps
                        elapsed = (
                            duration * (step + 1) / steps
                            if phase == "in"
                            else intro + max(0.0, track.duration_seconds - intro - outro)
                            + duration * (step + 1) / steps
                        )
                        captured.append(capture_composed_frame(
                            track, number, start, duration / steps,
                            animation_phase=phase, animation_progress=progress,
                            animation_phase_duration=duration,
                            elapsed_seconds=elapsed,
                        ))
                    return captured

                stable = max(0.01, track.duration_seconds - intro - outro)
                frames.extend(capture_animation_frames("in", intro))
                has_progress_bar = any(source.source_type is SourceType.PROGRESS_BAR for source in sources)
                has_lyrics = any(source.source_type is SourceType.LYRICS for source in sources)
                sample_points = {intro, intro + stable}
                for source in sources:
                    boundaries = [source.timeline_start]
                    if source.timeline_duration > 0.0:
                        boundaries.append(source.timeline_start + source.timeline_duration)
                    for boundary in boundaries:
                        local_point = boundary - start
                        if intro < local_point < intro + stable:
                            sample_points.add(local_point)
                if has_progress_bar:
                    progress_steps = min(180, max(1, round(stable)))
                    sample_points.update(intro + stable * step / progress_steps for step in range(progress_steps + 1))
                if has_lyrics:
                    lyric_sources = [
                        source for source in sources
                        if source.source_type is SourceType.LYRICS
                    ]
                    track_offset = track.lyrics_timing_offset_seconds
                    for cue in track.lyrics:
                        for point in (float(cue.get("start", 0.0)), float(cue.get("end", 0.0))):
                            for lyric_source in lyric_sources:
                                adjusted_point = (
                                    point - track_offset
                                    - lyric_source.subtitle_timing_offset
                                )
                                if intro < adjusted_point < intro + stable:
                                    sample_points.add(adjusted_point)
                        cue_start = float(cue.get("start", 0.0))
                        for lyric_source in lyric_sources:
                            if lyric_source.subtitle_animation == "none":
                                continue
                            steps = max(1, round(lyric_source.subtitle_animation_duration * animation_fps))
                            for step in range(steps + 1):
                                point = (cue_start - track_offset
                                         - lyric_source.subtitle_timing_offset
                                         + lyric_source.subtitle_animation_duration * step / steps)
                                if intro < point < intro + stable:
                                    sample_points.add(point)
                for now_source in (source for source in sources if source.source_type is SourceType.NOW_PLAYING):
                    exit_start = max(0.0, now_source.now_playing_duration - now_source.now_playing_exit_duration)
                    steps = max(1, round(now_source.now_playing_exit_duration * animation_fps))
                    for step in range(steps + 1):
                        point = exit_start + now_source.now_playing_exit_duration * step / steps
                        if intro < point < intro + stable:
                            sample_points.add(point)
                ordered_points = sorted(sample_points)
                for point_index, point in enumerate(ordered_points[:-1]):
                    next_point = ordered_points[point_index + 1]
                    elapsed = min(next_point - 0.0005, point + 0.0005)
                    frames.append(capture_composed_frame(
                        track, number, start, max(0.001, next_point - point),
                        elapsed_seconds=elapsed,
                    ))
                frames.extend(capture_animation_frames("out", outro))
                cursor = start + track.duration_seconds
                previous_track = track
                previous_start = start
                previous_number = number
            static_layers = [
                StaticOverlayLayer(z_min if z_min is not None else -10_000.0, layer_frames)
                for (z_min, _z_max), layer_frames in zip(z_bands[1:], static_band_frames, strict=True)
                if layer_frames
            ]
            if preparation_cancel.is_set():
                raise RenderCancelledError("Export preparation was cancelled.")
        except RenderCancelledError:
            self._clear_export_frame_staging()
            if self._export_dialog:
                self._export_dialog.complete(False)
                self._export_dialog = None
            self._unlock_main_form_after_export()
            self.statusBar().showMessage(
                "내보내기를 취소했습니다." if korean else "Export cancelled.", 5000
            )
            return
        except RenderError as error:
            self._clear_export_frame_staging()
            if self._export_dialog:
                self._export_dialog.complete(False)
                self._export_dialog = None
            self._unlock_main_form_after_export()
            QMessageBox.critical(
                self, "내보내기 오류" if korean else "Export error", str(error)
            )
            return
        except Exception as error:
            self._clear_export_frame_staging()
            if self._export_dialog:
                self._export_dialog.complete(False)
                self._export_dialog = None
            self._unlock_main_form_after_export()
            report_unexpected_error("Preparing export frames", error)
            QMessageBox.critical(
                self, "내보내기 오류" if korean else "Export error", str(error)
            )
            return
        try:
            self._export_dialog.cancel_requested.disconnect(request_preparation_cancel)
        except (RuntimeError, TypeError):
            pass
        if korean:
            self._export_dialog.setWindowTitle("내보내기 진행 상황")
            self._export_dialog.cancel_button.setText("취소")
            self._export_dialog.stage_label.setText("내보내기 준비 중")
        self._render_worker = RenderWorker(
            renderer,
            frames,
            self.playlist_service.tracks,
            output,
            render_settings,
            visualizers,
            static_layers,
        )
        self._render_worker.progress.connect(self._export_dialog.update_progress)
        self._render_worker.succeeded.connect(self._export_succeeded)
        self._render_worker.failed.connect(self._export_failed)
        self._render_worker.cancelled.connect(self._export_cancelled)
        self._render_worker.finished.connect(self._export_finished)
        self._export_dialog.cancel_requested.connect(self._render_worker.cancel)
        self.statusBar().showMessage("렌더링 중..." if korean else "Rendering...")
        self._render_worker.start()

    def _open_playlist_preview(self) -> None:
        """Open the independent full-playlist playback preview."""
        tracks = [track for track in self.playlist_service.tracks if track.enabled]
        if not tracks:
            QMessageBox.warning(
                self, "Preview", "Select at least one music track before opening Preview."
            )
            return
        self._show_export_preview(tracks)

    def _show_export_preview(self, tracks: list) -> None:
        """Open a track-aware Canvas preview without beginning an FFmpeg export."""
        executable = None
        try:
            executable = FFmpegRenderer(
                self.settings_service.current.ffmpeg_path or None
            ).executable
        except FFmpegNotFoundError:
            pass
        preview = ExportPreviewDialog(
            self.canvas.scene_model, tracks, self.translator,
            self._export_visualizers(), executable, self, source_store=self.store,
        )
        preview.exec()

    def _preview_source_animation(self, source_id: str) -> None:
        """Play one source's configured animation directly on the Canvas."""
        if self._animation_preview_active:
            return
        source = self.store.get(source_id)
        item = self.canvas._items.get(source_id)
        if source is None or item is None:
            return
        self._animation_preview_active = True
        korean = self.translator.language is Language.KOREAN
        self.statusBar().showMessage(
            "애니메이션 미리보기 재생 중 · 편집이 잠겼습니다."
            if korean else
            "Playing animation preview · Editing is locked."
        )
        self.setEnabled(False)
        if not self.animation_preview_controller.preview(item, source):
            self._finish_canvas_animation_preview()

    def _finish_canvas_animation_preview(self) -> None:
        """Restore interaction after the Canvas preview returns to its source state."""
        if not self._animation_preview_active:
            return
        self._animation_preview_active = False
        self.setEnabled(True)
        korean = self.translator.language is Language.KOREAN
        self.statusBar().showMessage(
            "애니메이션 미리보기가 끝났습니다."
            if korean else "Animation preview finished.",
            2500,
        )
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

    def _export_visualizers(self) -> list[VisualizerOverlay]:
        """Translate visible, axis-aligned Canvas visualizers into Python-rendered overlays."""
        overlays: list[VisualizerOverlay] = []
        for source in self.store.sources():
            if (source.source_type not in {
                    SourceType.AUDIO_VISUALIZER, SourceType.AUDIO_WAVEFORM,
                    SourceType.AUDIO_LEVEL_METER, SourceType.PARTICLE_OVERLAY,
                } or not source.visible):
                continue
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
            # and right compared with the Canvas placement.
            overlays.append(VisualizerOverlay(
                x=round(source.x + (source.width - overlay_width) / 2.0),
                y=round(source.y + (source.height - overlay_height) / 2.0),
                width=overlay_width,
                height=overlay_height,
                style=source.visualizer_style,
                color=source.fill_color,
                opacity=source.opacity,
                bar_count=source.visualizer_bars,
                line_width=source.visualizer_line_width,
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
                level_meter_gap=source.level_meter_gap,
                level_meter_show_peak=source.level_meter_show_peak,
                level_meter_peak_hold=source.level_meter_peak_hold,
                level_meter_peak_decay=source.level_meter_peak_decay,
                level_meter_track_color=source.level_meter_track_color,
                level_meter_low_color=source.level_meter_low_color,
                level_meter_mid_color=source.level_meter_mid_color,
                level_meter_high_color=source.level_meter_high_color,
                particle_min_size=source.particle_min_size,
                particle_max_size=source.particle_max_size,
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
            ))
        # FFmpeg overlays later inputs on top.  Preserve the Canvas stacking
        # order when two reactive sources overlap.
        return sorted(overlays, key=lambda overlay: (overlay.z_index, overlay.y, overlay.x))

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

    def _show_settings(self) -> None:
        """Show and persist the Phase 4A application settings."""
        dialog = SettingsDialog(
            self.settings_service.current,
            self.translator.language,
            self.theme_service.preference,
            self.translator,
            self,
        )
        self._settings_dialog = dialog
        dialog.download_requested.connect(lambda: self._start_ffmpeg_install(dialog))
        if dialog.exec() != dialog.DialogCode.Accepted:
            self._settings_dialog = None
            return
        self.settings_service.save(dialog.app_settings)
        self.theme_service.set_preference(dialog.selected_theme)
        self.translator.set_language(dialog.selected_language)
        self._settings_dialog = None
        korean = self.translator.language is Language.KOREAN
        self.statusBar().showMessage(
            "설정을 저장했습니다." if korean else "Settings saved.", 4000
        )

    def _start_ffmpeg_install(self, settings_dialog: SettingsDialog) -> None:
        """Request consent, then download the checksum-verified managed FFmpeg build."""
        if self._ffmpeg_install_worker and self._ffmpeg_install_worker.isRunning():
            return
        korean = self.translator.language is Language.KOREAN
        message = (
            "BtbN GitHub 배포본의 Windows 64비트 GPL FFmpeg을 다운로드합니다.\n"
            "SHA-256 체크섬 및 ffmpeg -version 검증 후에만 앱 전용 폴더에 설치됩니다.\n\n"
            "계속할까요?"
            if korean else
            "This downloads BtbN's GPL FFmpeg build for Windows 64-bit.\n"
            "It is installed in the app-only folder only after SHA-256 and ffmpeg -version verification.\n\n"
            "Continue?"
        )
        response = QMessageBox.question(
            settings_dialog,
            "FFmpeg 다운로드" if korean else "Download FFmpeg",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        # PySide can return an equivalent enum wrapper instead of the exact
        # same Python object, so a value comparison is required here.
        if response != QMessageBox.StandardButton.Yes:
            return
        settings_dialog.set_ffmpeg_installing(True)
        progress = FFmpegInstallProgressDialog(korean, settings_dialog)
        progress.set_busy(
            "Preparing download",
            "GitHub에서 최신 FFmpeg 배포 정보를 확인하는 중입니다."
            if korean else "Checking the latest FFmpeg release on GitHub.",
        )
        worker = FFmpegInstallWorker(ManagedFFmpegInstaller())
        self._ffmpeg_install_dialog = progress
        self._ffmpeg_install_worker = worker
        worker.progress.connect(progress.update_progress)
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
        worker.start()

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
            self._settings_dialog.set_ffmpeg_installing(False)
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
        if self._settings_dialog and self._settings_dialog.isVisible():
            self._settings_dialog.ffmpeg_download_button.setEnabled(True)
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
            if answer is not QMessageBox.StandardButton.Yes:
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
        QMessageBox.information(self, "내보내기 완료" if korean else "Export complete", message)

    def _export_failed(self, message: str) -> None:
        """Show FFmpeg failure details reported by the worker thread."""
        LOGGER.error("Video export failed: %s", message)
        if self._export_dialog:
            self._export_dialog.complete(False)
            self._export_dialog = None
        korean = self.translator.language is Language.KOREAN
        QMessageBox.critical(self, "내보내기 오류" if korean else "Export error", message)
        self.statusBar().showMessage(message, 7000)

    def _export_cancelled(self) -> None:
        """Close the progress window after safe cancellation and temp cleanup."""
        if self._export_dialog:
            self._export_dialog.complete(False)
            self._export_dialog = None
        message = "내보내기를 취소했습니다." if self.translator.language is Language.KOREAN else "Export cancelled."
        self.statusBar().showMessage(message, 5000)

    def _export_finished(self) -> None:
        """Release the UI export lock after any worker completion path."""
        self._unlock_main_form_after_export()
        self._clear_export_frame_staging()
        QTimer.singleShot(0, self._release_render_worker)

    def _release_render_worker(self) -> None:
        """Delete the completed worker after its queued result signal is delivered."""
        if self._render_worker:
            self._render_worker.deleteLater()
        self._render_worker = None

    def _choose_preset(self) -> None:
        """Select and apply a complete visual canvas preset to this project."""
        dialog = DesignPresetDialog(self.translator, self)
        if dialog.exec() and dialog.selected_preset:
            self._apply_preset(dialog.selected_preset)

    def _show_ai_project_builder(self) -> None:
        """Open the standalone configurable AI Project Builder."""
        AIProjectBuilderDialog(self.translator, self).exec()

    def _apply_preset(self, preset: PresetDefinition) -> None:
        """Replace canvas sources with a preset while preserving the playlist."""
        self.store.replace(preset.builder())
        message = "프리셋을 적용했습니다. Ctrl+Z로 되돌릴 수 있습니다."
        if self.translator.language is not Language.KOREAN:
            message = "Preset applied. Press Ctrl+Z to undo."
        self.statusBar().showMessage(message, 4000)

    def _new_project(self) -> bool:
        if not self._confirm_unsaved_changes():
            return False
        dialog = NewProjectDialog(self.translator, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        canvas_width, canvas_height = dialog.canvas_size
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
            self._add_welcome_sources()
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
        self.canvas.zoom_changed.connect(lambda _zoom: self._schedule_history())
        self.history.changed.connect(self._update_history_actions)

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
                if self._new_project():
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
        if self.centralWidget() is not None:
            self.motion.fade_in(self.centralWidget(), 140)

    def _set_sidebar_visible(self, visible: bool) -> None:
        """Collapse or reveal the editing sidebar with a short width animation."""
        if self._sidebar_transition:
            return
        self._sidebar_transition = True
        self.panels_action.setEnabled(False)
        if visible:
            target_width = max(180, self._sidebar_open_width)
            self.left_workspace.setVisible(True)
            self.left_workspace.setMinimumWidth(0)
            self.left_workspace.setMaximumWidth(0)
            self.motion.animate_width(self.left_workspace, 0, target_width)

            def finish_expand() -> None:
                self.left_workspace.setMaximumWidth(16_777_215)
                self.left_workspace.setMinimumWidth(180)
                self._sidebar_transition = False
                self.panels_action.setEnabled(True)
                self.motion.fade_in(self.left_workspace, 120)

            QTimer.singleShot(205, finish_expand)
            return
        self._sidebar_open_width = max(180, self.left_workspace.width())
        self.left_workspace.setMinimumWidth(0)
        self.left_workspace.setMaximumWidth(self._sidebar_open_width)
        self.motion.animate_width(self.left_workspace, self._sidebar_open_width, 0)

        def finish_collapse() -> None:
            self.left_workspace.setVisible(False)
            self.left_workspace.setMaximumWidth(16_777_215)
            self._sidebar_transition = False
            self.panels_action.setEnabled(True)

        QTimer.singleShot(205, finish_collapse)

    def _schedule_history(self) -> None:
        """Coalesce rapid property edits such as dragging into a single undo entry."""
        if self._history_ready and not self._history_restoring:
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
        state = (
            "저장됨" if korean and not self._project_dirty else
            "저장 필요" if korean else
            "Saved" if not self._project_dirty else "Unsaved"
        )
        legacy = " · 레거시 JSON" if korean and self._legacy_project_path else (
            " · Legacy JSON" if self._legacy_project_path else ""
        )
        self.project_status_label.setText(f"{name}  ·  {state}{legacy}")
        self.project_status_label.setToolTip(
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
        """Periodically write a recovery document without changing the active project."""
        if not self._history_ready or not self._project_dirty:
            return
        try:
            self.autosave.save(self._project_document(), self.current_project_path)
            self._update_project_status()
            message = "자동 저장됨" if self.translator.language is Language.KOREAN else "Autosaved"
            self.statusBar().showMessage(message, 2500)
        except ProjectError as error:
            self.statusBar().showMessage(str(error), 5000)

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
        if answer is QMessageBox.StandardButton.Yes:
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

    def _save_project(self, force_choose: bool = False) -> None:
        """Save to the active project path or ask the user where to save."""
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
                return
            target = Path(selected)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.statusBar().showMessage(
            "프로젝트 저장 중..." if self.translator.language is Language.KOREAN
            else "Saving project..."
        )
        QApplication.processEvents()
        try:
            previous_project_path = self.current_project_path
            self.current_project_path = ProjectService.save(
                target, self._project_document(), self._project_thumbnail_image()
            )
            self._legacy_project_path = (
                self.current_project_path
                if self.current_project_path.suffix.lower() == ".json" else None
            )
            self.upgrade_project_action.setEnabled(self._legacy_project_path is not None)
            self.recent_projects.add(self.current_project_path)
            self.autosave.clear(previous_project_path)
            self.autosave.clear(self.current_project_path)
            self._project_dirty = False
            self._update_project_status()
            message = "프로젝트를 저장했습니다." if self.translator.language is Language.KOREAN else "Project saved."
            self.statusBar().showMessage(message, 4000)
        except ProjectError as error:
            self._show_project_error(error)
        finally:
            QApplication.restoreOverrideCursor()

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
        previous_project_path = self.current_project_path
        had_unsaved_changes = self._project_dirty
        if not self._confirm_unsaved_changes():
            return
        if self._load_project_path(Path(selected)) and had_unsaved_changes:
            try:
                self.autosave.clear(previous_project_path)
            except ProjectError as error:
                self.statusBar().showMessage(str(error), 5000)

    def _confirm_unsaved_changes(self) -> bool:
        """Save, explicitly discard, or keep the active unsaved workspace."""
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
            self._save_project()
            return not self._project_dirty
        return response == QMessageBox.StandardButton.Discard

    def _load_project_path(self, path: Path) -> bool:
        """Restore a selected project path through the normal safe load workflow."""
        try:
            document = ProjectService.load(path)
            if (document.settings.title == "Untitled Project"
                    and path.suffix.lower() == ".json"):
                document.settings.title = path.stem.removesuffix(".project")
            if not self._resolve_project_media(document, path):
                return False
            self._history_restoring = True
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
        except ProjectError as error:
            self._show_project_error(error)
            return False

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
        thumbnail = QPixmap.fromImage(self._project_thumbnail_image())
        dialog = ProjectSettingsDialog(
            self.project_settings, self.translator, thumbnail, self
        )
        if dialog.exec() == dialog.DialogCode.Accepted:
            self.project_settings = dialog.selected_settings
            self._schedule_history()
            self._update_project_status()

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
        if response is QMessageBox.StandardButton.Yes:
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
        self.ai_project_builder_action.setText(
            "AI 프로젝트 빌더" if self.translator.language is Language.KOREAN
            else "AI Project Builder"
        )
        self.fit_action.setText(text("fit_canvas"))
        self.grid_action.setText(text("grid"))
        self.delete_action.setText(text("delete"))
        self.export_action.setText(text("export"))
        self.preview_action.setText("미리보기" if self.translator.language is Language.KOREAN else "Preview")
        self.settings_action.setText("설정" if self.translator.language is Language.KOREAN else "Settings")
        self.file_menu.setTitle("파일" if self.translator.language is Language.KOREAN else "File")
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
        self.panels_action.setText("패널" if korean else "Panels")
        self.sidebar_title.setText(text("add_to_canvas"))
        self.left_tabs.setTabText(
            0, "요소" if self.translator.language is Language.KOREAN else "Sources"
        )
        self.left_tabs.setTabText(
            1, "프로젝트 콘텐츠" if self.translator.language is Language.KOREAN else "Project content"
        )
        self.bottom_tabs.setTabText(
            0, "플레이리스트" if self.translator.language is Language.KOREAN else "Playlist"
        )
        self.bottom_tabs.setTabText(
            1, "타임라인" if self.translator.language is Language.KOREAN else "Timeline"
        )
        self.source_search.setPlaceholderText(
            "요소 검색…" if korean else "Search sources…"
        )
        for source_type, button in self._source_buttons.items():
            label = self._source_type_label(source_type)
            button.setText(f"+  {label}")
            self._update_source_button_help(source_type, button, label)
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
            "전체 플레이리스트를 실제 음원과 함께 미리 확인합니다."
            if self.translator.language is Language.KOREAN
            else "Preview the complete playlist with the actual audio tracks."
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
        """Reveal and focus the Playlist or Timeline workspace tab."""
        self.bottom_tabs.setCurrentIndex(max(0, min(self.bottom_tabs.count() - 1, index)))
        sizes = self.workspace_splitter.sizes()
        if len(sizes) == 2 and sizes[1] < 180:
            total = max(500, sum(sizes))
            self.workspace_splitter.setSizes([max(300, total - 280), 280])
        target = (
            self.playlist_editor.list_widget if index == 0
            else self.timeline_panel.track_table
        )
        target.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Avoid destroying a running FFmpeg thread during application shutdown."""
        if self._animation_preview_active:
            event.ignore()
            return
        if self._render_worker and self._render_worker.isRunning():
            if self._export_dialog is not None:
                cancellation_requested = self._export_dialog.request_cancel()
            else:
                self._render_worker.cancel()
                cancellation_requested = True
            if cancellation_requested:
                message = "내보내기를 안전하게 취소하는 중입니다." if self.translator.language is Language.KOREAN else "Cancelling export safely..."
                self.statusBar().showMessage(message, 5000)
            event.ignore()
            return
        if self._ffmpeg_install_worker and self._ffmpeg_install_worker.isRunning():
            self._ffmpeg_install_worker.cancel()
            message = "FFmpeg 다운로드를 취소하는 중입니다." if self.translator.language is Language.KOREAN else "Cancelling FFmpeg download..."
            self.statusBar().showMessage(message, 5000)
            event.ignore()
            return
        if self._project_dirty:
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
                self._save_project()
                if self._project_dirty:
                    # The user cancelled Save As or saving failed; stay in the editor.
                    event.ignore()
                    return
            elif response != QMessageBox.StandardButton.Discard:
                # Fail closed: Cancel, Escape, or an unrecognized response keeps
                # the unsaved workspace open. Only explicit Discard may exit.
                event.ignore()
                return
        self._clear_recovery()
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
        self.setStyleSheet(
            f"""
            QMainWindow {{ background: {colors['window']}; color: {colors['text']}; }}
            QDialog#startupDialog {{ background: {colors['window']}; color: {colors['text']}; }}
            QDialog#startupDialog QFrame#card {{ background: {colors['panel']}; border: 1px solid {colors['border']}; border-radius: 12px; }}
            #startupTitle {{ color: {colors['text']}; }}
            #recentProjectList {{ background: {colors['field']}; border: 1px solid {colors['border']}; border-radius: 8px; padding: 5px; }}
            #recentProjectList::item {{ padding: 5px; margin: 2px; border-radius: 6px; }}
            #recentProjectThumbnail, #thumbnailPreview {{ background: {colors['alternate']}; color: {colors['muted']}; border: 1px solid {colors['border']}; border-radius: 7px; font-weight: 700; }}
            #leftProjectTabs::pane {{ border: 0; background: {colors['panel']}; }}
            #leftProjectTabs QTabBar::tab {{ background: {colors['button']}; color: {colors['muted']}; padding: 7px 10px; border: 1px solid {colors['border']}; }}
            #leftProjectTabs QTabBar::tab:selected {{ background: {colors['panel']}; color: {colors['text']}; border-bottom-color: {colors['panel']}; }}
            QToolBar {{ background: {colors['panel']}; border: 0; border-bottom: 1px solid {colors['border']}; spacing: 6px; padding: 7px 10px; }}
            QToolButton, QPushButton {{ background: {colors['button']}; color: {colors['text']}; border: 1px solid {colors['border']}; border-radius: 8px; padding: 7px 10px; }}
            QToolButton:hover, QPushButton:hover {{ background: {colors['hover']}; border-color: #55B8FF; }}
            QToolTip {{ background: {colors['panel']}; color: {colors['text']}; border: 1px solid #55B8FF; border-radius: 7px; padding: 8px; }}
            QToolButton:checked {{ background: #1685D1; color: #FFFFFF; }}
            QToolButton:disabled {{ color: {colors['muted']}; background: {colors['disabled']}; }}
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
            #sourceSearch {{ padding-left: 9px; min-height: 22px; }}
            QScrollArea, QListWidget, QTreeWidget, QTableWidget {{ background: {colors['panel']}; color: {colors['text']}; border: 0; }}
            #sourceInspector, #sourceInspector::viewport, #inspectorContent {{ background: {colors['panel']}; color: {colors['text']}; }}
            #inspectorEmptyState {{ color: {colors['muted']}; font-size: 14px; background: {colors['panel']}; }}
            QHeaderView::section {{ background: {colors['alternate']}; color: {colors['text']}; border: 0; border-bottom: 1px solid {colors['border']}; padding: 5px; }}
            QTreeWidget::item:selected, QListWidget::item:selected {{ background: #1685D1; color: #FFFFFF; border-radius: 5px; }}
            #trackRow {{ background: {colors['field']}; border: 1px solid {colors['border']}; border-radius: 8px; }}
            #trackRow:hover {{ background: {colors['hover']}; }}
            QGroupBox {{ color: {colors['text']}; font-weight: 600; border: 1px solid {colors['border']}; border-radius: 8px; margin-top: 10px; padding: 10px 7px 7px 7px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; }}
            QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background: {colors['field']}; color: {colors['text']}; border: 1px solid {colors['border']}; border-radius: 6px; padding: 5px; min-height: 18px; }}
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border: 2px solid #1685D1; padding: 4px; }}
            QCheckBox, QLabel {{ color: {colors['text']}; padding: 3px; }}
            QScrollBar:vertical {{ background: transparent; width: 10px; margin: 3px; }}
            QScrollBar::handle:vertical {{ background: {colors['border']}; min-height: 28px; border-radius: 5px; }}
            QScrollBar::handle:vertical:hover {{ background: {colors['muted']}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QSplitter::handle {{ background: {colors['border']}; width: 2px; height: 5px; }}
            QSplitter::handle:hover {{ background: #1685D1; }}
            """
        )
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
