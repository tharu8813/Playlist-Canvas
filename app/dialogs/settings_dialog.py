"""Application settings dialog for Phase 4A."""

from __future__ import annotations

import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.services.app_settings_service import (
    ENCODING_PRESETS,
    RESOLUTIONS,
    VIDEO_ENCODERS,
    AUDIO_BITRATES,
    AppSettings,
)
from app.services.theme_service import Theme
from app.utils.i18n import Language, Translator
from app.utils.subprocess_utils import hidden_process_kwargs


class SettingsDialog(QDialog):
    """Edits app-wide render, appearance, and localization preferences."""

    download_requested = Signal()

    def __init__(self, settings: AppSettings, language: Language, theme: Theme,
                 translator: Translator, parent: object | None = None) -> None:
        super().__init__(parent)
        self.translator = translator
        self.setMinimumSize(720, 590)
        self.resize(760, 640)
        self._ffmpeg_installing = False
        self._ffmpeg_status_override: tuple[bool, str] | None = None
        self.title_label = QLabel()
        self.title_label.setObjectName("dialogTitle")
        self.subtitle_label = QLabel()
        self.subtitle_label.setObjectName("mutedLabel")
        self.subtitle_label.setWordWrap(True)
        self.ffmpeg_edit = QLineEdit(settings.ffmpeg_path)
        self.ffmpeg_edit.setClearButtonEnabled(True)
        self.ffmpeg_browse_button = QPushButton()
        self.ffmpeg_test_button = QPushButton()
        self.ffmpeg_download_button = QPushButton()
        self.ffmpeg_download_button.setObjectName("primaryButton")
        self.ffmpeg_download_button.setMinimumWidth(180)
        self.ffmpeg_status_card = QFrame()
        self.ffmpeg_status_card.setObjectName("settingsStatusCard")
        self.ffmpeg_status_dot = QFrame()
        self.ffmpeg_status_dot.setFixedSize(12, 12)
        self.ffmpeg_status_label = QLabel()
        self.ffmpeg_status_label.setObjectName("panelTitle")
        self.ffmpeg_status_detail = QLabel()
        self.ffmpeg_status_detail.setObjectName("mutedLabel")
        self.ffmpeg_status_detail.setWordWrap(True)
        self.managed_install_label = QLabel()
        self.managed_install_label.setObjectName("mutedLabel")
        self.managed_install_label.setWordWrap(True)
        self.output_edit = QLineEdit(settings.output_directory)
        self.output_edit.setClearButtonEnabled(True)
        self.output_browse_button = QPushButton()
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems(RESOLUTIONS)
        self.resolution_combo.setCurrentText(settings.resolution_name)
        self.fps_combo = QComboBox()
        self.fps_combo.addItems(["24", "25", "30", "50", "60"])
        self.fps_combo.setCurrentText(str(settings.fps))
        self.codec_combo = QComboBox()
        for label, encoder in VIDEO_ENCODERS.items():
            self.codec_combo.addItem(label, encoder)
        self.codec_combo.setCurrentIndex(self.codec_combo.findData(settings.video_codec))
        self.crf_spin = QSpinBox()
        self.crf_spin.setRange(0, 51)
        self.crf_spin.setValue(settings.crf)
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(ENCODING_PRESETS)
        self.preset_combo.setCurrentText(settings.preset)
        self.audio_bitrate_combo = QComboBox()
        self.audio_bitrate_combo.addItems(AUDIO_BITRATES)
        self.audio_bitrate_combo.setCurrentText(settings.audio_bitrate)
        for combo in (
            self.resolution_combo, self.fps_combo, self.codec_combo,
            self.preset_combo, self.audio_bitrate_combo,
        ):
            combo.setMinimumWidth(260)
        self.render_hint_label = QLabel()
        self.render_hint_label.setObjectName("mutedLabel")
        self.render_hint_label.setWordWrap(True)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("", Theme.LIGHT)
        self.theme_combo.addItem("", Theme.DARK)
        self.theme_combo.addItem("", Theme.AUTO)
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(theme))
        self.language_combo = QComboBox()
        self.language_combo.addItem("한국어", Language.KOREAN)
        self.language_combo.addItem("English", Language.ENGLISH)
        self.language_combo.setCurrentIndex(self.language_combo.findData(language))
        self.smooth_scroll_check = QCheckBox()
        self.smooth_scroll_check.setChecked(settings.smooth_scrolling)
        self.smooth_scroll_duration_slider = QSlider(Qt.Orientation.Horizontal)
        self.smooth_scroll_duration_slider.setRange(80, 420)
        self.smooth_scroll_duration_slider.setSingleStep(10)
        self.smooth_scroll_duration_slider.setPageStep(40)
        self.smooth_scroll_duration_slider.setValue(settings.smooth_scroll_duration_ms)
        self.smooth_scroll_duration_value = QLabel()
        self.smooth_scroll_duration_value.setObjectName("mutedLabel")
        self.smooth_scroll_duration_value.setMinimumWidth(92)
        self.smooth_scroll_duration_value.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        smooth_scroll_speed_row = QWidget()
        smooth_scroll_speed_layout = QHBoxLayout(smooth_scroll_speed_row)
        smooth_scroll_speed_layout.setContentsMargins(0, 0, 0, 0)
        smooth_scroll_speed_layout.addWidget(self.smooth_scroll_duration_slider, 1)
        smooth_scroll_speed_layout.addWidget(self.smooth_scroll_duration_value)
        self.smooth_scroll_check.toggled.connect(self._update_smooth_scroll_ui)
        self.smooth_scroll_duration_slider.valueChanged.connect(
            self._update_smooth_scroll_ui
        )

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        self.button_box.accepted.connect(self._accept_settings)
        self.button_box.rejected.connect(self.reject)
        self.ffmpeg_browse_button.clicked.connect(self._browse_ffmpeg)
        self.ffmpeg_test_button.clicked.connect(self._test_ffmpeg)
        self.ffmpeg_download_button.clicked.connect(self.download_requested.emit)
        self.output_browse_button.clicked.connect(self._browse_output)
        self.ffmpeg_edit.textChanged.connect(self._refresh_ffmpeg_status)

        ffmpeg_row = QHBoxLayout()
        ffmpeg_row.addWidget(self.ffmpeg_edit, 1)
        ffmpeg_row.addWidget(self.ffmpeg_browse_button)
        ffmpeg_actions = QHBoxLayout()
        ffmpeg_actions.addStretch()
        ffmpeg_actions.addWidget(self.ffmpeg_test_button)
        ffmpeg_actions.addWidget(self.ffmpeg_download_button)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(self.output_browse_button)

        ffmpeg_group = QGroupBox()
        ffmpeg_form = QFormLayout(ffmpeg_group)
        self.ffmpeg_path_label = QLabel()
        self.output_label = QLabel()
        ffmpeg_form.addRow(self.ffmpeg_path_label, ffmpeg_row)
        ffmpeg_form.addRow("", ffmpeg_actions)
        ffmpeg_form.addRow("", self.managed_install_label)

        status_grid = QGridLayout(self.ffmpeg_status_card)
        status_grid.setContentsMargins(14, 12, 14, 12)
        status_grid.addWidget(self.ffmpeg_status_dot, 0, 0, 2, 1)
        status_grid.addWidget(self.ffmpeg_status_label, 0, 1)
        status_grid.addWidget(self.ffmpeg_status_detail, 1, 1)

        output_group = QGroupBox()
        output_form = QFormLayout(output_group)
        output_form.addRow(self.output_label, output_row)
        render_group = QGroupBox()
        render_form = QFormLayout(render_group)
        self.resolution_label = QLabel()
        self.fps_label = QLabel()
        self.codec_label = QLabel()
        self.crf_label = QLabel()
        self.preset_label = QLabel()
        self.audio_bitrate_label = QLabel()
        render_form.addRow(self.resolution_label, self.resolution_combo)
        render_form.addRow(self.fps_label, self.fps_combo)
        render_form.addRow(self.codec_label, self.codec_combo)
        render_form.addRow(self.crf_label, self.crf_spin)
        render_form.addRow(self.preset_label, self.preset_combo)
        render_form.addRow(self.audio_bitrate_label, self.audio_bitrate_combo)
        render_form.addRow("", self.render_hint_label)
        app_group = QGroupBox()
        app_form = QFormLayout(app_group)
        self.theme_label = QLabel()
        self.language_label = QLabel()
        self.smooth_scroll_label = QLabel()
        self.smooth_scroll_speed_label = QLabel()
        app_form.addRow(self.theme_label, self.theme_combo)
        app_form.addRow(self.language_label, self.language_combo)
        app_form.addRow(self.smooth_scroll_label, self.smooth_scroll_check)
        app_form.addRow(self.smooth_scroll_speed_label, smooth_scroll_speed_row)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("settingsTabs")
        self.tabs.setDocumentMode(True)
        self.general_page = QWidget()
        general_layout = QVBoxLayout(self.general_page)
        general_layout.setContentsMargins(14, 16, 14, 14)
        general_layout.addWidget(app_group)
        general_layout.addStretch()
        self.export_page = QWidget()
        export_layout = QVBoxLayout(self.export_page)
        export_layout.setContentsMargins(14, 16, 14, 14)
        export_layout.addWidget(output_group)
        export_layout.addWidget(render_group)
        export_layout.addStretch()
        self.ffmpeg_page = QWidget()
        ffmpeg_layout = QVBoxLayout(self.ffmpeg_page)
        ffmpeg_layout.setContentsMargins(14, 16, 14, 14)
        ffmpeg_layout.addWidget(self.ffmpeg_status_card)
        ffmpeg_layout.addWidget(ffmpeg_group)
        ffmpeg_layout.addStretch()
        self.tabs.addTab(self.general_page, "")
        self.tabs.addTab(self.export_page, "")
        self.tabs.addTab(self.ffmpeg_page, "")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(self.title_label)
        layout.addWidget(self.subtitle_label)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.button_box)
        self.ffmpeg_group = ffmpeg_group
        self.output_group = output_group
        self.render_group = render_group
        self.app_group = app_group
        self.retranslate()
        self._update_smooth_scroll_ui()
        self._refresh_ffmpeg_status()

    @property
    def app_settings(self) -> AppSettings:
        """Return validated scalar values collected from dialog controls."""
        return AppSettings(
            ffmpeg_path=self.ffmpeg_edit.text(),
            output_directory=self.output_edit.text(),
            resolution_name=self.resolution_combo.currentText(),
            fps=int(self.fps_combo.currentText()),
            video_codec=self.codec_combo.currentData(),
            crf=self.crf_spin.value(),
            preset=self.preset_combo.currentText(),
            audio_bitrate=self.audio_bitrate_combo.currentText(),
            smooth_scrolling=self.smooth_scroll_check.isChecked(),
            smooth_scroll_duration_ms=self.smooth_scroll_duration_slider.value(),
        )

    @property
    def selected_theme(self) -> Theme:
        """Return the selected theme preference."""
        return Theme(self.theme_combo.currentData())

    @property
    def selected_language(self) -> Language:
        """Return the selected application language."""
        return Language(self.language_combo.currentData())

    def _browse_ffmpeg(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, self._ffmpeg_browse_title(), self.ffmpeg_edit.text(),
            "FFmpeg executable (ffmpeg.exe ffmpeg);;All files (*)",
        )
        if path:
            self.ffmpeg_edit.setText(path)

    def _browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, self._output_browse_title(), self.output_edit.text() or str(Path.home())
        )
        if path:
            self.output_edit.setText(path)

    def _test_ffmpeg(self) -> None:
        path = Path(self.ffmpeg_edit.text().strip())
        if not path.is_file():
            self._show_test_result(False, "FFmpeg executable was not found at the selected path.")
            return
        try:
            completed = subprocess.run(
                [str(path), "-version"], capture_output=True, text=True, timeout=10, check=False,
                **hidden_process_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            self._show_test_result(False, str(error))
            return
        version = completed.stdout.splitlines()[0] if completed.stdout else ""
        if completed.returncode != 0:
            self._show_test_result(False, version or "FFmpeg did not return version data.")
            return
        try:
            encoders = subprocess.run(
                [str(path), "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                **hidden_process_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            self._show_test_result(False, str(error))
            return
        encoder = self.codec_combo.currentData()
        if encoders.returncode != 0 or encoder not in encoders.stdout.split():
            self._show_test_result(False, f"Selected encoder is unavailable: {encoder}")
            return
        self._show_test_result(True, f"{version}\nEncoder available: {encoder}")

    def _accept_settings(self) -> None:
        """Reject a visibly broken executable path instead of saving silent failure."""
        raw_path = self.ffmpeg_edit.text().strip()
        if raw_path and not Path(raw_path).is_file():
            korean = self.translator.language is Language.KOREAN
            QMessageBox.warning(
                self,
                "FFmpeg 경로 확인" if korean else "Check FFmpeg path",
                "선택한 FFmpeg 실행 파일을 찾을 수 없습니다. 경로를 지우거나 올바른 파일을 선택해 주세요."
                if korean else
                "The selected FFmpeg executable does not exist. Clear the path or choose a valid file.",
            )
            self.tabs.setCurrentWidget(self.ffmpeg_page)
            self.ffmpeg_edit.setFocus()
            return
        self.accept()

    def _update_smooth_scroll_ui(self, _value: object = None) -> None:
        enabled = self.smooth_scroll_check.isChecked()
        self.smooth_scroll_duration_slider.setEnabled(enabled)
        self.smooth_scroll_duration_value.setEnabled(enabled)
        duration = self.smooth_scroll_duration_slider.value()
        korean = self.translator.language is Language.KOREAN
        feel = (
            "빠름" if korean and duration <= 130 else
            "균형" if korean and duration <= 240 else
            "매우 부드러움" if korean else
            "Fast" if duration <= 130 else
            "Balanced" if duration <= 240 else
            "Very smooth"
        )
        self.smooth_scroll_duration_value.setText(f"{duration} ms · {feel}")

    def set_ffmpeg_installing(self, installing: bool) -> None:
        """Expose immediate, persistent feedback while the background install runs."""
        self._ffmpeg_installing = installing
        self.ffmpeg_download_button.setEnabled(not installing)
        if installing:
            self._ffmpeg_status_override = (
                True,
                "FFmpeg를 다운로드하고 검증하는 중입니다. 진행 창에서 상태를 확인하세요."
                if self.translator.language is Language.KOREAN else
                "Downloading and verifying FFmpeg. Follow progress in the install window.",
            )
        else:
            self._ffmpeg_status_override = None
        self._refresh_ffmpeg_status()

    def set_ffmpeg_install_error(self, message: str) -> None:
        """Keep an installation error visible inside Settings after a message box closes."""
        self._ffmpeg_installing = False
        self.ffmpeg_download_button.setEnabled(True)
        self._ffmpeg_status_override = (False, message)
        self._refresh_ffmpeg_status()

    def _refresh_ffmpeg_status(self, *_args: object) -> None:
        korean = self.translator.language is Language.KOREAN
        path = Path(self.ffmpeg_edit.text().strip()) if self.ffmpeg_edit.text().strip() else None
        if self._ffmpeg_status_override is not None:
            positive, detail = self._ffmpeg_status_override
            title = (
                "설치 진행 중" if self._ffmpeg_installing and korean else
                "Installing FFmpeg" if self._ffmpeg_installing else
                "설치 확인 필요" if korean else "FFmpeg needs attention"
            )
            color = "#F59E0B" if positive else "#EF4444"
        elif path is not None and path.is_file():
            title = "FFmpeg 사용 가능" if korean else "FFmpeg configured"
            detail = str(path)
            color = "#22C55E"
        elif path is not None:
            title = "FFmpeg 파일 없음" if korean else "FFmpeg file not found"
            detail = str(path)
            color = "#EF4444"
        else:
            title = "FFmpeg가 설정되지 않음" if korean else "FFmpeg is not configured"
            detail = (
                "자동 다운로드를 사용하거나 설치된 ffmpeg.exe를 직접 선택하세요."
                if korean else
                "Use automatic download or select an existing ffmpeg executable."
            )
            color = "#94A3B8"
        self.ffmpeg_status_dot.setStyleSheet(
            f"background: {color}; border: 0; border-radius: 6px;"
        )
        self.ffmpeg_status_label.setText(title)
        self.ffmpeg_status_detail.setText(detail)

    def _show_test_result(self, success: bool, detail: str) -> None:
        korean = self.translator.language is Language.KOREAN
        QMessageBox.information(
            self,
            "FFmpeg 확인" if korean else "FFmpeg check",
            ("FFmpeg을 확인했습니다.\n" if korean else "FFmpeg is ready.\n") + detail
            if success else
            ("FFmpeg 확인에 실패했습니다.\n" if korean else "FFmpeg check failed.\n") + detail,
        )

    def retranslate(self) -> None:
        """Set all labels for the currently active language."""
        korean = self.translator.language is Language.KOREAN
        self.setWindowTitle("설정" if korean else "Settings")
        self.title_label.setText("애플리케이션 설정" if korean else "Application settings")
        self.subtitle_label.setText(
            "화면 표시, 기본 출력 품질과 FFmpeg 설치를 관리합니다."
            if korean else
            "Manage appearance, default export quality, and the FFmpeg installation."
        )
        self.tabs.setTabText(0, "일반" if korean else "General")
        self.tabs.setTabText(1, "내보내기" if korean else "Export")
        self.tabs.setTabText(2, "FFmpeg")
        self.ffmpeg_group.setTitle("FFmpeg 실행 파일" if korean else "FFmpeg executable")
        self.output_group.setTitle("출력 위치" if korean else "Output location")
        self.render_group.setTitle("기본 렌더링" if korean else "Default rendering")
        self.app_group.setTitle("앱" if korean else "Application")
        self.ffmpeg_path_label.setText("FFmpeg 경로" if korean else "FFmpeg path")
        self.output_label.setText("기본 출력 폴더" if korean else "Default output folder")
        self.resolution_label.setText("해상도" if korean else "Resolution")
        self.fps_label.setText("FPS")
        self.codec_label.setText("비디오 인코더" if korean else "Video encoder")
        self.crf_label.setText("CRF (낮을수록 고화질)" if korean else "CRF (lower is higher quality)")
        self.preset_label.setText("인코딩 Preset" if korean else "Encoding preset")
        self.audio_bitrate_label.setText(
            "오디오 품질 (AAC)" if korean else "Audio quality (AAC)"
        )
        self.theme_label.setText("테마" if korean else "Theme")
        self.language_label.setText("언어" if korean else "Language")
        self.smooth_scroll_label.setText("스크롤 동작" if korean else "Scrolling")
        self.smooth_scroll_speed_label.setText(
            "부드러움" if korean else "Smoothness"
        )
        self.smooth_scroll_check.setText(
            "부드러운 스크롤 사용" if korean else "Enable smooth scrolling"
        )
        self.smooth_scroll_duration_slider.setToolTip(
            "값이 높을수록 더 천천히 부드럽게 이동합니다."
            if korean else
            "Higher values scroll more slowly and smoothly."
        )
        self.ffmpeg_browse_button.setText("찾아보기" if korean else "Browse")
        self.ffmpeg_test_button.setText("확인" if korean else "Check")
        self.ffmpeg_download_button.setText("다운로드" if korean else "Download")
        self.ffmpeg_download_button.setText(
            "FFmpeg 자동 다운로드 및 설치" if korean else "Download and install FFmpeg"
        )
        self.managed_install_label.setText(
            "Windows 64비트용 BtbN FFmpeg GPL 배포본을 앱 전용 폴더에 내려받고 SHA-256으로 검증합니다."
            if korean else
            "Downloads the BtbN FFmpeg GPL Windows 64-bit build into the app-only folder and verifies SHA-256."
        )
        self.ffmpeg_edit.setPlaceholderText(
            "예: C:\\Tools\\ffmpeg\\bin\\ffmpeg.exe" if korean else
            "Example: C:\\Tools\\ffmpeg\\bin\\ffmpeg.exe"
        )
        self.output_edit.setPlaceholderText(
            "비워 두면 기본 비디오 폴더 사용" if korean else
            "Leave empty to use the default Videos folder"
        )
        self.render_hint_label.setText(
            "권장 시작값: Full HD, 30 FPS, H.264, CRF 18, preset medium. "
            "CRF가 낮을수록 화질과 파일 크기가 증가합니다."
            if korean else
            "Recommended starting point: Full HD, 30 FPS, H.264, CRF 18, preset medium. "
            "Lower CRF increases quality and file size."
        )
        self.codec_combo.setToolTip(
            "GPU 인코더는 해당 그래픽 드라이버와 FFmpeg 지원이 필요합니다."
            if korean else
            "GPU encoders require compatible graphics drivers and FFmpeg support."
        )
        self.preset_combo.setToolTip(
            "느린 preset은 일반적으로 더 작은 파일을 만들지만 인코딩 시간이 길어집니다."
            if korean else
            "Slower presets generally produce smaller files but take longer to encode."
        )
        self.output_browse_button.setText("찾아보기" if korean else "Browse")
        self.theme_combo.setItemText(0, "라이트" if korean else "Light")
        self.theme_combo.setItemText(1, "다크" if korean else "Dark")
        self.theme_combo.setItemText(2, "자동" if korean else "Auto")
        self._update_smooth_scroll_ui()
        self.button_box.button(QDialogButtonBox.StandardButton.Save).setText(
            "저장" if korean else "Save"
        )
        self.button_box.button(QDialogButtonBox.StandardButton.Cancel).setText(
            "취소" if korean else "Cancel"
        )
        self._refresh_ffmpeg_status()

    def _ffmpeg_browse_title(self) -> str:
        return "FFmpeg 실행 파일 선택" if self.translator.language is Language.KOREAN else "Choose FFmpeg executable"

    def _output_browse_title(self) -> str:
        return "기본 출력 폴더 선택" if self.translator.language is Language.KOREAN else "Choose default output folder"
