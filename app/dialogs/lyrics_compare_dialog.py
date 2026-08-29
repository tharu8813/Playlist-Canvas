"""Side-by-side confirmation before replacing lyrics attached to a track."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from app.services.lyrics_service import LyricsService
from app.utils.i18n import Language, Translator


class LyricsCompareDialog(QDialog):
    """Let the user compare current and incoming timed lyrics before replacement."""

    def __init__(
        self, current_cues: list[dict[str, Any]], incoming_cues: list[dict[str, Any]],
        current_path: str, incoming_path: str, translator: Translator,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.replace_requested = False
        korean = translator.language is Language.KOREAN
        self.setWindowTitle("가사 비교 및 교체" if korean else "Compare and replace lyrics")
        self.setModal(True)
        self.resize(900, 590)

        root = QVBoxLayout(self)
        title = QLabel(
            "이 곡에는 이미 가사가 적용되어 있습니다. 두 가사를 비교한 뒤 사용할 항목을 선택하세요."
            if korean else
            "This track already has lyrics. Compare both versions before choosing which one to use."
        )
        title.setWordWrap(True)
        root.addWidget(title)

        columns = QHBoxLayout()
        columns.setSpacing(12)
        columns.addLayout(self._column(
            "현재 가사" if korean else "Current lyrics", current_path,
            current_cues, korean,
        ), 1)
        columns.addLayout(self._column(
            "추가할 가사" if korean else "Incoming lyrics", incoming_path,
            incoming_cues, korean,
        ), 1)
        root.addLayout(columns, 1)

        buttons = QDialogButtonBox()
        keep = buttons.addButton(
            "현재 가사 유지" if korean else "Keep current",
            QDialogButtonBox.ButtonRole.RejectRole,
        )
        replace = buttons.addButton(
            "새 가사 적용" if korean else "Use new lyrics",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        replace.setDefault(True)
        replace.setProperty("primary", True)
        keep.clicked.connect(self.reject)
        replace.clicked.connect(self._accept_replacement)
        root.addWidget(buttons)

    @staticmethod
    def _column(
        heading: str, path: str, cues: list[dict[str, Any]], korean: bool,
    ) -> QVBoxLayout:
        layout = QVBoxLayout()
        label = QLabel(f"<b>{heading}</b> · {len(cues)}" + ("개 구간" if korean else " cues"))
        layout.addWidget(label)
        path_label = QLabel(Path(path).name if path else ("프로젝트 내장 가사" if korean else "Embedded lyrics"))
        path_label.setObjectName("mutedLabel")
        path_label.setToolTip(path)
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(path_label)
        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        preview.setPlainText("\n".join(
            f"[{LyricsService.lrc_timestamp(float(cue.get('start', 0.0)))}] "
            f"{LyricsService.decode_line_breaks(cue.get('text', ''))}"
            for cue in cues
        ))
        layout.addWidget(preview, 1)
        return layout

    def _accept_replacement(self) -> None:
        self.replace_requested = True
        self.accept()
