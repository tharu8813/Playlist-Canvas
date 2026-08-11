"""Modal project chooser displayed before the editor becomes accessible."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from app.services.recent_projects_service import RecentProjectsService
from app.services.project_service import ProjectError, ProjectService, ProjectSummary
from app.utils.i18n import Language, Translator


class _RecentProjectRow(QWidget):
    """Thumbnail and project metadata displayed in one recent-project row."""

    def __init__(self, path: Path, summary: ProjectSummary, modified: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(12)
        thumbnail = QLabel()
        thumbnail.setObjectName("recentProjectThumbnail")
        thumbnail.setFixedSize(128, 72)
        thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap()
        if summary.thumbnail:
            pixmap.loadFromData(summary.thumbnail)
        if not pixmap.isNull():
            thumbnail.setPixmap(pixmap.scaled(
                thumbnail.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            ))
        else:
            thumbnail.setText("PVS")
        layout.addWidget(thumbnail)
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        title = QLabel(summary.title)
        title.setObjectName("panelTitle")
        description = QLabel(summary.description or str(path.parent))
        description.setObjectName("mutedLabel")
        description.setWordWrap(False)
        meta_parts = [part for part in (summary.author, modified) if part]
        metadata = QLabel("  ·  ".join(meta_parts))
        metadata.setObjectName("mutedLabel")
        text_layout.addWidget(title)
        text_layout.addWidget(description)
        text_layout.addWidget(metadata)
        layout.addLayout(text_layout, 1)


class StartupDialog(QDialog):
    """Choose how the initial editing session should begin."""

    NEW_PROJECT = "new"
    OPEN_PROJECT = "open"

    def __init__(self, translator: Translator, recent: RecentProjectsService,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.translator = translator
        self.recent = recent
        self.action = ""
        self.project_path: Path | None = None
        self.setObjectName("startupDialog")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setMinimumSize(820, 520)
        self.resize(900, 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 26, 28, 24)
        root.setSpacing(18)
        self.title = QLabel()
        self.title.setObjectName("startupTitle")
        title_font = self.title.font()
        title_font.setPointSize(22)
        title_font.setBold(True)
        self.title.setFont(title_font)
        self.description = QLabel()
        self.description.setObjectName("mutedLabel")
        root.addWidget(self.title)
        root.addWidget(self.description)

        body = QHBoxLayout()
        body.setSpacing(18)
        actions_card = QFrame()
        actions_card.setObjectName("card")
        actions_layout = QVBoxLayout(actions_card)
        actions_layout.setContentsMargins(18, 18, 18, 18)
        actions_layout.setSpacing(12)
        self.actions_title = QLabel()
        self.actions_title.setObjectName("panelTitle")
        self.new_button = QPushButton()
        self.new_button.setMinimumHeight(64)
        self.new_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon))
        self.new_button.setIconSize(QSize(24, 24))
        self.open_button = QPushButton()
        self.open_button.setMinimumHeight(64)
        self.open_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton)
        )
        self.open_button.setIconSize(QSize(24, 24))
        actions_layout.addWidget(self.actions_title)
        actions_layout.addWidget(self.new_button)
        actions_layout.addWidget(self.open_button)
        actions_layout.addStretch()
        body.addWidget(actions_card, 2)

        recent_card = QFrame()
        recent_card.setObjectName("card")
        recent_layout = QVBoxLayout(recent_card)
        recent_layout.setContentsMargins(18, 18, 18, 14)
        recent_layout.setSpacing(10)
        recent_header = QHBoxLayout()
        self.recent_title = QLabel()
        self.recent_title.setObjectName("panelTitle")
        self.clear_button = QPushButton()
        self.clear_button.setFlat(True)
        recent_header.addWidget(self.recent_title)
        recent_header.addStretch()
        recent_header.addWidget(self.clear_button)
        recent_layout.addLayout(recent_header)
        self.recent_list = QListWidget()
        self.recent_list.setObjectName("recentProjectList")
        self.recent_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.recent_list.setAlternatingRowColors(False)
        recent_layout.addWidget(self.recent_list, 1)
        recent_buttons = QHBoxLayout()
        self.remove_button = QPushButton()
        self.open_recent_button = QPushButton()
        self.open_recent_button.setDefault(True)
        recent_buttons.addWidget(self.remove_button)
        recent_buttons.addStretch()
        recent_buttons.addWidget(self.open_recent_button)
        recent_layout.addLayout(recent_buttons)
        body.addWidget(recent_card, 3)
        root.addLayout(body, 1)

        self.exit_hint = QLabel()
        self.exit_hint.setObjectName("mutedLabel")
        root.addWidget(self.exit_hint)

        self.new_button.clicked.connect(self._choose_new)
        self.open_button.clicked.connect(self._browse_project)
        self.open_recent_button.clicked.connect(self._open_recent)
        self.remove_button.clicked.connect(self._remove_recent)
        self.clear_button.clicked.connect(self._clear_recent)
        self.recent_list.itemDoubleClicked.connect(lambda _item: self._open_recent())
        self.recent_list.itemSelectionChanged.connect(self._update_recent_buttons)
        translator.language_changed.connect(self.retranslate)
        recent.changed.connect(self.refresh_recent)
        self.retranslate()
        self.refresh_recent()

    def retranslate(self) -> None:
        """Refresh all visible startup text."""
        korean = self.translator.language is Language.KOREAN
        self.setWindowTitle("프로젝트 시작" if korean else "Start a project")
        self.title.setText("Playlist Canvas")
        self.description.setText(
            "새 작업을 시작하거나 기존 프로젝트에서 계속하세요."
            if korean else "Start something new or continue an existing project."
        )
        self.actions_title.setText("시작" if korean else "Get started")
        self.new_button.setText("새 프로젝트 만들기" if korean else "Create new project")
        self.open_button.setText("프로젝트 파일 열기" if korean else "Open project file")
        self.recent_title.setText("최근 프로젝트" if korean else "Recent projects")
        self.clear_button.setText("목록 지우기" if korean else "Clear list")
        self.remove_button.setText("목록에서 제거" if korean else "Remove from list")
        self.open_recent_button.setText("선택한 프로젝트 열기" if korean else "Open selected")
        self.exit_hint.setText(
            "이 창을 닫으면 프로그램이 종료됩니다."
            if korean else "Closing this window exits the application."
        )
        self.refresh_recent()

    def refresh_recent(self) -> None:
        """Rebuild the list with current file names, folders, and timestamps."""
        selected_path = self._selected_recent_path()
        self.recent_list.clear()
        projects = self.recent.projects()
        korean = self.translator.language is Language.KOREAN
        if not projects:
            empty = QListWidgetItem(
                "최근 프로젝트가 없습니다." if korean else "No recent projects yet."
            )
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.recent_list.addItem(empty)
        else:
            for path in projects:
                try:
                    modified = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                except OSError:
                    modified = ""
                try:
                    summary = ProjectService.inspect(path)
                except ProjectError:
                    summary = ProjectSummary(
                        path.stem.removesuffix(".project"), "", "", ""
                    )
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, str(path))
                item.setToolTip(str(path))
                item.setSizeHint(QSize(0, 88))
                self.recent_list.addItem(item)
                self.recent_list.setItemWidget(
                    item, _RecentProjectRow(path, summary, modified, self.recent_list)
                )
                if selected_path == path:
                    self.recent_list.setCurrentItem(item)
            if self.recent_list.currentItem() is None:
                self.recent_list.setCurrentRow(0)
        self.clear_button.setEnabled(bool(projects))
        self._update_recent_buttons()

    def _selected_recent_path(self) -> Path | None:
        item = self.recent_list.currentItem()
        value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return Path(str(value)) if value else None

    def _update_recent_buttons(self) -> None:
        enabled = self._selected_recent_path() is not None
        self.open_recent_button.setEnabled(enabled)
        self.remove_button.setEnabled(enabled)

    def _choose_new(self) -> None:
        self.action = self.NEW_PROJECT
        self.project_path = None
        self.accept()

    def _browse_project(self) -> None:
        korean = self.translator.language is Language.KOREAN
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "프로젝트 열기" if korean else "Open project",
            "",
            "Playlist Canvas Project (*.pvsproj *.project.json *.json)",
        )
        if selected:
            self.action = self.OPEN_PROJECT
            self.project_path = Path(selected)
            self.accept()

    def _open_recent(self) -> None:
        path = self._selected_recent_path()
        if path is None:
            return
        self.action = self.OPEN_PROJECT
        self.project_path = path
        self.accept()

    def _remove_recent(self) -> None:
        path = self._selected_recent_path()
        if path is not None:
            self.recent.remove(path)

    def _clear_recent(self) -> None:
        self.recent.clear()
