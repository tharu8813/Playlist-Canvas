"""Canvas renderer for SourceType.AUDIO_WAVEFORM, split out of
SourceItem._paint_legacy."""

from __future__ import annotations

from math import sin
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

from app.canvas.renderers.base import paint_background, paint_selection_guide

if TYPE_CHECKING:
    from app.canvas.source_item import SourceItem


def render(
    item: "SourceItem", painter: QPainter, option: object, widget: object | None = None,
) -> None:
    rect, _fill, _pen = paint_background(item, painter)
    painter.setPen(QPen(QColor(item.source.fill_color), max(1.0, item.source.visualizer_line_width)))
    path = QPainterPath(QPointF(rect.left(), rect.center().y()))
    points = max(24, min(128, item.source.visualizer_bars))
    for index in range(points):
        x = rect.left() + index * rect.width() / max(1, points - 1)
        level = 0.22 + 0.64 * abs(sin(index * 0.38 + 0.8))
        y = rect.center().y() - sin(index * 0.72) * level * rect.height() * 0.36
        path.lineTo(x, y)
    painter.drawPath(path)
    paint_selection_guide(item, painter, rect)
