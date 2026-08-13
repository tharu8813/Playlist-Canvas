"""Creation-only canvas ratio and size selection."""

from __future__ import annotations

from math import gcd

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.utils.i18n import Language, Translator
from app.presets.preset_service import PresetDefinition, PresetService


CANVAS_PRESETS: tuple[tuple[str, int, int], ...] = (
    ("16:9", 1280, 720),
    ("9:16", 720, 1280),
    ("1:1", 1080, 1080),
    ("4:3", 1440, 1080),
    ("3:4", 1080, 1440),
    ("8:19", 800, 1900),
    ("21:9", 1680, 720),
)


class NewProjectDialog(QDialog):
    """Choose the initial project canvas dimensions and optional design preset."""

    def __init__(self, translator: Translator, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.translator = translator
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(500)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(14)

        self.description = QLabel()
        self.description.setObjectName("mutedLabel")
        self.description.setWordWrap(True)
        root.addWidget(self.description)

        self.canvas_group = QGroupBox()
        form = QFormLayout(self.canvas_group)
        self.preset_label = QLabel()
        self.preset_combo = QComboBox()
        for ratio, width, height in CANVAS_PRESETS:
            self.preset_combo.addItem(ratio, (width, height))
        self.preset_combo.addItem("", None)
        self.width_label = QLabel()
        self.width_spin = QSpinBox()
        self.width_spin.setRange(64, 16_384)
        self.width_spin.setSingleStep(2)
        self.width_spin.setValue(1280)
        self.height_label = QLabel()
        self.height_spin = QSpinBox()
        self.height_spin.setRange(64, 16_384)
        self.height_spin.setSingleStep(2)
        self.height_spin.setValue(720)
        form.addRow(self.preset_label, self.preset_combo)
        form.addRow(self.width_label, self.width_spin)
        form.addRow(self.height_label, self.height_spin)
        root.addWidget(self.canvas_group)

        self.design_group = QGroupBox()
        design_form = QFormLayout(self.design_group)
        self.design_preset_label = QLabel()
        self.design_preset_combo = QComboBox()
        self.design_preset_combo.addItem("", None)
        for preset in PresetService.all():
            self.design_preset_combo.addItem(
                preset.name(self.translator.language.value), preset.identifier,
            )
        design_form.addRow(self.design_preset_label, self.design_preset_combo)
        self.design_description = QLabel()
        self.design_description.setObjectName("mutedLabel")
        self.design_description.setWordWrap(True)
        design_form.addRow("", self.design_description)
        root.addWidget(self.design_group)

        self.summary = QLabel()
        self.summary.setObjectName("mutedLabel")
        self.summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.summary)

        self.notice = QLabel()
        self.notice.setObjectName("mutedLabel")
        self.notice.setWordWrap(True)
        root.addWidget(self.notice)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

        self.preset_combo.currentIndexChanged.connect(self._preset_changed)
        self.design_preset_combo.currentIndexChanged.connect(
            self._design_preset_changed
        )
        self.width_spin.valueChanged.connect(self._update_summary)
        self.height_spin.valueChanged.connect(self._update_summary)
        translator.language_changed.connect(self.retranslate)
        self.retranslate()
        self._preset_changed(0)

    @property
    def canvas_size(self) -> tuple[int, int]:
        """Return the canvas dimensions chosen for the new project."""
        return self.width_spin.value(), self.height_spin.value()

    @property
    def selected_design_preset(self) -> PresetDefinition | None:
        """Return the optional design used to populate the new project."""
        identifier = self.design_preset_combo.currentData()
        return next(
            (preset for preset in PresetService.all()
             if preset.identifier == identifier),
            None,
        )

    def _preset_changed(self, index: int) -> None:
        size = self.preset_combo.itemData(index)
        custom = size is None
        self.width_spin.setEnabled(custom)
        self.height_spin.setEnabled(custom)
        self.width_label.setVisible(custom)
        self.width_spin.setVisible(custom)
        self.height_label.setVisible(custom)
        self.height_spin.setVisible(custom)
        if not custom:
            width, height = size
            self.width_spin.setValue(width)
            self.height_spin.setValue(height)
        self._update_summary()

    def _design_preset_changed(self, _index: int) -> None:
        preset = self.selected_design_preset
        korean = self.translator.language is Language.KOREAN
        self.design_description.setText(
            preset.description(self.translator.language.value)
            if preset else (
                "기본 배경, 제목 및 진행 표시줄로 시작합니다."
                if korean else
                "Start with the default background, title, and progress bar."
            )
        )

    def _update_summary(self) -> None:
        width, height = self.canvas_size
        divisor = gcd(width, height)
        ratio = f"{width // divisor}:{height // divisor}"
        korean = self.translator.language is Language.KOREAN
        custom = self.preset_combo.currentData() is None
        if custom:
            self.summary.setText(
                f"사용자 지정 캔버스 {width} × {height}  ·  화면 비율 {ratio}"
                if korean else f"Custom canvas {width} × {height}  ·  Aspect ratio {ratio}"
            )
        else:
            self.summary.setText(
                f"화면 비율 {ratio}" if korean else f"Aspect ratio {ratio}"
            )

    def retranslate(self) -> None:
        korean = self.translator.language is Language.KOREAN
        selected_identifier = self.design_preset_combo.currentData()
        self.setWindowTitle("새 프로젝트" if korean else "New project")
        self.description.setText(
            "프로젝트에서 시작할 화면 비율을 선택하세요. 이후 프로젝트 설정에서도 변경할 수 있습니다."
            if korean else
            "Choose the initial aspect ratio. It can also be changed later in Project Settings."
        )
        self.canvas_group.setTitle("화면 비율 및 캔버스" if korean else "Aspect ratio and canvas")
        self.preset_label.setText("화면 비율" if korean else "Aspect ratio")
        self.width_label.setText("너비" if korean else "Width")
        self.height_label.setText("높이" if korean else "Height")
        custom_index = self.preset_combo.count() - 1
        self.preset_combo.setItemText(
            custom_index,
            "사용자 지정" if korean else "Custom",
        )
        self.width_spin.setSuffix(" px")
        self.height_spin.setSuffix(" px")
        self.notice.setText(
            "※ 내보내기 해상도는 출력 품질 설정이며 이 프로젝트의 화면 비율을 변경하지 않습니다."
            if korean else
            "Note: Export resolution controls output quality and does not change this project's aspect ratio."
        )
        self.design_group.setTitle(
            "시작 디자인" if korean else "Starting design"
        )
        self.design_preset_label.setText(
            "디자인 프리셋" if korean else "Design preset"
        )
        self.design_preset_combo.setItemText(
            0, "기본 프로젝트" if korean else "Default project"
        )
        for index, preset in enumerate(PresetService.all(), start=1):
            self.design_preset_combo.setItemText(
                index, preset.name(self.translator.language.value)
            )
        selected_index = self.design_preset_combo.findData(selected_identifier)
        self.design_preset_combo.setCurrentIndex(max(0, selected_index))
        self._design_preset_changed(self.design_preset_combo.currentIndex())
        ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button is not None:
            ok_button.setText("프로젝트 만들기" if korean else "Create project")
        cancel_button = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_button is not None:
            cancel_button.setText("취소" if korean else "Cancel")
        self._update_summary()
