"""Application-modal progress dialog for managed FFmpeg installation."""

from __future__ import annotations

from time import monotonic

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class FFmpegInstallProgressDialog(QDialog):
    """Show live download, verification, extraction, and installation progress."""

    cancel_requested = Signal()

    def __init__(self, korean: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._korean = korean
        self._started_at = monotonic()
        self._cancelling = False
        self._allow_close = False
        self._last_log = ""

        self.setModal(True)
        # setModal(True) selects WindowModal for a parented dialog, therefore
        # apply ApplicationModal afterwards to lock every application window.
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setMinimumSize(580, 370)
        self.resize(620, 410)
        self.setWindowTitle(
            "FFmpeg 다운로드 및 설치" if korean else "Download and install FFmpeg"
        )

        self.stage_label = QLabel(
            "다운로드 준비 중" if korean else "Preparing download"
        )
        self.stage_label.setObjectName("panelTitle")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        self.percent_label = QLabel("…")
        self.percent_label.setObjectName("panelTitle")
        self.detail_label = QLabel(
            "최신 FFmpeg 배포 정보를 확인하고 있습니다."
            if korean else "Checking the latest FFmpeg release."
        )
        self.detail_label.setObjectName("mutedLabel")
        self.detail_label.setWordWrap(True)
        self.time_label = QLabel(
            "경과 00:00 · 남은 시간 계산 중"
            if korean else "Elapsed 00:00 · Calculating remaining time"
        )
        self.time_label.setObjectName("mutedLabel")
        self.log_heading = QLabel("진행 상황" if korean else "Progress details")
        self.log_heading.setObjectName("panelTitle")
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.document().setMaximumBlockCount(200)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.cancel_button = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        self.cancel_button.setText("취소" if korean else "Cancel")
        self.cancel_button.clicked.connect(self.request_cancel)

        progress_row = QHBoxLayout()
        progress_row.addWidget(self.progress_bar, 1)
        progress_row.addWidget(self.percent_label)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(self.stage_label)
        layout.addLayout(progress_row)
        layout.addWidget(self.detail_label)
        layout.addWidget(self.time_label)
        layout.addSpacing(4)
        layout.addWidget(self.log_heading)
        layout.addWidget(self.log_output, 1)
        layout.addWidget(self.buttons)

    def set_busy(self, stage: str, message: str) -> None:
        """Show an indeterminate initial network operation."""
        self.stage_label.setText(self._stage_text(stage))
        self.progress_bar.setRange(0, 0)
        self.percent_label.setText("…")
        self._set_detail(message)

    def update_progress(self, stage: str, fraction: float, message: str) -> None:
        """Update the measurable progress, detail text, log, and estimated time."""
        fraction = max(0.0, min(1.0, fraction))
        percent = round(fraction * 100)
        elapsed = monotonic() - self._started_at
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(percent)
        self.percent_label.setText(f"{percent}%")
        self.stage_label.setText(self._stage_text(stage))
        self._set_detail(message)
        if fraction > 0.02:
            remaining = elapsed * (1.0 - fraction) / fraction
            self.time_label.setText(
                f"경과 {self._format_time(elapsed)} · 남은 시간 약 {self._format_time(remaining)}"
                if self._korean else
                f"Elapsed {self._format_time(elapsed)} · About {self._format_time(remaining)} remaining"
            )
        else:
            self.time_label.setText(
                f"경과 {self._format_time(elapsed)} · 남은 시간 계산 중"
                if self._korean else
                f"Elapsed {self._format_time(elapsed)} · Calculating remaining time"
            )

    def request_cancel(self) -> bool:
        """Ask once before cancelling and keep the window locked until cleanup finishes."""
        if self._cancelling:
            return False
        response = QMessageBox.question(
            self,
            "FFmpeg 설치 취소" if self._korean else "Cancel FFmpeg installation",
            (
                "FFmpeg 다운로드 및 설치를 취소하시겠습니까?\n"
                "받은 임시 파일은 안전하게 삭제됩니다."
                if self._korean else
                "Cancel the FFmpeg download and installation?\n"
                "Downloaded temporary files will be removed safely."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if response != QMessageBox.StandardButton.Yes:
            return False
        self._cancelling = True
        self.cancel_button.setEnabled(False)
        self.stage_label.setText(
            "설치를 안전하게 취소하는 중…"
            if self._korean else "Cancelling installation safely…"
        )
        self.detail_label.setText(
            "현재 네트워크 또는 파일 작업이 끝나는 즉시 정리합니다."
            if self._korean else
            "Cleanup will begin as soon as the current network or file operation stops."
        )
        self.log_output.append(
            "취소를 요청했습니다." if self._korean else "Cancellation requested."
        )
        self.cancel_requested.emit()
        return True

    def complete(self, accepted: bool) -> None:
        """Allow the worker's terminal state to close the otherwise locked dialog."""
        self._allow_close = True
        if accepted:
            self.accept()
        else:
            self.reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._allow_close:
            event.accept()
            return
        if not self._cancelling:
            self.request_cancel()
        event.ignore()

    def _set_detail(self, message: str) -> None:
        detail = self._detail_text(message)
        self.detail_label.setText(detail)
        if detail and detail != self._last_log:
            self.log_output.append(detail)
            self._last_log = detail

    def _stage_text(self, stage: str) -> str:
        if not self._korean:
            return stage
        return {
            "Preparing download": "다운로드 준비 중",
            "Downloading FFmpeg": "FFmpeg 다운로드 중",
            "Extracting": "압축 해제 및 설치 중",
            "Complete": "설치 완료",
        }.get(stage, stage)

    def _detail_text(self, message: str) -> str:
        if not self._korean:
            return message
        translated = {
            "Reading the verified release manifest": "검증된 최신 배포 정보를 확인하고 있습니다.",
            "Using the existing verified FFmpeg version": "이미 설치된 검증된 FFmpeg를 적용하고 있습니다.",
            "Downloading the checksum manifest": "SHA-256 체크섬 정보를 다운로드하고 있습니다.",
            "Checksum found; starting FFmpeg download": "체크섬을 확인했습니다. FFmpeg 다운로드를 시작합니다.",
            "Checksum verified; extracting archive safely": "체크섬 검증을 완료했습니다. 안전하게 압축을 해제합니다.",
            "FFmpeg was installed and verified": "FFmpeg 설치와 실행 검증을 완료했습니다.",
            "GitHub API unavailable; using the official latest release links":
                "GitHub API 대신 공식 최신 배포 주소를 사용합니다.",
        }.get(message)
        if translated is not None:
            return translated
        if message.startswith("Downloaded "):
            return "다운로드됨 · " + message.removeprefix("Downloaded ")
        return message

    @staticmethod
    def _format_time(seconds: float) -> str:
        total = max(0, round(seconds))
        hours, remainder = divmod(total, 3600)
        minutes, seconds_part = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds_part:02d}"
        return f"{minutes:02d}:{seconds_part:02d}"
