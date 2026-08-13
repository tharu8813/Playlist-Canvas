"""Detailed report shown when a project cannot be loaded safely."""

from __future__ import annotations

from PySide6.QtCore import Qt
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


class ProjectCrashReportDialog(QDialog):
    """Explain a failed project load while reassuring that the editor survived."""

    def __init__(
        self,
        *,
        project_path: str,
        stage: str,
        exception_type: str,
        exception_message: str,
        cause_type: str,
        cause_message: str,
        guidance: str,
        report_text: str,
        log_path: str,
        korean: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setMinimumSize(760, 520)
        self.resize(820, 580)
        self.setWindowTitle(
            "프로젝트 크래시 보고서" if korean else "Project crash report"
        )

        heading = QLabel(
            "프로젝트를 불러오지 못했습니다."
            if korean else "The project could not be loaded."
        )
        heading.setObjectName("panelTitle")
        description = QLabel(
            "문제가 있는 프로젝트는 적용하지 않았으며, 기존 프로젝트와 프로그램은 계속 사용할 수 있습니다."
            if korean else
            "The problematic project was not applied. Your existing project and the application remain available."
        )
        description.setWordWrap(True)

        form = QFormLayout()

        def add_row(korean_label: str, english_label: str, value: str) -> None:
            label = QLabel(value or "-")
            label.setWordWrap(True)
            label.setTextInteractionFlags(
                label.textInteractionFlags()
                | Qt.TextInteractionFlag.TextSelectableByMouse
            )
            form.addRow(korean_label if korean else english_label, label)

        add_row("프로젝트 파일", "Project file", project_path)
        add_row("실패 단계", "Failed stage", stage)
        add_row("오류 유형", "Exception type", exception_type)
        add_row("오류 메시지", "Exception message", exception_message)
        if cause_type and (cause_type != exception_type or cause_message != exception_message):
            add_row("근본 원인", "Root cause", f"{cause_type}: {cause_message}")
        add_row("권장 조치", "Suggested action", guidance)
        add_row("로그 폴더", "Log folder", log_path)

        details_label = QLabel("상세 보고서" if korean else "Detailed report")
        details_label.setObjectName("sectionTitle")
        self.details = QPlainTextEdit(report_text)
        self.details.setReadOnly(True)
        self.details.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        self.copy_button = QPushButton(
            "보고서 복사" if korean else "Copy report"
        )
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
        layout.addWidget(details_label)
        layout.addWidget(self.details, 1)
        layout.addWidget(self.copy_button)
        layout.addWidget(buttons)
