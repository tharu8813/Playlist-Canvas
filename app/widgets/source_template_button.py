"""Clickable and draggable buttons used by the source-template palette."""

from __future__ import annotations

import json

from PySide6.QtCore import QMimeData, QPoint, Qt
from PySide6.QtGui import QDrag, QMouseEvent
from PySide6.QtWidgets import QApplication, QPushButton


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
        self._drag_start = QPoint()

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
