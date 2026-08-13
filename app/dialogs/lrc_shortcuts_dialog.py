"""Keyboard shortcut reference dedicated to the LRC generator."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHeaderView, QLabel, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.utils.i18n import Language, Translator


class LrcShortcutsDialog(QDialog):
    """Display only shortcuts that are active in the LRC timing workflow."""

    def __init__(self, translator: Translator, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.translator = translator
        self.setMinimumSize(560, 560)
        self.intro = QLabel()
        self.intro.setObjectName("mutedLabel")
        self.intro.setWordWrap(True)
        self.table = QTableWidget(0, 2)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        buttons.accepted.connect(self.close)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(self.intro)
        layout.addWidget(self.table, 1)
        layout.addWidget(buttons)
        translator.language_changed.connect(self.retranslate)
        self.retranslate()

    def retranslate(self) -> None:
        korean = self.translator.language is Language.KOREAN
        self.setWindowTitle("LRC 편집기 단축키" if korean else "LRC Editor Shortcuts")
        self.intro.setText(
            "가사 입력란에 커서가 있을 때는 Space와 실행 취소가 일반 텍스트 편집에 사용됩니다."
            if korean else
            "While a lyric text field has focus, Space and Undo keep their normal text-editing behavior."
        )
        self.table.setHorizontalHeaderLabels(["키" if korean else "Key", "동작" if korean else "Action"])
        entries = [
            ("Space", "현재 가사 줄에 재생 시간 기록" if korean else "Record the playback time for the current lyric line"),
            ("Ctrl+Z", "마지막 타이밍 기록 취소" if korean else "Undo the last timing change"),
            ("Ctrl+Y / Ctrl+Shift+Z", "취소한 타이밍 다시 실행" if korean else "Redo the last undone timing change"),
            ("Ctrl+S", "현재 기록을 LRC 파일로 저장" if korean else "Save current timing as an LRC file"),
            ("F1", "이 단축키 안내 창 열기" if korean else "Open this shortcut reference"),
            ("Ctrl+Space", "오디오 재생 또는 일시정지" if korean else "Play or pause audio"),
            ("← / →", "재생 위치를 1초 앞뒤로 이동" if korean else "Seek backward or forward by one second"),
            ("F2", "선택한 가사 내용 편집" if korean else "Edit the selected lyric"),
            ("Delete", "확인 후 선택한 가사와 타이밍 삭제" if korean else "Delete the selected lyric and timing after confirmation"),
        ]
        self.table.setRowCount(len(entries))
        for row, (key_text, action) in enumerate(entries):
            key_item = QTableWidgetItem(key_text)
            key_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, key_item)
            self.table.setItem(row, 1, QTableWidgetItem(action))
            self.table.setRowHeight(row, 40)
