"""Review the songs an M3U8 playlist will add before they join the Playlist."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.preview.album_art import extract_track_cover
from app.preview.text_template import format_timestamp
from app.services.playlist_service import AudioImportCandidate
from app.utils.i18n import Language, Translator

_ART_SIZE = 44


class M3uImportDialog(QDialog):
    """Read-only list (art, title, artist, album) of the songs a playlist file adds."""

    def __init__(
        self,
        candidates: list[AudioImportCandidate],
        playlist_path: Path,
        skipped: list[str],
        translator: Translator,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.candidates = candidates
        self.playlist_path = playlist_path
        self.skipped = skipped
        self.translator = translator
        self.setMinimumSize(760, 420)
        self.resize(900, min(720, 300 + len(candidates) * (_ART_SIZE + 8)))

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)
        self.intro = QLabel()
        self.intro.setObjectName("panelTitle")
        self.intro.setWordWrap(True)
        root.addWidget(self.intro)

        self.table = QTableWidget(len(candidates), 5)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setIconSize(QSize(_ART_SIZE, _ART_SIZE))
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(_ART_SIZE + 8)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, _ART_SIZE + 16)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(column, 190)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.table, 1)

        self.note = QLabel()
        self.note.setObjectName("mutedLabel")
        self.note.setWordWrap(True)
        root.addWidget(self.note)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

        self._populate()
        translator.language_changed.connect(self.retranslate)
        self.retranslate()

    def _populate(self) -> None:
        fallback = QIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        for row, candidate in enumerate(self.candidates):
            track = candidate.track
            cover = extract_track_cover(track.file_path, track.cover_path)
            art = QTableWidgetItem()
            art.setIcon(
                QIcon(cover.scaled(
                    _ART_SIZE, _ART_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )) if not cover.isNull() else fallback
            )
            self.table.setItem(row, 0, art)
            for column, value in enumerate(
                (track.title, track.artist, track.album,
                 format_timestamp(track.duration_seconds)), start=1,
            ):
                item = QTableWidgetItem(value)
                item.setToolTip(track.file_path if column == 1 else value)
                self.table.setItem(row, column, item)

    def retranslate(self) -> None:
        korean = self.translator.language is Language.KOREAN
        name = self.playlist_path.name
        count = len(self.candidates)
        self.setWindowTitle("M3U8 플레이리스트 가져오기" if korean else "Import M3U8 playlist")
        self.intro.setText(
            f"'{name}'에서 {count}곡을 플레이리스트에 추가합니다."
            if korean else f"Add {count} song(s) from '{name}' to the Playlist."
        )
        self.table.setHorizontalHeaderLabels(
            ["아트", "제목", "아티스트", "앨범", "길이"]
            if korean else ["Art", "Title", "Artist", "Album", "Length"]
        )
        note = (
            "곡은 플레이리스트 끝에 목록 순서대로 추가되고 프로젝트 콘텐츠에 등록됩니다. "
            "M3U8 파일 자체는 프로젝트에 추가되지 않습니다."
            if korean else
            "Songs are appended to the Playlist in list order and registered as project "
            "content. The M3U8 file itself is not added to the project."
        )
        if self.skipped:
            note += (
                f"\n찾을 수 없거나 지원하지 않는 항목 {len(self.skipped)}개는 건너뜁니다."
                if korean else
                f"\nSkipping missing or unsupported entries: {len(self.skipped)}."
            )
        self.note.setText(note)
        self.note.setToolTip("\n".join(self.skipped[:30]))
        ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button is not None:
            ok_button.setText("플레이리스트에 추가" if korean else "Add to Playlist")
        cancel_button = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_button is not None:
            cancel_button.setText("취소" if korean else "Cancel")
