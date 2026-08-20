"""Expanded multiline editor for text-source content."""

from __future__ import annotations

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout, QWidget,
)

from app.utils.i18n import Translator
from app.widgets.token_text_editor import TokenPlainTextEdit


class TextEditorDialog(QDialog):
    def __init__(self, text: str, translator: Translator,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.translator = translator
        korean = translator.language.value == "ko"
        self.setWindowTitle("텍스트 확장 입력" if korean else "Expanded text editor")
        self.setModal(True)
        self.resize(680, 460)
        self.setMinimumSize(480, 320)

        layout = QVBoxLayout(self)
        heading = QLabel(
            "긴 문장을 여러 줄로 편집할 수 있습니다. %를 입력하면 동적 토큰을 선택할 수 있습니다."
            if korean else
            "Edit long or multiline text here. Type % to choose a dynamic token."
        )
        heading.setWordWrap(True)
        heading.setObjectName("mutedLabel")
        layout.addWidget(heading)

        self.editor = TokenPlainTextEdit(translator)
        self.editor.setObjectName("expandedTextEditor")
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.editor.setPlainText(text)
        self.editor.moveCursor(QTextCursor.MoveOperation.End)
        self.editor.setAccessibleName("텍스트 내용" if korean else "Text content")
        layout.addWidget(self.editor, 1)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def text(self) -> str:
        return self.editor.toPlainText()
