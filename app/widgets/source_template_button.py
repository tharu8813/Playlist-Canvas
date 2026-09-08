"""Clickable and draggable buttons used by the source-template palette."""

from __future__ import annotations

import json

from PySide6.QtCore import QMimeData, QPoint, Qt, QTimer
from PySide6.QtGui import QDrag, QIcon, QMouseEvent
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout,
)


SOURCE_TEMPLATE_MIME = "application/x-playlist-canvas-source-template"


def source_template_mime_data(source_type: str, parent_type: str) -> QMimeData:
    """Build the private payload used when a palette template is dragged."""
    mime_data = QMimeData()
    payload = json.dumps(
        {"source_type": source_type, "parent_type": parent_type},
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    mime_data.setData(SOURCE_TEMPLATE_MIME, payload)
    return mime_data


def read_source_template_mime(mime_data: QMimeData) -> tuple[str, str] | None:
    """Return a validated ``(template, parent)`` pair from a drag payload."""
    if not mime_data.hasFormat(SOURCE_TEMPLATE_MIME):
        return None
    try:
        payload = json.loads(bytes(mime_data.data(SOURCE_TEMPLATE_MIME)).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    source_type = payload.get("source_type")
    parent_type = payload.get("parent_type")
    if not isinstance(source_type, str) or not source_type:
        return None
    if not isinstance(parent_type, str) or not parent_type:
        return None
    return source_type, parent_type


class SourceTemplateButton(QPushButton):
    """Preserve click-to-add while also starting a copy drag after movement."""

    def __init__(self, source_type: str, parent_type: str, parent=None) -> None:
        super().__init__(parent)
        self.source_type = source_type
        self.parent_type = parent_type
        self._variant = False
        self._drag_start = QPoint()
        self.setObjectName("sourceTemplateButton")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(72)
        self.setProperty("variant", False)
        card_layout = QHBoxLayout(self)
        card_layout.setContentsMargins(6, 6, 6, 6)
        card_layout.setSpacing(9)
        self.icon_label = QLabel(self)
        self.icon_label.setObjectName("sourceTemplateIcon")
        self.icon_label.setFixedSize(26, 26)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        text_layout = QVBoxLayout()
        self._text_layout = text_layout
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(1)
        self.title_label = QLabel(self)
        self.title_label.setObjectName("sourceTemplateTitle")
        self.description_label = QLabel(self)
        self.description_label.setObjectName("sourceTemplateDescription")
        self.description_label.setWordWrap(True)
        for label in (self.title_label, self.description_label):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            label.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred,
            )
        card_layout.setAlignment(self.icon_label, Qt.AlignmentFlag.AlignTop)
        text_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.description_label)
        card_layout.addWidget(self.icon_label)
        card_layout.addLayout(text_layout, 1)

    def set_card_icon(self, icon: QIcon) -> None:
        """Set the palette icon independently of QPushButton's text layout."""
        self.icon_label.setPixmap(icon.pixmap(22, 22))

    def set_card_text(self, title: str, description: str, *, variant: bool = False) -> None:
        """Update the rich card labels without changing click/drag behavior."""
        self.title_label.setText(title)
        self.description_label.setText(description)
        self.setAccessibleName(title)
        self.setProperty("paletteText", f"{title} {description}")
        self.setProperty("variant", variant)
        self._variant = variant
        self.setMinimumHeight(62 if variant else 72)
        layout = self.layout()
        if isinstance(layout, QHBoxLayout):
            layout.setContentsMargins(9, 6 if variant else 8, 10, 6 if variant else 8)
        self.style().unpolish(self)
        self.style().polish(self)
        QTimer.singleShot(0, self._fit_card_height)

    def _fit_card_height(self) -> None:
        """Keep wrapped descriptions from being compressed by a narrow panel."""
        margins = self.layout().contentsMargins()
        base_height = 62 if self._variant else 72
        required = self._text_layout.sizeHint().height() + margins.top() + margins.bottom()
        self.setMinimumHeight(max(base_height, required))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        QTimer.singleShot(0, self._fit_card_height)

    def create_mime_data(self) -> QMimeData:
        """Expose payload creation independently for drop handling and tests."""
        return source_template_mime_data(self.source_type, self.parent_type)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() is Qt.MouseButton.LeftButton:
            self._drag_start = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        if (event.position().toPoint() - self._drag_start).manhattanLength() < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return
        drag = QDrag(self)
        drag.setMimeData(self.create_mime_data())
        preview = self.grab()
        drag.setPixmap(preview)
        drag.setHotSpot(self._drag_start)
        # A drag is an insertion gesture of its own. Clear QPushButton's
        # pressed state so releasing the mouse after the drop cannot also
        # trigger the click-to-add path.
        self.setDown(False)
        drag.exec(Qt.DropAction.CopyAction)
        self._drag_start = QPoint()
