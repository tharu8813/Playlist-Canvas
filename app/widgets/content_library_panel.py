"""Project-scoped reusable media list."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor, QFont, QIcon, QPainter, QPen, QPixmap, QPolygonF,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMenu, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from app.services.project_content_service import ProjectContentService
from app.utils.i18n import Language, Translator


class ContentLibraryPanel(QWidget):
    """Import project content and reuse it without browsing repeatedly."""

    add_requested = Signal(str, str)

    def __init__(
        self, service: ProjectContentService, translator: Translator,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.translator = translator
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 12, 10, 10)
        layout.setSpacing(8)
        self.help_label = QLabel()
        self.help_label.setObjectName("mutedLabel")
        self.help_label.setWordWrap(True)
        layout.addWidget(self.help_label)
        self.list = QListWidget()
        self.list.setObjectName("projectContentList")
        self.list.setIconSize(QSize(38, 38))
        self.list.setSpacing(2)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.itemDoubleClicked.connect(lambda _item: self._add_selected())
        self.list.itemSelectionChanged.connect(self._update_buttons)
        self.list.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.import_button = QPushButton()
        self.add_button = QPushButton()
        self.remove_button = QPushButton()
        row.addWidget(self.import_button)
        row.addStretch()
        row.addWidget(self.remove_button)
        row.addWidget(self.add_button)
        layout.addLayout(row)
        self.import_button.clicked.connect(self._import_files)
        self.add_button.clicked.connect(self._add_selected)
        self.remove_button.clicked.connect(self._remove_selected)
        self.service.changed.connect(self.refresh)
        self.translator.language_changed.connect(self.retranslate)
        self.retranslate()
        self.refresh()

    def retranslate(self) -> None:
        korean = self.translator.language is Language.KOREAN
        self.help_label.setText(
            "프로젝트에서 재사용할 이미지·음원·폰트·가사 파일입니다. "
            "더블클릭하면 캔버스 또는 플레이리스트에 추가됩니다."
            if korean else
            "Reusable images, audio, fonts, and lyrics. Double-click to add an item "
            "to the canvas or playlist."
        )
        self.import_button.setText("콘텐츠 가져오기" if korean else "Import content")
        self.add_button.setText("프로젝트에 추가" if korean else "Add to project")
        self.remove_button.setText("목록에서 제거" if korean else "Remove")
        self.refresh()

    def refresh(self) -> None:
        selected_id = self._selected_id()
        self.list.clear()
        korean = self.translator.language is Language.KOREAN
        labels = {
            "image": "이미지" if korean else "Image",
            "audio": "오디오" if korean else "Audio",
            "font": "폰트" if korean else "Font",
            "lyrics": "가사 / 자막" if korean else "Lyrics / subtitles",
        }
        for content in self.service.items:
            path = Path(content.path)
            available = path.is_file()
            type_label = labels.get(content.media_type, content.media_type)
            extension = path.suffix.removeprefix(".").upper()
            detail = f"{type_label} · {extension}" if extension else type_label
            if not available:
                detail += " · " + ("파일 없음" if korean else "Missing file")
            item = QListWidgetItem(f"{content.name}\n{detail}")
            item.setIcon(self._content_icon(content.media_type, available))
            item.setSizeHint(QSize(0, 54))
            item.setData(Qt.ItemDataRole.UserRole, content.id)
            item.setData(Qt.ItemDataRole.UserRole + 1, content.path)
            item.setData(Qt.ItemDataRole.UserRole + 2, content.media_type)
            accessible_type = type_label + (
                "; 파일 없음" if korean and not available else ""
            )
            if not korean and not available:
                accessible_type += "; missing file"
            item.setData(
                Qt.ItemDataRole.AccessibleTextRole,
                f"{content.name}; {accessible_type}",
            )
            item.setToolTip(
                f"{type_label}\n{content.path}"
                + (
                    ("\n파일을 찾을 수 없습니다." if korean else "\nFile not found.")
                    if not available else ""
                )
            )
            self.list.addItem(item)
            if content.id == selected_id:
                self.list.setCurrentItem(item)
        self._update_buttons()

    def _content_icon(self, media_type: str, available: bool) -> QIcon:
        """Create a crisp, theme-safe icon for a project content category."""
        colors = {
            "image": QColor("#36A2EB"),
            "audio": QColor("#8B5CF6"),
            "font": QColor("#F59E0B"),
            "lyrics": QColor("#14B8A6"),
        }
        accent = colors.get(media_type, QColor("#64748B"))
        if not available:
            accent = QColor("#E05252")

        pixmap = QPixmap(40, 40)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        background = QColor(accent)
        background.setAlpha(42)
        painter.setPen(QPen(accent, 1.4))
        painter.setBrush(background)
        painter.drawRoundedRect(QRectF(1.5, 1.5, 37.0, 37.0), 9.0, 9.0)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(
            accent, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
        ))

        if media_type == "image":
            painter.drawRoundedRect(QRectF(9.0, 10.0, 22.0, 20.0), 3.0, 3.0)
            painter.drawEllipse(QPointF(25.0, 15.0), 2.2, 2.2)
            painter.drawPolyline(QPolygonF([
                QPointF(11.0, 27.0), QPointF(17.0, 20.0),
                QPointF(21.0, 24.0), QPointF(24.0, 21.0),
                QPointF(30.0, 27.0),
            ]))
        elif media_type == "audio":
            painter.drawLine(QPointF(19.0, 12.0), QPointF(19.0, 26.0))
            painter.drawLine(QPointF(19.0, 12.0), QPointF(29.0, 10.0))
            painter.drawLine(QPointF(29.0, 10.0), QPointF(29.0, 23.0))
            painter.setBrush(accent)
            painter.drawEllipse(QPointF(15.5, 27.0), 4.0, 3.2)
            painter.drawEllipse(QPointF(25.5, 24.0), 4.0, 3.2)
        elif media_type == "font":
            font = QFont(self.font())
            font.setBold(True)
            font.setPixelSize(16)
            painter.setFont(font)
            painter.drawText(QRectF(5.0, 7.0, 30.0, 27.0), Qt.AlignmentFlag.AlignCenter, "Aa")
        else:
            for y, width in ((12.0, 19.0), (18.0, 23.0), (24.0, 16.0), (30.0, 21.0)):
                painter.drawLine(QPointF(8.5, y), QPointF(8.5 + width, y))

        if not available:
            painter.setPen(QPen(
                QColor("#FFFFFF"), 1.8, Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
            ))
            painter.setBrush(accent)
            painter.drawEllipse(QPointF(31.0, 9.0), 6.0, 6.0)
            painter.drawLine(QPointF(31.0, 5.7), QPointF(31.0, 9.0))
            painter.drawPoint(QPointF(31.0, 12.0))

        painter.end()
        return QIcon(pixmap)

    def _import_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "콘텐츠 가져오기" if self.translator.language is Language.KOREAN else "Import content",
            "",
            "Supported content (*.jpg *.jpeg *.png *.webp *.svg *.mp3 *.wav *.flac "
            "*.aac *.m4a *.ogg *.ttf *.otf *.woff *.woff2 *.lrc *.srt *.vtt)",
        )
        if paths:
            self.service.add_paths(paths)

    def _selected_id(self) -> str:
        item = self.list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item is not None else ""

    def _add_selected(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self.add_requested.emit(
                str(item.data(Qt.ItemDataRole.UserRole + 1)),
                str(item.data(Qt.ItemDataRole.UserRole + 2)),
            )

    def _remove_selected(self) -> None:
        content_id = self._selected_id()
        if content_id:
            self.service.remove(content_id)

    def _show_context_menu(self, position: QPoint) -> None:
        """Open a localized menu for an item or the list's empty area."""
        item = self.list.itemAt(position)
        if item is not None:
            self.list.setCurrentItem(item)
        menu = self._create_context_menu(item)
        menu.exec(self.list.viewport().mapToGlobal(position))

    def _create_context_menu(self, item: QListWidgetItem | None) -> QMenu:
        """Build the content menu separately so its availability is testable."""
        korean = self.translator.language is Language.KOREAN
        menu = QMenu(self)
        if item is not None:
            add_action = menu.addAction(
                "프로젝트에 추가" if korean else "Add to project",
                self._add_selected,
            )
            add_action.setData("add")
            available = Path(str(item.data(Qt.ItemDataRole.UserRole + 1))).is_file()
            add_action.setEnabled(available)

            remove_action = menu.addAction(
                "목록에서 제거" if korean else "Remove from list",
                self._remove_selected,
            )
            remove_action.setData("remove")

            info_action = menu.addAction(
                "정보" if korean else "Information",
                self._show_selected_information,
            )
            info_action.setData("information")
            menu.addSeparator()

        import_action = menu.addAction(
            "콘텐츠 가져오기…" if korean else "Import content…",
            self._import_files,
        )
        import_action.setData("import")
        return menu

    def _show_selected_information(self) -> None:
        """Show useful, read-only file details for the selected content."""
        item = self.list.currentItem()
        if item is None:
            return
        korean = self.translator.language is Language.KOREAN
        path = Path(str(item.data(Qt.ItemDataRole.UserRole + 1)))
        media_type = str(item.data(Qt.ItemDataRole.UserRole + 2))
        type_names = {
            "image": "이미지" if korean else "Image",
            "audio": "오디오" if korean else "Audio",
            "font": "폰트" if korean else "Font",
            "lyrics": "가사 / 자막" if korean else "Lyrics / subtitles",
        }
        available = path.is_file()
        size = self._format_file_size(path.stat().st_size) if available else "-"
        lines = (
            [
                f"이름: {path.stem}",
                f"유형: {type_names.get(media_type, media_type)}",
                f"상태: {'사용 가능' if available else '파일 없음'}",
                f"크기: {size}",
                f"경로: {path}",
            ]
            if korean else
            [
                f"Name: {path.stem}",
                f"Type: {type_names.get(media_type, media_type)}",
                f"Status: {'Available' if available else 'Missing file'}",
                f"Size: {size}",
                f"Path: {path}",
            ]
        )
        QMessageBox.information(
            self,
            "콘텐츠 정보" if korean else "Content information",
            "\n".join(lines),
        )

    @staticmethod
    def _format_file_size(size: int) -> str:
        value = float(max(0, size))
        units = ("B", "KB", "MB", "GB")
        unit = units[0]
        for unit in units:
            if value < 1024.0 or unit == units[-1]:
                break
            value /= 1024.0
        return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"

    def _update_buttons(self) -> None:
        enabled = self.list.currentItem() is not None
        self.add_button.setEnabled(enabled)
        self.remove_button.setEnabled(enabled)
