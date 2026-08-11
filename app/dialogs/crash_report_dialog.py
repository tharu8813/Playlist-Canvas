"""Detailed but user-safe unexpected-crash report dialog."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class CrashReportDialog(QDialog):
    """Shows the exception summary, traceback, and persistent log location."""

    def __init__(self, exception_type: str, exception_message: str, traceback_text: str,
                 log_path: str | None, korean: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setMinimumSize(680, 440)
        self.setWindowTitle("크래시 보고서" if korean else "Crash report")
        heading = QLabel("프로그램 오류가 발생했습니다." if korean else "The application encountered an error.")
        heading.setObjectName("panelTitle")
        description = QLabel(
            "오류 정보는 로그에 저장되었습니다. 아래 내용을 복사해 지원 요청에 첨부할 수 있습니다."
            if korean else
            "The error details were saved to the log. You can copy the information below for support."
        )
        description.setWordWrap(True)
        form = QFormLayout()
        form.addRow("오류 유형" if korean else "Exception type", QLabel(exception_type))
        message = QLabel(exception_message or ("메시지 없음" if korean else "No message"))
        message.setWordWrap(True)
        form.addRow("오류 메시지" if korean else "Message", message)
        location = QLabel(log_path or ("로그 파일을 만들 수 없습니다." if korean else "Log file unavailable."))
        location.setWordWrap(True)
        form.addRow("로그 파일" if korean else "Log file", location)
        self.details = QPlainTextEdit(traceback_text)
        self.details.setReadOnly(True)
        self.copy_button = QPushButton("세부 정보 복사" if korean else "Copy details")
        self.copy_button.clicked.connect(
            lambda: QApplication.clipboard().setText(self.details.toPlainText())
        )
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(description)
        layout.addLayout(form)
        layout.addWidget(self.details, 1)
        layout.addWidget(self.copy_button)
        layout.addWidget(buttons)
