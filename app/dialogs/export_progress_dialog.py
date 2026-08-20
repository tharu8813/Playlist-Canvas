"""Modeless export progress dialog with clear status, ETA, log, and cancellation."""

from __future__ import annotations

from pathlib import Path
import re
from time import monotonic

from PySide6.QtCore import Signal, Qt
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


class ExportProgressDialog(QDialog):
    """Display a comprehensible in-flight FFmpeg export and allow safe cancellation."""

    cancel_requested = Signal()
    minimize_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setModal(False)
        self.setMinimumSize(600, 420)
        self._started_at = monotonic()
        self._cancelling = False
        self._allow_close = False
        self._last_log = ""
        self._korean = False
        self._cancel_title = "Cancel export"
        self._cancel_message = (
            "Cancel the current export?\n"
            "Prepared temporary frames and the active render will be discarded."
        )
        self._export_track_count = 0
        self._export_duration_seconds = 0.0
        self._export_settings_summary = ""
        self._export_output_path = ""
        layout = QVBoxLayout(self)
        self.export_settings_heading = QLabel("Export settings")
        self.export_settings_heading.setObjectName("panelTitle")
        self.export_settings_label = QLabel()
        self.export_settings_label.setObjectName("infoCallout")
        self.export_settings_label.setWordWrap(True)
        self.export_settings_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.stage_label = QLabel("Preparing export")
        self.stage_label.setObjectName("panelTitle")
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.percent_label = QLabel("0%")
        self.percent_label.setObjectName("panelTitle")
        self.detail_label = QLabel("Preparing temporary files")
        self.detail_label.setObjectName("mutedLabel")
        self.detail_label.setWordWrap(True)
        self.time_label = QLabel("Elapsed 00:00 · Calculating remaining time")
        self.time_label.setObjectName("mutedLabel")
        self.log_heading = QLabel("Activity")
        self.log_heading.setObjectName("panelTitle")
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.document().setMaximumBlockCount(200)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.minimize_button = self.buttons.addButton(
            "Minimize", QDialogButtonBox.ButtonRole.ActionRole,
        )
        self.cancel_button = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        self.minimize_button.clicked.connect(self.minimize_requested.emit)
        self.cancel_button.clicked.connect(self.request_cancel)
        progress_row = QHBoxLayout()
        progress_row.addWidget(self.progress_bar, 1)
        progress_row.addWidget(self.percent_label)
        layout.addWidget(self.export_settings_heading)
        layout.addWidget(self.export_settings_label)
        layout.addWidget(self.stage_label)
        layout.addLayout(progress_row)
        layout.addWidget(self.detail_label)
        layout.addWidget(self.time_label)
        layout.addWidget(self.log_heading)
        layout.addWidget(self.log_output, 1)
        layout.addWidget(self.buttons)
        self.setWindowTitle("Export progress")

    def set_korean(self, korean: bool) -> None:
        """Localize the export-specific labels without changing app-wide language."""
        self._korean = korean
        if korean:
            self._cancel_title = "내보내기 취소"
            self._cancel_message = (
                "현재 내보내기를 취소할까요?\n"
                "준비된 임시 프레임과 진행 중인 렌더링이 중단됩니다."
            )
            self.setWindowTitle("내보내기 진행 상황")
            self.export_settings_heading.setText("내보내기 설정")
            self.log_heading.setText("작업 내역")
            self.minimize_button.setText("최소화")
            self.cancel_button.setText("취소")
            self.stage_label.setText("내보내기 준비")
            self.time_label.setText("경과 00:00 · 남은 시간 계산 중")
        else:
            self._cancel_title = "Cancel export"
            self._cancel_message = (
                "Cancel the current export?\n"
                "Prepared temporary frames and the active render will be discarded."
            )
            self.setWindowTitle("Export progress")
            self.export_settings_heading.setText("Export settings")
            self.log_heading.setText("Activity")
            self.minimize_button.setText("Minimize")
            self.cancel_button.setText("Cancel")
            self.stage_label.setText("Preparing export")
            self.time_label.setText("Elapsed 00:00 · Calculating remaining time")

    def set_cancel_confirmation(self, title: str, message: str) -> None:
        """Customize confirmation text when the dialog tracks a non-export task."""
        self._cancel_title = title
        self._cancel_message = message

        self._refresh_export_details()

    def set_export_details(
        self, track_count: int, duration_seconds: float, summary: str,
        output_path: str | Path | None = None,
    ) -> None:
        """Keep the selected render configuration visible throughout export."""
        self._export_track_count = max(0, int(track_count))
        self._export_duration_seconds = max(0.0, float(duration_seconds))
        self._export_settings_summary = summary.strip()
        self._export_output_path = str(output_path or "").strip()
        self._refresh_export_details()

    def _refresh_export_details(self) -> None:
        """Render the persistent settings card in the active dialog language."""
        if not self._export_settings_summary and not self._export_output_path:
            self.export_settings_label.clear()
            self.export_settings_label.hide()
            return
        prefix = (
            f"곡 {self._export_track_count}개 · 총 재생 시간 {self._format_time(self._export_duration_seconds)}"
            if self._korean else
            f"{self._export_track_count} track(s) · {self._format_time(self._export_duration_seconds)}"
        )
        lines = [prefix]
        if self._export_settings_summary:
            lines.append(self._export_settings_summary)
        if self._export_output_path:
            lines.append(
                f"저장 위치 · {self._export_output_path}"
                if self._korean else f"Output · {self._export_output_path}"
            )
        display = "\n".join(lines)
        self.export_settings_label.setText(display)
        self.export_settings_label.setToolTip(display)
        self.export_settings_label.show()

    def set_busy(self, stage: str, message: str) -> None:
        """Show activity before a measurable FFmpeg progress stream exists."""
        display_message = self._detail_text(message)
        self.stage_label.setText(self._stage_text(stage))
        self.progress_bar.setRange(0, 0)
        self.percent_label.setText("…")
        self.detail_label.setText(display_message)
        self._update_time_label(None)
        if display_message and display_message != self._last_log:
            self.log_output.append(display_message)
            self._last_log = display_message

    def update_progress(self, stage: str, fraction: float, message: str) -> None:
        """Apply worker progress and calculate an approximate time remaining."""
        percent = round(max(0.0, min(1.0, fraction)) * 100)
        elapsed = monotonic() - self._started_at
        display_message = self._detail_text(message)
        if self.progress_bar.maximum() == 0:
            self.progress_bar.setRange(0, 100)
        self.stage_label.setText(self._stage_text(stage))
        self.progress_bar.setValue(percent)
        self.percent_label.setText(f"{percent}%")
        self.detail_label.setText(display_message)
        if display_message and display_message != self._last_log:
            self.log_output.append(display_message)
            self._last_log = display_message
        remaining = elapsed * (1.0 - fraction) / fraction if fraction > 0.02 else None
        self._update_time_label(remaining, elapsed)

    def _update_time_label(
        self, remaining: float | None, elapsed: float | None = None,
    ) -> None:
        """Keep the export ETA in its single dedicated label."""
        elapsed = monotonic() - self._started_at if elapsed is None else elapsed
        if remaining is None:
            self.time_label.setText(
                f"경과 {self._format_time(elapsed)} · 남은 시간 계산 중"
                if self._korean else
                f"Elapsed {self._format_time(elapsed)} · Calculating remaining time"
            )
            return
        self.time_label.setText(
            f"경과 {self._format_time(elapsed)} · 남은 시간 약 {self._format_time(remaining)}"
            if self._korean else
            f"Elapsed {self._format_time(elapsed)} · Remaining ~{self._format_time(remaining)}"
        )

    def request_cancel(self) -> bool:
        """Confirm and emit a cancellation request exactly once."""
        if self._cancelling:
            return False
        response = QMessageBox.question(
            self,
            self._cancel_title,
            self._cancel_message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if response != QMessageBox.StandardButton.Yes:
            return False
        self._cancelling = True
        self.cancel_button.setEnabled(False)
        self.stage_label.setText("내보내기를 안전하게 취소하는 중..." if self._korean else "Cancelling export safely...")
        self.detail_label.setText(
            "현재 프레임 준비 또는 FFmpeg 작업을 안전하게 중단하는 중" if self._korean
            else "Safely stopping the current frame preparation or FFmpeg operation"
        )
        self.log_output.append("취소를 요청했습니다" if self._korean else "Cancellation requested")
        self.cancel_requested.emit()
        return True

    def complete(self, accepted: bool) -> None:
        """Close the dialog after the worker has reached a terminal state."""
        self._allow_close = True
        if accepted:
            self.accept()
        else:
            self.reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Treat a window-close attempt as a safe cancellation request."""
        if self._allow_close:
            event.accept()
            return
        if not self._cancelling:
            self.request_cancel()
        event.ignore()

    @staticmethod
    def _format_time(seconds: float) -> str:
        total = max(0, round(seconds))
        hours, remainder = divmod(total, 3600)
        minutes, seconds_part = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds_part:02d}" if hours else f"{minutes:02d}:{seconds_part:02d}"

    def _stage_text(self, stage: str) -> str:
        """Translate known renderer stages while preserving diagnostic custom stages."""
        if not self._korean:
            return stage
        return {
            "Preparing visual frames": "화면 프레임 준비",
            "Preparing export": "내보내기 준비",
            "Preparing audio": "오디오 준비",
            "Combining audio": "오디오 결합",
            "Preparing visualizers": "비주얼라이저 준비",
            "Preparing visual layers": "시각 레이어 준비",
            "Encoding video": "영상 인코딩",
            "Finalizing export": "내보내기 마무리",
            "Preparing download": "다운로드 준비",
            "Downloading FFmpeg": "FFmpeg 다운로드",
            "Extracting": "압축 해제 및 설치",
            "Complete": "완료",
        }.get(stage, stage)

    def _detail_text(self, message: str) -> str:
        """Translate visualizer progress details while retaining their live numbers."""
        # Visualizer workers can provide a stage-local ETA, while this dialog
        # calculates the overall export ETA. Showing both in different rows was
        # visually inconsistent and the two estimates represented different
        # scopes. Keep all remaining-time text in ``time_label`` only.
        message = re.sub(
            r"\s*·\s*about\s+[0-9:]+\s+remaining\s*$", "", message,
            flags=re.IGNORECASE,
        )
        message = re.sub(r"\s*·\s*약\s+[0-9:]+\s+남음\s*$", "", message)
        if not self._korean:
            return message
        translated = {
            "Preparing temporary files": "임시 파일 준비 중",
            "Concatenating normalized tracks": "정규화된 오디오 트랙 결합 중",
            "Analyzing audio and rendering Python visualizer frames":
                "오디오를 분석하고 비주얼라이저 프레임을 생성하는 중",
            "Rendering Python visualizer frames": "비주얼라이저 프레임 생성 중",
            "Decoding audio for visualizers": "비주얼라이저용 오디오를 분석하는 중",
            "Analyzing visualizer frequency levels": "비주얼라이저 주파수 레벨 분석 중",
            "Analyzing waveform samples": "파형 샘플 분석 중",
            "Analyzing stereo level meter channels": "스테레오 레벨 미터 분석 중",
            "Visualizer frames complete": "비주얼라이저 프레임 준비 완료",
            "Rendering the final video": "최종 영상 렌더링 중",
            "Audio normalization complete": "오디오 정규화 완료",
            "Export completed": "내보내기 완료",
            "Moving the completed video to the selected location":
                "완성된 영상을 선택한 위치로 이동하는 중",
            "Reading the verified release manifest": "검증된 FFmpeg 배포 정보 확인 중",
            "Using the existing verified FFmpeg version": "기존에 검증된 FFmpeg 사용 중",
            "Downloading the checksum manifest": "SHA-256 체크섬 정보 다운로드 중",
            "Checksum found; starting FFmpeg download":
                "체크섬 확인 완료 · FFmpeg 다운로드 시작",
            "Checksum verified; extracting archive safely":
                "체크섬 검증 완료 · 안전하게 압축 해제 중",
            "FFmpeg was installed and verified": "FFmpeg 설치 및 검증 완료",
            "GitHub API unavailable; using the official latest release links":
                "GitHub API를 사용할 수 없어 공식 최신 배포 주소를 사용합니다",
        }.get(message)
        if translated is not None:
            return translated
        if message.startswith("Preparing visualizer layer "):
            return message.replace(
                "Preparing visualizer layer ", "비주얼라이저 레이어 준비 중 ", 1
            )
        if message.startswith("Visualizer "):
            return (
                message.replace("Visualizer ", "비주얼라이저 ", 1)
                .replace(" · frame ", " · 프레임 ")
            )
        if message.startswith("Normalizing audio "):
            return (
                message.replace("Normalizing audio ", "오디오 정규화 중 · ", 1)
                .replace(" complete", " 완료")
                .replace(" total", " 전체")
            )
        if message.startswith("Normalizing "):
            return "오디오 정규화 중 · " + message.removeprefix("Normalizing ")
        if message.startswith("Combining audio "):
            return message.replace("Combining audio ", "오디오 결합 중 · ", 1)
        if message.startswith("Creating silence "):
            return (
                message.replace("Creating silence ", "무음 구간 생성 중 · ", 1)
                .replace(" total", " 전체")
            )
        if message.startswith("Validating visual frames "):
            return message.replace("Validating visual frames ", "화면 프레임 검사 중 · ", 1)
        if message.startswith("Preparing visual layers "):
            return message.replace("Preparing visual layers ", "시각 레이어 준비 중 · ", 1)
        if message.startswith("Preparing visual layer "):
            return (
                message.replace("Preparing visual layer ", "시각 레이어 준비 중 · ", 1)
                .replace(" · frame ", " · 프레임 ")
            )
        if message.startswith("Checking visual layer "):
            return (
                message.replace("Checking visual layer ", "시각 레이어 검사 중 · ", 1)
                .replace(" · frame ", " · 프레임 ")
            )
        if message.startswith("Inserted ") and message.endswith(" of silence"):
            duration = message.removeprefix("Inserted ").removesuffix(" of silence")
            return f"무음 구간 {duration} 추가"
        if message.startswith("Encoding ") and " / " in message:
            return message.replace("Encoding ", "영상 인코딩 중 · ", 1)
        if message.startswith("Downloaded "):
            return message.replace("Downloaded ", "다운로드됨 · ", 1)
        return message
