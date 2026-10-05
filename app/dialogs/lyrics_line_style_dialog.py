"""Edit typography overrides by physical line position inside lyric cues."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QFrame, QHBoxLayout, QLabel, QLayout, QListWidget, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from app.dialogs.color_editor_dialog import ColorEditorDialog
from app.dialogs.help_dialog import install_help_shortcut
from app.models.source import Source
from app.utils.i18n import Translator


class LyricsLineStyleDialog(QDialog):
    MAX_LINES = 12

    def __init__(
        self, source: Source, translator: Translator, parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._source = source
        self._advanced_style = source.resolved_lyrics().subtitle_role_styles.get("current", {}) if (
            "styles" in source.subtitle_advanced_categories
        ) else {}
        self._translator = translator
        install_help_shortcut(self, ("canvas", "lyrics_line_styles"), translator=translator)
        self._styles = [dict(style) for style in source.subtitle_line_styles]
        self._loading = False
        self._korean = translator.is_korean
        self._color = source.outline_color
        self._size_mode_value = "relative"

        self.setWindowTitle("가사 줄별 스타일" if self._korean else "Per-line lyric styles")
        self.setObjectName("lyricsLineStyleDialog")
        self.setModal(True)
        self.setMinimumSize(720, 540)
        self.resize(820, 640)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(14)
        title = QLabel("가사 줄별 스타일" if self._korean else "Per-line lyric styles")
        title.setObjectName("dialogTitle")
        root.addWidget(title)
        description = QLabel(
            "2번째 줄부터 색상과 글꼴을 조절하세요. 같은 줄 위치에 반복 적용되며, 개별 설정을 끄면 전체 스타일을 따릅니다."
            if self._korean else
            "Adjust color and typography from line 2 onward. Styles repeat by cue position; turn off custom settings to inherit the overall style."
        )
        description.setObjectName("mutedLabel")
        description.setWordWrap(True)
        root.addWidget(description)
        if self._advanced_style:
            notice = QLabel("고급 줄 스타일의 크기 배율이 함께 적용됩니다. 색상·굵기·기울임과 줄별 크기는 이 창에서 계속 편집할 수 있습니다."
                            if self._korean else "Advanced role size scaling also applies. Color, weight, italic and per-line size remain editable here.")
            notice.setObjectName("mutedLabel")
            notice.setWordWrap(True)
            root.addWidget(notice)

        body = QHBoxLayout()
        body.setSpacing(14)
        self.line_list = QListWidget()
        self.line_list.setObjectName("lyricStyleLines")
        self.line_list.setFixedWidth(184)
        self.line_list.setAccessibleName("가사 줄 선택" if self._korean else "Select lyric line")
        for index in range(1, self.MAX_LINES):
            self.line_list.addItem("")
            self._refresh_line_item(index)
        sidebar = QVBoxLayout()
        sidebar.setSpacing(8)
        sidebar.addWidget(self.line_list)
        self.expand_lines = QPushButton()
        self.expand_lines.setCheckable(True)
        self.expand_lines.setAutoDefault(False)
        self.expand_lines.toggled.connect(self._toggle_list)
        sidebar.addWidget(self.expand_lines)
        sidebar.addStretch(1)
        body.addLayout(sidebar)

        panel = QFrame()
        panel.setObjectName("lyricStylePanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 0, 0, 0)
        panel_layout.setSpacing(16)
        panel_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.line_title = QLabel()
        self.line_title.setObjectName("lyricLineTitle")
        heading = QHBoxLayout()
        heading.addWidget(self.line_title, 1)
        self.enabled = QCheckBox(
            "개별 스타일 사용" if self._korean else "Custom style"
        )
        heading.addWidget(self.enabled)
        panel_layout.addLayout(heading)

        preview_frame = QFrame()
        preview_frame.setObjectName("lyricStylePreview")
        preview_layout = QVBoxLayout(preview_frame)
        preview_layout.setContentsMargins(16, 14, 16, 14)
        preview_caption = QLabel("미리보기" if self._korean else "Preview")
        preview_caption.setObjectName("mutedLabel")
        preview_layout.addWidget(preview_caption)
        self.preview_before = QLabel("첫 번째 가사 줄" if self._korean else "First lyric line")
        self.preview_before.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_before.setMinimumHeight(44)
        preview_layout.addWidget(self.preview_before)
        self.preview = QLabel("가사 미리보기" if self._korean else "Lyric preview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(72)
        self.preview_detail = QLabel()
        self.preview_detail.setObjectName("mutedLabel")
        self.preview_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_layout.addWidget(self.preview)
        preview_layout.addWidget(self.preview_detail)
        panel_layout.addWidget(preview_frame)

        form = QFormLayout()
        form.setVerticalSpacing(12)
        form.setHorizontalSpacing(18)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.color_button = QPushButton()
        self.color_button.setFixedWidth(150)
        self.color_button.setAutoDefault(False)
        self.size_mode = QComboBox()
        self.size_mode.addItem(
            "기본 크기에서 조절" if self._korean else "Offset from base size", "relative",
        )
        self.size_mode.addItem(
            "고정 크기" if self._korean else "Fixed size", "absolute",
        )
        self.font_size = QDoubleSpinBox()
        self.font_size.setDecimals(0)
        self.font_size.setSingleStep(1.0)
        self.font_weight = QComboBox()
        weights = (
            ("얇게 · 300", "보통 · 400", "중간 · 500", "세미 볼드 · 600",
             "굵게 · 700", "매우 굵게 · 800", "블랙 · 900")
            if self._korean else
            ("Light · 300", "Regular · 400", "Medium · 500", "Semi bold · 600",
             "Bold · 700", "Extra bold · 800", "Black · 900")
        )
        for label, value in zip(weights, (300, 400, 500, 600, 700, 800, 900)):
            self.font_weight.addItem(label, value)
        self.italic = QCheckBox("기울임꼴" if self._korean else "Italic")
        form.addRow("색상" if self._korean else "Color", self.color_button)
        form.addRow("크기 방식" if self._korean else "Size mode", self.size_mode)
        form.addRow("글꼴 크기" if self._korean else "Font size", self.font_size)
        form.addRow("글자 굵기" if self._korean else "Font weight", self.font_weight)
        form.addRow("", self.italic)
        panel_layout.addLayout(form)
        panel_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setObjectName("lyricStyleScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(panel)
        body.addWidget(scroll, 1)
        root.addLayout(body, 1)

        footer = QHBoxLayout()
        reset = QPushButton(
            "전체 초기화" if self._korean else "Reset all lines"
        )
        reset.setFlat(True)
        reset.setAutoDefault(False)
        reset.setToolTip("모든 줄을 전체 스타일로 되돌립니다." if self._korean else "Restore the overall style for every line.")
        reset.clicked.connect(self._clear_all)
        footer.addWidget(reset)
        footer.addStretch(1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "적용" if self._korean else "Apply"
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setProperty("primary", True)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(
            "취소" if self._korean else "Cancel"
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        root.addLayout(footer)

        self.line_list.currentRowChanged.connect(self._load_line)
        self.enabled.toggled.connect(self._toggle_enabled)
        self.color_button.clicked.connect(self._choose_color)
        self.size_mode.currentIndexChanged.connect(self._size_mode_changed)
        self.font_size.valueChanged.connect(self._store_line)
        self.font_weight.currentIndexChanged.connect(self._store_line)
        self.italic.toggled.connect(self._store_line)
        self.line_list.setCurrentRow(0)
        self._toggle_list(False)

    def _toggle_list(self, expanded: bool) -> None:
        if not expanded and self.line_list.currentRow() >= 3:
            self.line_list.setCurrentRow(2)
        for row in range(self.line_list.count()):
            self.line_list.item(row).setHidden(not expanded and row >= 3)
        self.line_list.ensurePolished()
        margins = self.line_list.contentsMargins()
        self.line_list.setMaximumHeight(16777215 if expanded else
            sum(self.line_list.sizeHintForRow(row) for row in range(3))
            + margins.top() + margins.bottom() + self.line_list.frameWidth() * 2)
        self.expand_lines.setText(
            ("목록 접기" if expanded else "목록 펼치기") if self._korean else
            ("Collapse list" if expanded else "Expand list")
        )
        self.expand_lines.setAccessibleDescription(
            "2~4번째 줄만 표시합니다. 펼치면 12번째 줄까지 볼 수 있습니다." if self._korean else
            "Shows lines 2–4. Expand to see through line 12."
        )

    def styles(self) -> list[dict[str, object]]:
        styles = [dict(style) for style in self._styles]
        while styles and not styles[-1]:
            styles.pop()
        return styles

    def _style(self, index: int) -> dict[str, object]:
        while len(self._styles) <= index:
            self._styles.append({})
        return self._styles[index]

    def _load_line(self, row: int) -> None:
        if row < 0:
            return
        index = row + 1
        self._loading = True
        try:
            style = self._style(index)
            active = bool(style)
            self.line_title.setText(
                f"{index + 1}번째 줄" if self._korean else f"Line {index + 1}"
            )
            self.enabled.setChecked(active)
            self._set_color(str(style.get("color", self._source.outline_color)))
            relative = "font_size" not in style
            self.size_mode.setCurrentIndex(self.size_mode.findData(
                "relative" if relative else "absolute"
            ))
            self._size_mode_value = "relative" if relative else "absolute"
            self._configure_size_spin(relative)
            self.font_size.setValue(float(
                style.get("font_size_offset", 0.0) if relative
                else style.get("font_size", self._source.font_size)
            ))
            self._update_size_prefix()
            weight = int(style.get("font_weight", self._source.font_weight))
            weight_index = min(
                range(self.font_weight.count()),
                key=lambda candidate: abs(int(self.font_weight.itemData(candidate)) - weight),
            )
            self.font_weight.setCurrentIndex(weight_index)
            self.italic.setChecked(bool(style.get("italic", self._source.text_italic)))
            self._set_fields_enabled(active)
            self._refresh_preview()
        finally:
            self._loading = False

    def _toggle_enabled(self, enabled: bool) -> None:
        if self._loading:
            return
        self._set_fields_enabled(enabled)
        if enabled:
            self._store_line()
        else:
            row = self.line_list.currentRow()
            index = row + 1
            self._styles[index] = {}
            self._refresh_line_item(index)
            self._load_line(row)

    def _set_fields_enabled(self, enabled: bool) -> None:
        for widget in (
            self.color_button, self.size_mode, self.font_size,
            self.font_weight, self.italic,
        ):
            widget.setEnabled(enabled)

    def _size_mode_changed(self, _index: int) -> None:
        if self._loading:
            return
        mode = str(self.size_mode.currentData())
        previous_effective = self.font_size.value()
        if self._size_mode_value == "relative":
            previous_effective += self._source.font_size
        relative = mode == "relative"
        self._loading = True
        try:
            self._configure_size_spin(relative)
            self.font_size.setValue(
                previous_effective - self._source.font_size
                if relative else previous_effective
            )
        finally:
            self._loading = False
        self._size_mode_value = mode
        self._update_size_prefix()
        self._store_line()

    def _configure_size_spin(self, relative: bool) -> None:
        if relative:
            self.font_size.setRange(-100.0, 100.0)
        else:
            self.font_size.setRange(8.0, 120.0)
        self.font_size.setSuffix(" px")

    def _effective_size(self) -> float:
        value = self.font_size.value()
        if self.size_mode.currentData() == "relative":
            value += self._source.font_size
        return max(8.0, min(120.0, value))

    def _update_size_prefix(self) -> None:
        relative = self.size_mode.currentData() == "relative"
        self.font_size.setPrefix("+" if relative and self.font_size.value() >= 0 else "")

    def _store_line(self, _value: object = None) -> None:
        if self._loading or not self.enabled.isChecked():
            return
        self._update_size_prefix()
        style: dict[str, object] = {
            "color": self._color,
            "font_weight": int(self.font_weight.currentData()),
            "italic": self.italic.isChecked(),
        }
        if self.size_mode.currentData() == "relative":
            style["font_size_offset"] = self.font_size.value()
        else:
            style["font_size"] = self.font_size.value()
        index = self.line_list.currentRow() + 1
        self._styles[index] = style
        self._refresh_line_item(index)
        self._refresh_preview()

    def _choose_color(self) -> None:
        dialog = ColorEditorDialog(
            QColor(self._color), self._source, self._translator,
            "줄 색상 편집" if self._korean else "Edit line color",
            self, show_personal_color=False,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._set_color(ColorEditorDialog._serialized(dialog.selected_color))
            self._store_line()

    def _set_color(self, value: str) -> None:
        color = QColor(value)
        if not color.isValid():
            color = QColor(self._source.outline_color)
        self._color = ColorEditorDialog._serialized(color)
        swatch = QPixmap(16, 16)
        swatch.fill(color)
        self.color_button.setIcon(QIcon(swatch))
        self.color_button.setText(self._color)
        self.color_button.setAccessibleName(
            f"줄 색상 {self._color}" if self._korean else f"Line color {self._color}"
        )

    def _refresh_preview(self) -> None:
        index = self.line_list.currentRow() + 1
        self.preview.setText(f"{index + 1}번째 가사 줄" if self._korean else f"Lyric line {index + 1}")
        self.preview_before.setVisible(index == 1)
        effective = self._effective_size()
        family = self._source.font_family.replace("\\", "\\\\").replace('"', '\\"')
        weight = int(self.font_weight.currentData())
        italic = "italic" if self.italic.isChecked() else "normal"
        color = self._color
        display_size = effective * float(self._advanced_style.get("scale", 1))
        self.preview.setStyleSheet(
            f'color:{color}; font-family:"{family}"; '
            f"font-size:{round(min(48.0, display_size))}px; font-weight:{weight}; font-style:{italic};"
        )
        first = self._styles[0] if self._styles else {}
        first_size = max(8, min(48, float(first.get("font_size",
            self._source.font_size + float(first.get("font_size_offset", 0))))
            * float(self._advanced_style.get("scale", 1))))
        first_italic = "italic" if first.get("italic", self._source.text_italic) else "normal"
        self.preview_before.setStyleSheet(
            f'color:{first.get("color", self._source.outline_color)}; font-family:"{family}"; '
            f'font-size:{round(first_size)}px; font-weight:{first.get("font_weight", self._source.font_weight)}; '
            f'font-style:{first_italic};'
        )
        if self.size_mode.currentData() == "relative":
            offset = self.font_size.value()
            self.preview_detail.setText(
                (f"결과 {effective:g}px · 기본 {self._source.font_size:g}px · {offset:+g}px"
                 if self._korean else
                 f"Result {effective:g}px · base {self._source.font_size:g}px · {offset:+g}px")
            )
        else:
            self.preview_detail.setText(
                f"고정 {effective:g}px" if self._korean else f"Fixed {effective:g}px"
            )
        if not self.enabled.isChecked():
            self.preview_detail.setText(
                ("전체 스타일 · " if self._korean else "Overall style · ")
                + self.preview_detail.text()
            )
        if self._advanced_style:
            self.preview_detail.setText(self.preview_detail.text() + (
                " · 고급 현재 가사 크기 배율 적용" if self._korean else " · Advanced current role size scale applied"))
        if display_size > 48:
            self.preview_detail.setText(
                self.preview_detail.text()
                + (" · 미리보기는 48px로 축소" if self._korean else " · Preview scaled to 48px")
            )

    def _refresh_line_item(self, index: int) -> None:
        if not 1 <= index <= self.line_list.count():
            return
        custom = index < len(self._styles) and bool(self._styles[index])
        label = f"{index + 1}번째 줄" if self._korean else f"Line {index + 1}"
        status = (
            "개별" if self._korean else "Custom"
        ) if custom else (
            "기본" if self._korean else "Default"
        )
        self.line_list.item(index - 1).setText(f"{label}  ·  {status}")
        self.line_list.item(index - 1).setToolTip(
            "이 줄에 개별 스타일이 적용됩니다." if custom and self._korean else
            "This line uses a custom style." if custom else
            "전체 가사 스타일을 따릅니다." if self._korean else "Inherits the overall lyric style."
        )

    def _clear_all(self) -> None:
        self._styles = self._styles[:1]
        for index in range(1, self.line_list.count() + 1):
            self._refresh_line_item(index)
        self._load_line(max(0, self.line_list.currentRow()))
