"""Per-export video quality dialog that does not overwrite defaults by default."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.services.app_settings_service import (
    AUDIO_BITRATES,
    ENCODING_PRESETS,
    RESOLUTIONS,
    VIDEO_ENCODERS,
    AppSettings,
)
from app.utils.i18n import Language, Translator


class ExportSettingsDialog(QDialog):
    """Collect render quality and the final output file in one place."""

    def __init__(self, settings: AppSettings, track_count: int, duration_seconds: float,
                 translator: Translator, default_output_path: str | Path,
                 parent: QWidget | None = None,
                 canvas_size: tuple[int, int] | None = None) -> None:
        super().__init__(parent)
        self.translator = translator
        self._base_settings = settings
        self.canvas_size = canvas_size
        self.setMinimumWidth(480)
        self.resolution_combo = QComboBox()
        self._populate_resolutions(settings)
        self.fps_combo = QComboBox()
        self.fps_combo.addItems(["24", "25", "30", "50", "60"])
        self.fps_combo.setCurrentText(str(settings.fps))
        self.codec_combo = QComboBox()
        for label, codec in VIDEO_ENCODERS.items():
            self.codec_combo.addItem(label, codec)
        self.codec_combo.setCurrentIndex(max(0, self.codec_combo.findData(settings.video_codec)))
        self.crf_spin = QSpinBox()
        self.crf_spin.setRange(0, 51)
        self.crf_spin.setValue(settings.crf)
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(ENCODING_PRESETS)
        self.preset_combo.setCurrentText(settings.preset)
        self.audio_bitrate_combo = QComboBox()
        self.audio_bitrate_combo.addItems(AUDIO_BITRATES)
        self.audio_bitrate_combo.setCurrentText(settings.audio_bitrate)
        self.output_path_edit = QLineEdit(str(default_output_path))
        self.output_path_edit.setMinimumWidth(330)
        self.output_browse_button = QPushButton()
        self.output_browse_button.clicked.connect(self._browse_output_path)
        self.summary_label = QLabel()
        self.summary_label.setObjectName("mutedLabel")
        self.summary_label.setWordWrap(True)
        self.save_default_check = QCheckBox()
        self.optimize_button = QPushButton()
        self.optimize_button.clicked.connect(self._apply_balanced_optimization)
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        self.button_box.accepted.connect(self._accept_if_valid)
        self.button_box.rejected.connect(self.reject)

        render_group = QGroupBox()
        render_form = QFormLayout(render_group)
        self.resolution_label = QLabel()
        self.fps_label = QLabel()
        self.codec_label = QLabel()
        self.crf_label = QLabel()
        self.preset_label = QLabel()
        self.audio_label = QLabel()
        render_form.addRow(self.resolution_label, self.resolution_combo)
        render_form.addRow(self.fps_label, self.fps_combo)
        render_form.addRow(self.codec_label, self.codec_combo)
        render_form.addRow(self.crf_label, self.crf_spin)
        render_form.addRow(self.preset_label, self.preset_combo)
        render_form.addRow(self.audio_label, self.audio_bitrate_combo)
        output_group = QGroupBox()
        output_layout = QHBoxLayout(output_group)
        output_layout.addWidget(self.output_path_edit, 1)
        output_layout.addWidget(self.output_browse_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.summary_label)
        layout.addWidget(render_group)
        layout.addWidget(output_group)
        layout.addWidget(self.optimize_button)
        layout.addWidget(self.save_default_check)
        layout.addWidget(self.button_box)
        self.render_group = render_group
        self.output_group = output_group
        self.track_count = track_count
        self.duration_seconds = duration_seconds
        self.retranslate()

    @property
    def app_settings(self) -> AppSettings:
        """Return this export's settings while retaining app-wide path defaults."""
        resolution_data = self.resolution_combo.currentData()
        if not isinstance(resolution_data, tuple) or len(resolution_data) != 3:
            width, height = self._base_settings.resolution
            base_name = self._base_settings.resolution_name
        else:
            width, height, base_name = resolution_data
        return AppSettings(
            ffmpeg_path=self._base_settings.ffmpeg_path,
            output_directory=str(self.output_path.parent),
            resolution_name=str(base_name),
            fps=int(self.fps_combo.currentText()),
            video_codec=str(self.codec_combo.currentData()),
            crf=self.crf_spin.value(),
            preset=self.preset_combo.currentText(),
            audio_bitrate=self.audio_bitrate_combo.currentText(),
            smooth_scrolling=self._base_settings.smooth_scrolling,
            smooth_scroll_duration_ms=self._base_settings.smooth_scroll_duration_ms,
            render_width=int(width),
            render_height=int(height),
        )

    def _populate_resolutions(self, settings: AppSettings) -> None:
        """Offer quality tiers that always retain the active project's ratio."""
        if self.canvas_size is None:
            for name, size in RESOLUTIONS.items():
                self.resolution_combo.addItem(name, (*size, name))
            index = self.resolution_combo.findData(
                (*settings.resolution, settings.resolution_name)
            )
            self.resolution_combo.setCurrentIndex(max(0, index))
            return
        canvas_width, canvas_height = self.canvas_size
        ratio = canvas_width / max(1, canvas_height)

        def even(value: float) -> int:
            return max(2, round(value / 2.0) * 2)

        selected_index = 0
        for index, (base_name, base_size) in enumerate(RESOLUTIONS.items()):
            long_edge = max(base_size)
            if canvas_width >= canvas_height:
                width = long_edge
                height = even(long_edge / max(ratio, 1e-9))
            else:
                height = long_edge
                width = even(long_edge * ratio)
            quality = base_name.partition("(")[2].removesuffix(")")
            label = f"{width} × {height} (프로젝트 비율 · {quality})"
            if self.translator.language is not Language.KOREAN:
                label = f"{width} × {height} (Project ratio · {quality})"
            self.resolution_combo.addItem(label, (width, height, base_name))
            if base_name == settings.resolution_name:
                selected_index = index
        exact_width = even(canvas_width)
        exact_height = even(canvas_height)
        existing = {
            (self.resolution_combo.itemData(index)[0], self.resolution_combo.itemData(index)[1])
            for index in range(self.resolution_combo.count())
        }
        if (exact_width, exact_height) not in existing:
            label = (
                f"{exact_width} × {exact_height} (프로젝트 캔버스)"
                if self.translator.language is Language.KOREAN else
                f"{exact_width} × {exact_height} (Project canvas)"
            )
            self.resolution_combo.addItem(
                label, (exact_width, exact_height, settings.resolution_name)
            )
        self.resolution_combo.setCurrentIndex(selected_index)

    @property
    def save_as_default(self) -> bool:
        """Whether the chosen render values should become the next export defaults."""
        return self.save_default_check.isChecked()

    @property
    def output_path(self) -> Path:
        """Return the normalized MP4 destination selected in this dialog."""
        path = Path(self.output_path_edit.text().strip()).expanduser()
        if path.suffix.lower() != ".mp4":
            path = path.with_suffix(".mp4")
        return path.resolve()

    def _browse_output_path(self) -> None:
        korean = self.translator.language is Language.KOREAN
        selected, _filter = QFileDialog.getSaveFileName(
            self,
            "출력 파일 선택" if korean else "Choose output file",
            self.output_path_edit.text().strip(),
            "MP4 Video (*.mp4)",
        )
        if selected:
            path = Path(selected)
            if path.suffix.lower() != ".mp4":
                path = path.with_suffix(".mp4")
            self.output_path_edit.setText(str(path))

    def _accept_if_valid(self) -> None:
        """Validate the destination before expensive frame preparation begins."""
        korean = self.translator.language is Language.KOREAN
        if not self.output_path_edit.text().strip():
            QMessageBox.warning(
                self,
                "출력 경로 필요" if korean else "Output path required",
                "내보낼 MP4 파일 경로를 선택하세요."
                if korean else "Choose where the exported MP4 file should be saved.",
            )
            return
        output = self.output_path
        if output.exists():
            answer = QMessageBox.question(
                self,
                "파일 덮어쓰기" if korean else "Replace existing file",
                f"{output.name} 파일을 덮어쓸까요?"
                if korean else f"Replace the existing file '{output.name}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer is not QMessageBox.StandardButton.Yes:
                return
        self.output_path_edit.setText(str(output))
        self.accept()

    def _apply_balanced_optimization(self) -> None:
        """Apply a fast, visually lossless starting point without changing resolution."""
        self.crf_spin.setValue(18)
        self.preset_combo.setCurrentText("fast")
        self.audio_bitrate_combo.setCurrentText("256k")

    def retranslate(self) -> None:
        """Refresh dialog wording for the currently selected app language."""
        korean = self.translator.language is Language.KOREAN
        self.setWindowTitle("내보내기 설정" if korean else "Export settings")
        self.render_group.setTitle("영상 및 오디오 품질" if korean else "Video and audio quality")
        self.output_group.setTitle("출력 파일" if korean else "Output file")
        self.output_browse_button.setText("찾아보기" if korean else "Browse")
        self.resolution_label.setText("해상도" if korean else "Resolution")
        self.fps_label.setText("프레임 레이트" if korean else "Frame rate")
        self.codec_label.setText("비디오 인코더" if korean else "Video encoder")
        self.crf_label.setText("화질 (CRF, 낮을수록 고화질)" if korean else "Quality (CRF, lower is higher quality)")
        self.preset_label.setText("인코딩 속도" if korean else "Encoding speed")
        self.audio_label.setText("오디오 품질 (AAC)" if korean else "Audio quality (AAC)")
        self.optimize_button.setText(
            "균형 최적화 적용 (권장)" if korean else "Apply balanced optimization (Recommended)"
        )
        self.optimize_button.setToolTip(
            "해상도와 FPS는 유지하고, CRF 18 · Fast · AAC 256k를 적용합니다. "
            "선택한 GPU 인코더에서는 Fast가 가속 프리셋으로 변환됩니다."
            if korean else
            "Keeps resolution and FPS, then applies CRF 18 · Fast · AAC 256k. "
            "With a selected GPU encoder, Fast maps to its accelerated preset."
        )
        self.save_default_check.setText(
            "이 값을 다음 내보내기의 기본값으로 저장" if korean else "Save these values as future export defaults"
        )
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setText(
            "내보내기 시작" if korean else "Start export"
        )
        self.button_box.button(QDialogButtonBox.StandardButton.Cancel).setText(
            "취소" if korean else "Cancel"
        )
        duration = self._format_duration(self.duration_seconds)
        self.summary_label.setText(
            f"내보낼 곡 {self.track_count}개 · 총 재생 시간 {duration}\n"
            + (
                f"프로젝트 캔버스 {self.canvas_size[0]} × {self.canvas_size[1]}의 화면 비율을 유지합니다.\n"
                if self.canvas_size is not None else ""
            )
            + "품질과 출력 위치를 확인한 후 내보내기를 시작하세요."
            if korean else
            f"{self.track_count} track(s) · total duration {duration}\n"
            + (
                f"The {self.canvas_size[0]} × {self.canvas_size[1]} project canvas ratio will be preserved.\n"
                if self.canvas_size is not None else ""
            )
            + "Review the quality and output destination, then start the export."
        )

    @staticmethod
    def _format_duration(seconds: float) -> str:
        total = max(0, round(seconds))
        hours, remainder = divmod(total, 3600)
        minutes, seconds_part = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds_part:02d}" if hours else f"{minutes:02d}:{seconds_part:02d}"
