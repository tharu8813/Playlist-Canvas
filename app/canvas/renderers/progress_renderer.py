"""Canvas renderer for SourceType.PROGRESS_BAR, split out of SourceItem._paint_legacy."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter

from app.canvas.renderers.base import paint_background, paint_selection_guide

if TYPE_CHECKING:
    from app.canvas.source_item import SourceItem


def render(
    item: "SourceItem", painter: QPainter, option: object, widget: object | None = None,
) -> None:
    rect, fill, _pen = paint_background(item, painter)
    style = item.source.progress_style
    radius = 0.0 if style == "youtube" else rect.height() / 2
    track_color = QColor(item.source.progress_track_color)
    if style == "apple":
        track_color = QColor(item.source.fill_color)
        track_color.setAlpha(80)
    elif style == "spotify":
        track_color = QColor("#1B2530")
    painter.setBrush(track_color)
    painter.drawRoundedRect(rect, radius, radius)
    painter.setBrush(fill)
    progress_width = rect.width() * max(0.0, min(1.0, item.source.progress_value))
    if style == "apple":
        progress_width = rect.width() * max(0.0, min(1.0, item.source.progress_value))
    painter.drawRoundedRect(QRectF(0, 0, progress_width, rect.height()), radius, radius)
    if style == "spotify":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(
            QPointF(progress_width, rect.center().y()), rect.height() * 0.34, rect.height() * 0.34,
        )
    paint_selection_guide(item, painter, rect)
