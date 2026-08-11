"""Dialog that lets users relink missing project media before loading."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QHBoxLayout,
)

from app.services.project_media_service import MissingMedia
from app.utils.i18n import Language, Translator


class MissingMediaDialog(QDialog):
    """Displays every unresolved project asset and allows each path to be replaced."""

    def __init__(self, media: list[MissingMedia], translator: Translator,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.media = media
        self.translator = translator
        self.setMinimumSize(760, 360)
        self.heading = QLabel()
        self.heading.setObjectName("panelTitle")
        self.description = QLabel()
        self.description.setObjectName("mutedLabel")
        self.description.setWordWrap(True)
        self.table = QTableWidget(len(media), 4)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnWidth(0, 95)
        self.table.setColumnWidth(1, 180)
        self.table.setColumnWidth(2, 330)
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        for row, entry in enumerate(media):
            self._populate_row(row, entry)
        layout = QVBoxLayout(self)
        layout.addWidget(self.heading)
        layout.addWidget(self.description)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.button_box)
        translator.language_changed.connect(self.retranslate)
        self.retranslate()

    def _populate_row(self, row: int, entry: MissingMedia) -> None:
        kind = QTableWidgetItem()
        name = QTableWidgetItem(entry.display_name)
        path = QTableWidgetItem(entry.original_path)
        path.setToolTip(entry.original_path)
        self.table.setItem(row, 0, kind)
        self.table.setItem(row, 1, name)
        self.table.setItem(row, 2, path)
        browse_button = QPushButton()
        browse_button.clicked.connect(lambda _checked=False, index=row: self._choose_file(index))
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(browse_button)
        self.table.setCellWidget(row, 3, container)

    def _choose_file(self, row: int) -> None:
        entry = self.media[row]
        filter_text = (
            "Audio (*.mp3 *.wav *.flac *.aac *.m4a *.ogg)" if entry.is_audio else
            "Fonts (*.ttf *.otf)" if entry.is_font else
            "Lyrics (*.lrc *.srt *.vtt)" if entry.is_lyrics else
            "Supported content (*.*)" if entry.library_type else
            "Images (*.jpg *.jpeg *.png *.webp *.svg)"
        )
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "음원 찾기" if entry.is_audio and self._korean else
            "글꼴 찾기" if entry.is_font and self._korean else
            "가사 찾기" if entry.is_lyrics and self._korean else
            "콘텐츠 찾기" if entry.library_type and self._korean else
            "이미지 찾기" if self._korean else
            "Locate audio" if entry.is_audio else "Locate font" if entry.is_font else
            "Locate lyrics" if entry.is_lyrics else "Locate content" if entry.library_type else
            "Locate image",
            str(Path(entry.original_path).parent),
            filter_text,
        )
        if selected:
            entry.replacement_path = selected
            item = self.table.item(row, 2)
            item.setText(selected)
            item.setToolTip(selected)

    @property
    def _korean(self) -> bool:
        return self.translator.language is Language.KOREAN

    def retranslate(self) -> None:
        """Refresh all labels without losing paths selected by the user."""
        korean = self._korean
        self.setWindowTitle("누락된 미디어" if korean else "Missing media")
        self.heading.setText("프로젝트에서 찾을 수 없는 파일" if korean else "Files missing from this project")
        self.description.setText(
            "각 항목의 새 경로를 지정하세요. 경로를 지정하지 않고 계속하면 이미지 소스는 비워지고, 음원 트랙은 비활성화됩니다."
            if korean else
            "Choose a replacement for each item. Continuing without one clears image sources and disables audio tracks."
        )
        self.table.setHorizontalHeaderLabels(
            ["종류", "이름", "저장된 경로", "동작"]
            if korean else ["Type", "Name", "Saved path", "Action"]
        )
        for row, entry in enumerate(self.media):
            label = (
                "음원" if korean and entry.is_audio else
                "글꼴" if korean and entry.is_font else
                "가사" if korean and entry.is_lyrics else
                "콘텐츠" if korean and entry.library_type else
                "이미지" if korean else
                "Audio" if entry.is_audio else "Font" if entry.is_font else
                "Lyrics" if entry.is_lyrics else "Content" if entry.library_type else "Image"
            )
            self.table.item(row, 0).setText(label)
            button = self.table.cellWidget(row, 3).findChild(QPushButton)
            button.setText("찾아보기" if korean else "Browse")
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setText(
            "계속" if korean else "Continue"
        )
        self.button_box.button(QDialogButtonBox.StandardButton.Cancel).setText(
            "취소" if korean else "Cancel"
        )
