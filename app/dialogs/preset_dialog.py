"""Built-in design preset selection dialog."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem,
    QVBoxLayout, QWidget,
)

from app.presets.preset_service import PresetDefinition, PresetService
from app.utils.i18n import Translator


class DesignPresetDialog(QDialog):
    """Select and apply one built-in visual design preset."""

    def __init__(self, translator: Translator, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.translator = translator
        self.setMinimumSize(520, 430)
        self.resize(620, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)
        self.heading = QLabel()
        self.heading.setObjectName("panelTitle")
        self.description = QLabel()
        self.description.setObjectName("mutedLabel")
        self.description.setWordWrap(True)
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("presetList")
        layout.addWidget(self.heading)
        layout.addWidget(self.description)
        layout.addWidget(self.list_widget, 1)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply
        )
        self.button_box.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(
            self.accept
        )
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

        self.list_widget.currentRowChanged.connect(self._update_description)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self.accept())
        translator.language_changed.connect(self.retranslate)
        self.retranslate()

    def retranslate(self) -> None:
        """Refresh translated preset names while preserving the selection."""
        selected_id = self.selected_preset.identifier if self.selected_preset else None
        korean = self.translator.language.value == "ko"
        self.setWindowTitle("디자인 프리셋" if korean else "Design Presets")
        self.heading.setText("디자인 프리셋 선택" if korean else "Choose a design preset")
        self.button_box.button(QDialogButtonBox.StandardButton.Apply).setText(
            "적용" if korean else "Apply"
        )
        self.button_box.button(QDialogButtonBox.StandardButton.Cancel).setText(
            "취소" if korean else "Cancel"
        )
        self.list_widget.clear()
        for preset in PresetService.all():
            item = QListWidgetItem(preset.name(self.translator.language.value))
            item.setData(Qt.ItemDataRole.UserRole, preset.identifier)
            self.list_widget.addItem(item)
            if preset.identifier == selected_id:
                self.list_widget.setCurrentItem(item)
        if self.list_widget.currentItem() is None and self.list_widget.count():
            self.list_widget.setCurrentRow(0)
        self._update_description()

    @property
    def selected_preset(self) -> PresetDefinition | None:
        """Return the selected preset definition."""
        current = self.list_widget.currentItem()
        if current is None:
            return None
        identifier = current.data(Qt.ItemDataRole.UserRole)
        return next(
            (preset for preset in PresetService.all() if preset.identifier == identifier),
            None,
        )

    def _update_description(self) -> None:
        preset = self.selected_preset
        self.description.setText(
            preset.description(self.translator.language.value) if preset else ""
        )
        self.button_box.button(QDialogButtonBox.StandardButton.Apply).setEnabled(
            preset is not None
        )


# Keep third-party imports compatible with releases that exposed this name.
PresetDialog = DesignPresetDialog
