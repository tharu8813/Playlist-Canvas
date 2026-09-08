"""Useful next actions after a video export finishes."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.utils.i18n import Language, Translator
from app.services.export_validation_service import ExportValidationResult


class ExportCompleteDialog(QDialog):
    """Confirm the result and keep common post-export actions one click away."""

    def __init__(
        self,
        output_path: str | Path,
        translator: Translator,
        parent: QWidget | None = None,
        validation: ExportValidationResult | None = None,
    ) -> None:
        super().__init__(parent)
        self.output_path = Path(output_path).expanduser().resolve()
        self.translator = translator
        self.export_again_requested = False
        self.setMinimumSize(640, 300)
        self.resize(700, 330)

        korean = translator.language is Language.KOREAN
        self.setWindowTitle("내보내기 완료" if korean else "Export complete")
        title = QLabel("영상 내보내기를 완료했습니다" if korean else "Your video is ready")
        title.setObjectName("dialogTitle")
        subtitle = QLabel(
            "바로 확인하거나 파일을 공유할 준비를 할 수 있습니다."
            if korean else
            "Review the result now or prepare the file for sharing."
        )
        subtitle.setObjectName("mutedLabel")

        result_card = QFrame()
        result_card.setObjectName("infoCallout")
        card_layout = QVBoxLayout(result_card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        file_name = QLabel(self.output_path.name)
        file_name.setObjectName("panelTitle")
        path_label = QLabel(str(self.output_path))
        path_label.setObjectName("mutedLabel")
        path_label.setWordWrap(True)
        path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        size_text = self._file_size_text()
        size_label = QLabel(
            f"파일 크기 · {size_text}" if korean else f"File size · {size_text}"
        )
        size_label.setObjectName("mutedLabel")
        card_layout.addWidget(file_name)
        card_layout.addWidget(path_label)
        card_layout.addWidget(size_label)
        if validation is not None:
            validation_label = QLabel(self._validation_text(validation, korean))
            validation_label.setObjectName("mutedLabel" if validation.passed else "warningLabel")
            validation_label.setWordWrap(True)
            card_layout.addWidget(validation_label)

        self.play_button = QPushButton(
            "영상 재생하기" if korean else "Play video"
        )
        self.play_button.setObjectName("primaryButton")
        self.folder_button = QPushButton(
            "저장 폴더 열기" if korean else "Open export folder"
        )
        self.copy_path_button = QPushButton(
            "경로 복사" if korean else "Copy path"
        )
        self.export_again_button = QPushButton(
            "다시 내보내기" if korean else "Export again"
        )
        self.close_button = QPushButton("닫기" if korean else "Close")

        self.play_button.clicked.connect(self._play_video)
        self.folder_button.clicked.connect(self._open_folder)
        self.copy_path_button.clicked.connect(self._copy_path)
        self.export_again_button.clicked.connect(self._request_export_again)
        self.close_button.clicked.connect(self.accept)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        action_row.addWidget(self.play_button)
        action_row.addWidget(self.folder_button)
        action_row.addWidget(self.copy_path_button)
        action_row.addStretch(1)

        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self.export_again_button)
        bottom_row.addStretch(1)
        bottom_row.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(result_card)
        layout.addLayout(action_row)
        layout.addStretch(1)
        layout.addLayout(bottom_row)

    def _file_size_text(self) -> str:
        try:
            size = self.output_path.stat().st_size
        except OSError:
            return "-"
        units = ("B", "KB", "MB", "GB", "TB")
        value = float(size)
        for unit in units:
            if value < 1024.0 or unit == units[-1]:
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024.0
        return f"{size} B"

    @staticmethod
    def _validation_text(validation: ExportValidationResult, korean: bool) -> str:
        if not validation.available:
            return ("출력 품질 확인을 건너뛰었습니다: " + validation.error
                    if korean else "Output quality check unavailable: " + validation.error)
        prefix = "출력 확인 · " if korean else "Output verified · "
        text = prefix + validation.summary
        if validation.warnings:
            text += ("\n확인 필요: " if korean else "\nCheck: ") + "; ".join(validation.warnings)
        return text

    def _play_video(self) -> None:
        if self.output_path.is_file() and QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(self.output_path))
        ):
            return
        self._show_open_error(
            "영상을 재생할 수 없습니다. 기본 동영상 앱과 파일 위치를 확인해 주세요."
            if self.translator.language is Language.KOREAN else
            "The video could not be opened. Check the file and the default video app."
        )

    def _open_folder(self) -> None:
        directory = self.output_path.parent
        if directory.is_dir() and QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(directory))
        ):
            return
        self._show_open_error(
            "내보내기 폴더를 열 수 없습니다."
            if self.translator.language is Language.KOREAN else
            "The export folder could not be opened."
        )

    def _copy_path(self) -> None:
        QApplication.clipboard().setText(str(self.output_path))
        self.copy_path_button.setText(
            "복사됨" if self.translator.language is Language.KOREAN else "Copied"
        )

    def _request_export_again(self) -> None:
        self.export_again_requested = True
        self.accept()

    def _show_open_error(self, message: str) -> None:
        QMessageBox.warning(
            self,
            "파일 열기 실패"
            if self.translator.language is Language.KOREAN else
            "Could not open file",
            message,
        )
