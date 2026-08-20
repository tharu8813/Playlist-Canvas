"""Project identity, packaging, and thumbnail settings."""

from __future__ import annotations

from dataclasses import replace
from math import gcd
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QMessageBox, QRadioButton, QSpinBox, QVBoxLayout, QWidget,
)

from app.models.project import ProjectSettings
from app.dialogs.new_project_dialog import CANVAS_PRESETS
from app.utils.i18n import Language, Translator


class ProjectSettingsDialog(QDialog):
    """Edit settings that travel with the project instead of the application."""

    def __init__(
        self, settings: ProjectSettings, translator: Translator,
        canvas_thumbnail: QPixmap, parent: QWidget | None = None,
        canvas_size: tuple[int, int] = (1280, 720),
    ) -> None:
        super().__init__(parent)
        self.translator = translator
        self.selected_settings = replace(settings)
        self.canvas_thumbnail = canvas_thumbnail
        self.original_canvas_size = canvas_size
        self.setMinimumWidth(620)
        self.resize(680, 820)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(14)

        identity_group = QGroupBox()
        self.identity_group = identity_group
        form = QFormLayout(identity_group)
        self.title_edit = QLineEdit(settings.title)
        self.author_edit = QLineEdit(settings.author)
        self.description_edit = QPlainTextEdit(settings.description)
        self.description_edit.setMaximumHeight(84)
        self.title_label = QLabel()
        self.author_label = QLabel()
        self.description_label = QLabel()
        form.addRow(self.title_label, self.title_edit)
        form.addRow(self.author_label, self.author_edit)
        form.addRow(self.description_label, self.description_edit)
        root.addWidget(identity_group)

        self.canvas_group = QGroupBox()
        canvas_form = QFormLayout(self.canvas_group)
        self.canvas_preset_label = QLabel()
        self.canvas_preset_combo = QComboBox()
        for ratio, width, height in CANVAS_PRESETS:
            self.canvas_preset_combo.addItem(ratio, (width, height))
        self.canvas_preset_combo.addItem("", None)
        self.canvas_width_label = QLabel()
        self.canvas_width_spin = QSpinBox()
        self.canvas_width_spin.setRange(64, 16_384)
        self.canvas_width_spin.setSingleStep(2)
        self.canvas_width_spin.setValue(canvas_size[0])
        self.canvas_height_label = QLabel()
        self.canvas_height_spin = QSpinBox()
        self.canvas_height_spin.setRange(64, 16_384)
        self.canvas_height_spin.setSingleStep(2)
        self.canvas_height_spin.setValue(canvas_size[1])
        self.canvas_summary = QLabel()
        self.canvas_summary.setObjectName("mutedLabel")
        self.canvas_summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scale_content_radio = QRadioButton()
        self.keep_content_radio = QRadioButton()
        resize_buttons = QButtonGroup(self)
        resize_buttons.addButton(self.scale_content_radio)
        resize_buttons.addButton(self.keep_content_radio)
        self.scale_content_radio.setChecked(True)
        canvas_form.addRow(self.canvas_preset_label, self.canvas_preset_combo)
        canvas_form.addRow(self.canvas_width_label, self.canvas_width_spin)
        canvas_form.addRow(self.canvas_height_label, self.canvas_height_spin)
        canvas_form.addRow("", self.canvas_summary)
        canvas_form.addRow("", self.scale_content_radio)
        canvas_form.addRow("", self.keep_content_radio)
        root.addWidget(self.canvas_group)

        self.content_group = QGroupBox()
        content_layout = QVBoxLayout(self.content_group)
        self.embed_radio = QRadioButton()
        self.reference_radio = QRadioButton()
        content_buttons = QButtonGroup(self)
        content_buttons.addButton(self.embed_radio)
        content_buttons.addButton(self.reference_radio)
        self.embed_help = QLabel()
        self.reference_help = QLabel()
        for label in (self.embed_help, self.reference_help):
            label.setObjectName("mutedLabel")
            label.setWordWrap(True)
            label.setContentsMargins(24, 0, 0, 4)
        content_layout.addWidget(self.embed_radio)
        content_layout.addWidget(self.embed_help)
        content_layout.addWidget(self.reference_radio)
        content_layout.addWidget(self.reference_help)
        (self.embed_radio if settings.content_mode == "embed" else self.reference_radio).setChecked(True)
        root.addWidget(self.content_group)

        self.thumbnail_group = QGroupBox()
        thumbnail_layout = QHBoxLayout(self.thumbnail_group)
        self.thumbnail_preview = QLabel()
        self.thumbnail_preview.setFixedSize(192, 108)
        self.thumbnail_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail_preview.setObjectName("thumbnailPreview")
        thumbnail_layout.addWidget(self.thumbnail_preview)
        thumbnail_controls = QVBoxLayout()
        self.canvas_radio = QRadioButton()
        self.custom_radio = QRadioButton()
        thumbnail_buttons = QButtonGroup(self)
        thumbnail_buttons.addButton(self.canvas_radio)
        thumbnail_buttons.addButton(self.custom_radio)
        self.choose_thumbnail_button = QPushButton()
        self.thumbnail_path_label = QLabel(settings.thumbnail_path)
        self.thumbnail_path_label.setObjectName("mutedLabel")
        self.thumbnail_path_label.setWordWrap(True)
        thumbnail_controls.addWidget(self.canvas_radio)
        thumbnail_controls.addWidget(self.custom_radio)
        thumbnail_controls.addWidget(self.choose_thumbnail_button)
        thumbnail_controls.addWidget(self.thumbnail_path_label)
        thumbnail_controls.addStretch()
        thumbnail_layout.addLayout(thumbnail_controls, 1)
        (self.custom_radio if settings.thumbnail_mode == "custom" else self.canvas_radio).setChecked(True)
        root.addWidget(self.thumbnail_group)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self.choose_thumbnail_button.clicked.connect(self._choose_thumbnail)
        self.canvas_radio.toggled.connect(self._refresh_thumbnail)
        self.custom_radio.toggled.connect(self._refresh_thumbnail)
        self.canvas_preset_combo.currentIndexChanged.connect(
            self._canvas_preset_changed
        )
        self.canvas_width_spin.valueChanged.connect(self._update_canvas_summary)
        self.canvas_height_spin.valueChanged.connect(self._update_canvas_summary)
        translator.language_changed.connect(self.retranslate)
        matching = self.canvas_preset_combo.findData(canvas_size)
        self.canvas_preset_combo.setCurrentIndex(
            matching if matching >= 0 else self.canvas_preset_combo.count() - 1
        )
        self.retranslate()
        self._canvas_preset_changed(self.canvas_preset_combo.currentIndex())
        self._refresh_thumbnail()

    @property
    def selected_canvas_size(self) -> tuple[int, int]:
        return self.canvas_width_spin.value(), self.canvas_height_spin.value()

    @property
    def scale_canvas_content(self) -> bool:
        return self.scale_content_radio.isChecked()

    def _canvas_preset_changed(self, index: int) -> None:
        size = self.canvas_preset_combo.itemData(index)
        custom = size is None
        self.canvas_width_spin.setEnabled(custom)
        self.canvas_height_spin.setEnabled(custom)
        if size is not None:
            self.canvas_width_spin.setValue(size[0])
            self.canvas_height_spin.setValue(size[1])
        self._update_canvas_summary()

    def _update_canvas_summary(self) -> None:
        width, height = self.selected_canvas_size
        divisor = gcd(width, height)
        ratio = f"{width // divisor}:{height // divisor}"
        self.canvas_summary.setText(
            f"캔버스 {width} × {height} · 화면 비율 {ratio}"
            if self.translator.language is Language.KOREAN else
            f"Canvas {width} × {height} · Aspect ratio {ratio}"
        )

    def retranslate(self) -> None:
        korean = self.translator.language is Language.KOREAN
        self.setWindowTitle("프로젝트 설정" if korean else "Project settings")
        self.identity_group.setTitle("프로젝트 정보" if korean else "Project information")
        self.title_label.setText("이름" if korean else "Name")
        self.author_label.setText("작성자" if korean else "Author")
        self.description_label.setText("설명" if korean else "Description")
        self.canvas_group.setTitle("화면 비율 및 캔버스" if korean else "Aspect ratio and canvas")
        self.canvas_preset_label.setText("화면 비율" if korean else "Aspect ratio")
        self.canvas_width_label.setText("너비" if korean else "Width")
        self.canvas_height_label.setText("높이" if korean else "Height")
        self.canvas_preset_combo.setItemText(
            self.canvas_preset_combo.count() - 1,
            "사용자 지정" if korean else "Custom",
        )
        self.scale_content_radio.setText(
            "기존 요소를 새 화면 비율에 맞춰 재배치 및 크기 조정 (권장)"
            if korean else "Reposition and scale existing sources for the new ratio (Recommended)"
        )
        self.keep_content_radio.setText(
            "캔버스만 변경하고 요소 위치와 크기 유지"
            if korean else "Change only the canvas and keep source geometry"
        )
        self.content_group.setTitle("콘텐츠 저장 방식" if korean else "Content storage")
        self.embed_radio.setText("추가한 콘텐츠를 프로젝트에 포함" if korean else "Add imported content to the project")
        self.embed_help.setText(
            "이미지, 음원, 폰트와 가사를 패키지 안에 복사합니다. 파일은 커지지만 다른 PC에서도 안전하게 열립니다."
            if korean else "Copies images, audio, fonts, and lyrics into the package. The file is larger but portable."
        )
        self.reference_radio.setText("외부 콘텐츠를 프로젝트에서 참조" if korean else "Reference content from its external location")
        self.reference_help.setText(
            "원본 파일 경로를 사용합니다. 프로젝트는 작지만 원본을 이동하면 다시 연결해야 합니다."
            if korean else "Keeps original file paths. The project stays small, but moved files must be relinked."
        )
        self.thumbnail_group.setTitle("프로젝트 썸네일" if korean else "Project thumbnail")
        self.canvas_radio.setText("현재 캔버스를 자동 사용" if korean else "Use the current canvas")
        self.custom_radio.setText("사용자 이미지 사용" if korean else "Use a custom image")
        self.choose_thumbnail_button.setText("이미지 선택…" if korean else "Choose image…")
        self._update_canvas_summary()

    def _choose_thumbnail(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "썸네일 선택" if self.translator.language is Language.KOREAN else "Choose thumbnail",
            "",
            "Images (*.jpg *.jpeg *.png *.webp)",
        )
        if selected:
            self.selected_settings.thumbnail_path = str(Path(selected).resolve())
            self.thumbnail_path_label.setText(self.selected_settings.thumbnail_path)
            self.custom_radio.setChecked(True)
            self._refresh_thumbnail()

    def _refresh_thumbnail(self) -> None:
        pixmap = self.canvas_thumbnail
        if self.custom_radio.isChecked() and self.selected_settings.thumbnail_path:
            custom = QPixmap(self.selected_settings.thumbnail_path)
            if not custom.isNull():
                pixmap = custom
        self.thumbnail_preview.setPixmap(
            pixmap.scaled(
                self.thumbnail_preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.choose_thumbnail_button.setEnabled(self.custom_radio.isChecked())

    def _accept(self) -> None:
        if self.selected_canvas_size != self.original_canvas_size:
            korean = self.translator.language is Language.KOREAN
            answer = QMessageBox.warning(
                self,
                "캔버스 크기 변경" if korean else "Change canvas size",
                (
                    f"캔버스를 {self.original_canvas_size[0]} × {self.original_canvas_size[1]}에서 "
                    f"{self.selected_canvas_size[0]} × {self.selected_canvas_size[1]}(으)로 변경합니다.\n\n"
                    "요소 배치가 달라질 수 있으며 변경 후 Ctrl+Z로 되돌릴 수 있습니다. 계속할까요?"
                    if korean else
                    f"Change the canvas from {self.original_canvas_size[0]} × {self.original_canvas_size[1]} "
                    f"to {self.selected_canvas_size[0]} × {self.selected_canvas_size[1]}?\n\n"
                    "Source layout may change. You can undo afterward with Ctrl+Z."
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.selected_settings.title = self.title_edit.text().strip() or "Untitled Project"
        self.selected_settings.author = self.author_edit.text().strip()
        self.selected_settings.description = self.description_edit.toPlainText().strip()
        self.selected_settings.content_mode = "embed" if self.embed_radio.isChecked() else "reference"
        self.selected_settings.thumbnail_mode = "custom" if self.custom_radio.isChecked() else "canvas"
        self.accept()
