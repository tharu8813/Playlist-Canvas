"""Editor for the common ID3 metadata fields stored in MP3 files."""

from __future__ import annotations

from pathlib import Path

from mutagen.id3 import APIC, COMM, ID3, ID3NoHeaderError, TALB, TBPM, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.utils.i18n import Translator
from app.dialogs.help_dialog import install_help_shortcut


class Mp3MetadataEditorDialog(QDialog):
    """Open, edit, and save common ID3 tags in one MP3 file."""

    _FIELDS = (
        ("title", TIT2, "TIT2"),
        ("artist", TPE1, "TPE1"),
        ("album", TALB, "TALB"),
        ("album_artist", TPE2, "TPE2"),
        ("year", TDRC, "TDRC"),
        ("genre", TCON, "TCON"),
        ("track", TRCK, "TRCK"),
        ("disc", TPOS, "TPOS"),
        ("bpm", TBPM, "TBPM"),
    )

    def __init__(
        self, translator: Translator, parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.translator = translator
        install_help_shortcut(self, ("track", "mp3_metadata"), translator=translator)
        self._path: Path | None = None
        self._cover_bytes: bytes | None = None
        self._cover_mime = "image/jpeg"
        self._remove_cover = False
        self._edits = {name: QLineEdit() for name, _, _ in self._FIELDS}
        self._comment = QLineEdit()
        self._cover_preview = QLabel()
        self._cover_preview.setFixedSize(164, 164)
        self._cover_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cover_preview.setObjectName("metadataCover")
        self._status = QLabel()
        self._status.setObjectName("mutedLabel")
        self._status.setWordWrap(True)
        self._open_button = QPushButton()
        self._save_button = QPushButton()
        self._choose_cover_button = QPushButton()
        self._remove_cover_button = QPushButton()
        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self._field_labels: dict[str, QLabel] = {}

        self._build_ui()
        self._open_button.clicked.connect(self._open_file)
        self._save_button.clicked.connect(self._save_file)
        self._choose_cover_button.clicked.connect(self._choose_cover)
        self._remove_cover_button.clicked.connect(self._clear_cover)
        self._buttons.rejected.connect(self.reject)
        translator.language_changed.connect(self.retranslate)
        self.retranslate()
        self._set_editing_enabled(False)

    def _build_ui(self) -> None:
        self.setObjectName("mp3MetadataEditor")
        self.setMinimumSize(720, 540)
        self.resize(820, 660)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(16)
        self._title = QLabel()
        self._title.setObjectName("dialogTitle")
        root.addWidget(self._title)
        self._description = QLabel()
        self._description.setObjectName("mutedLabel")
        self._description.setWordWrap(True)
        root.addWidget(self._description)

        file_row = QHBoxLayout()
        self._file_label = QLineEdit()
        self._file_label.setReadOnly(True)
        file_row.addWidget(self._file_label, 1)
        file_row.addWidget(self._open_button)
        root.addLayout(file_row)

        scroll = QScrollArea()
        scroll.setObjectName("metadataScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        body = QHBoxLayout(content)
        body.setContentsMargins(0, 0, 8, 0)
        body.setSpacing(24)
        body.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)

        cover_column = QVBoxLayout()
        cover_column.setSpacing(10)
        self._cover_title = QLabel()
        self._cover_title.setObjectName("panelTitle")
        cover_column.addWidget(self._cover_title)
        cover_column.addWidget(self._cover_preview)
        cover_column.addWidget(self._choose_cover_button)
        cover_column.addWidget(self._remove_cover_button)
        cover_column.addStretch(1)
        body.addLayout(cover_column)

        tag_column = QVBoxLayout()
        tag_column.setSpacing(16)
        self._tags_group = QGroupBox()
        form = QFormLayout(self._tags_group)
        form.setContentsMargins(4, 16, 4, 4)
        form.setVerticalSpacing(10)
        form.setHorizontalSpacing(16)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        for name, _, _ in self._FIELDS:
            label = QLabel()
            label.setBuddy(self._edits[name])
            self._field_labels[name] = label
            if name in ("title", "artist", "album", "album_artist"):
                form.addRow(label, self._edits[name])
        tag_column.addWidget(self._tags_group)

        self._details_group = QGroupBox()
        details = QGridLayout(self._details_group)
        details.setContentsMargins(4, 16, 4, 4)
        details.setHorizontalSpacing(12)
        details.setVerticalSpacing(8)
        for column, name in enumerate(("year", "track", "disc", "bpm")):
            details.addWidget(self._field_labels[name], 0, column)
            details.addWidget(self._edits[name], 1, column)
            details.setColumnStretch(column, 1)
        details.addWidget(self._field_labels["genre"], 2, 0)
        details.addWidget(self._edits["genre"], 3, 0)
        self._comment_label = QLabel()
        self._comment_label.setBuddy(self._comment)
        details.addWidget(self._comment_label, 2, 1, 1, 3)
        details.addWidget(self._comment, 3, 1, 1, 3)
        tag_column.addWidget(self._details_group)
        tag_column.addStretch(1)
        body.addLayout(tag_column, 1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        self._save_button.setProperty("primary", True)
        self._save_button.setAutoDefault(False)
        self._buttons.addButton(self._save_button, QDialogButtonBox.ButtonRole.ActionRole)
        footer = QHBoxLayout()
        footer.addWidget(self._status, 1)
        footer.addWidget(self._buttons)
        root.addLayout(footer)
        self._open_button.setFocus()

    def _set_editing_enabled(self, enabled: bool) -> None:
        for field in self._edits.values():
            field.setEnabled(enabled)
        self._comment.setEnabled(enabled)
        self._save_button.setEnabled(enabled)
        self._choose_cover_button.setEnabled(enabled)
        self._remove_cover_button.setEnabled(enabled and self._cover_bytes is not None)

    def _open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self._tr("MP3 파일 열기", "Open MP3 file"),
            "",
            self._tr("MP3 파일 (*.mp3)", "MP3 files (*.mp3)"),
        )
        if path:
            self.load_file(Path(path))

    def load_file(self, path: Path) -> None:
        """Load tags from ``path``; errors are shown without changing the editor."""
        if path.suffix.lower() != ".mp3":
            self._show_error(self._tr("MP3 파일만 열 수 있습니다.", "Only MP3 files can be opened."))
            return
        try:
            tags = ID3(str(path))
        except ID3NoHeaderError:
            tags = ID3()
        except (OSError, ValueError) as error:
            self._show_error(str(error))
            return
        self._path = path
        for name, _, key in self._FIELDS:
            value = tags.get(key)
            self._edits[name].setText(self._frame_text(value))
        comments = tags.getall("COMM")
        self._comment.setText(str(comments[0].text[0]) if comments and comments[0].text else "")
        pictures = tags.getall("APIC")
        self._cover_bytes = bytes(pictures[0].data) if pictures else None
        self._cover_mime = pictures[0].mime if pictures else "image/jpeg"
        self._remove_cover = False
        self._update_cover_preview()
        self._file_label.setText(str(path))
        self._file_label.setToolTip(str(path))
        self._set_editing_enabled(True)
        self._status.setText(self._tr("태그를 불러왔습니다.", "Tags loaded."))

    def _save_file(self) -> None:
        if self._path is None:
            return
        try:
            tags = ID3(str(self._path))
        except ID3NoHeaderError:
            tags = ID3()
        except (OSError, ValueError) as error:
            self._show_error(str(error))
            return
        for name, frame_type, key in self._FIELDS:
            tags.delall(key)
            value = self._edits[name].text().strip()
            if value:
                tags.add(frame_type(encoding=3, text=value))
        tags.delall("COMM")
        comment = self._comment.text().strip()
        if comment:
            tags.add(COMM(encoding=3, lang="eng", desc="", text=comment))
        if self._remove_cover:
            tags.delall("APIC")
        elif self._cover_bytes is not None:
            tags.delall("APIC")
            tags.add(APIC(
                encoding=3, mime=self._cover_mime, type=3,
                desc="Cover", data=self._cover_bytes,
            ))
        try:
            tags.save(str(self._path), v2_version=3)
        except (OSError, ValueError) as error:
            self._show_error(str(error))
            return
        self._status.setText(self._tr("MP3 메타데이터를 저장했습니다.", "MP3 metadata saved."))

    def _choose_cover(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self._tr("앨범 커버 선택", "Choose album cover"),
            "",
            self._tr("이미지 (*.jpg *.jpeg *.png)", "Images (*.jpg *.jpeg *.png)"),
        )
        if not path:
            return
        try:
            self._cover_bytes = Path(path).read_bytes()
        except OSError as error:
            self._show_error(str(error))
            return
        self._cover_mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
        self._remove_cover = False
        self._update_cover_preview()

    def _clear_cover(self) -> None:
        self._cover_bytes = None
        self._remove_cover = True
        self._update_cover_preview()

    def _update_cover_preview(self) -> None:
        self._remove_cover_button.setEnabled(self._path is not None and self._cover_bytes is not None)
        if self._cover_bytes is None:
            self._cover_preview.setText(self._tr("커버 없음", "No cover"))
            return
        pixmap = QPixmap()
        if pixmap.loadFromData(self._cover_bytes):
            self._cover_preview.setPixmap(pixmap.scaled(
                self._cover_preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        else:
            self._cover_preview.setText(self._tr("이미지 미리보기 없음", "No preview"))

    @staticmethod
    def _frame_text(frame: object) -> str:
        text = getattr(frame, "text", ())
        return str(text[0]) if text else ""

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, self._tr("메타데이터 편집 오류", "Metadata editor error"), message)

    def _tr(self, korean: str, english: str) -> str:
        return korean if self.translator.is_korean else english

    def retranslate(self) -> None:
        korean = self.translator.is_korean
        self.setWindowTitle("MP3 메타데이터 편집기" if korean else "MP3 Metadata Editor")
        self._title.setText(self.windowTitle())
        self._description.setText(self._tr(
            "MP3 파일을 열어 곡 정보와 앨범 커버를 편집하세요.",
            "Open an MP3 file to edit its track information and album cover.",
        ))
        self._file_label.setPlaceholderText(self._tr("선택한 MP3 파일이 없습니다", "No MP3 file selected"))
        self._file_label.setAccessibleName(self._tr("선택한 MP3 파일", "Selected MP3 file"))
        self._open_button.setText("파일 열기…" if korean else "Open file…")
        self._save_button.setText("저장" if korean else "Save")
        self._choose_cover_button.setText("커버 선택…" if korean else "Choose cover…")
        self._remove_cover_button.setText("커버 제거" if korean else "Remove cover")
        self._buttons.button(QDialogButtonBox.StandardButton.Close).setText(
            "닫기" if korean else "Close"
        )
        labels = (
            ("제목", "Title"), ("아티스트", "Artist"), ("앨범", "Album"),
            ("앨범 아티스트", "Album artist"), ("연도", "Year"), ("장르", "Genre"),
            ("트랙 번호", "Track"), ("디스크 번호", "Disc"), ("BPM", "BPM"),
        )
        for (name, _, _), (ko, en) in zip(self._FIELDS, labels):
            self._field_labels[name].setText(ko if korean else en)
        self._tags_group.setTitle(self._tr("기본 정보", "Track information"))
        self._details_group.setTitle(self._tr("추가 정보", "Additional information"))
        self._cover_title.setText(self._tr("앨범 커버", "Album cover"))
        self._comment_label.setText("설명" if korean else "Comment")
        self._edits["track"].setPlaceholderText("1 / 10")
        self._edits["disc"].setPlaceholderText("1 / 1")
        if self._path is None:
            self._status.setText(self._tr("파일을 열면 편집할 수 있습니다.", "Open a file to start editing."))
        if self._cover_bytes is None:
            self._update_cover_preview()
