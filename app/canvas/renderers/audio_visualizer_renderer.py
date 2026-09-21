"""Canvas renderer for SourceType.AUDIO_VISUALIZER, split out of
SourceItem._paint_legacy."""

from __future__ import annotations

from math import sin
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

from app.canvas.renderers.base import paint_background, paint_selection_guide

if TYPE_CHECKING:
    from app.canvas.source_item import SourceItem


def render(
    item: "SourceItem", painter: QPainter, option: object, widget: object | None = None,
) -> None:
    rect, fill, _pen = paint_background(item, painter)
    painter.setPen(Qt.PenStyle.NoPen)
    bar_count = max(4, min(96, item.source.visualizer_bars))
    gap = max(1.0, rect.width() * 0.012 / bar_count)
    bar_width = max(1.0, (rect.width() - gap * (bar_count - 1)) / bar_count)
    minimum = max(0.0, min(0.5, item.source.visualizer_min_level))
    maximum = max(minimum, min(1.0, item.source.visualizer_max_level))
    curve = max(0.25, min(3.0, item.source.visualizer_curve))
    # The editor has no playback signal, so show a representative design
    # sample.  Real preview/export frames use the audio analysis and are
    # completely flat at silence when Minimum level is zero.
    levels = [
        minimum + (maximum - minimum)
        * (0.16 + 0.76 * abs(sin(index * 0.61 + 0.8))) ** curve
        for index in range(bar_count)
    ]
    style = item.source.visualizer_style
    if style in {"line", "wave"}:
        painter.setPen(QPen(QColor(item.source.fill_color), max(
            item.source.visualizer_line_width, rect.height() * 0.025
        )))
        path = QPainterPath(QPointF(rect.left(), rect.center().y()))
        for index, level in enumerate(levels):
            x = rect.left() + index * rect.width() / max(1, bar_count - 1)
            y = rect.center().y() - (level - 0.5) * rect.height() * 0.82
            if style == "wave":
                y = rect.center().y() - sin(index * 0.42) * level * rect.height() * 0.36
            path.lineTo(x, y)
        painter.drawPath(path)
    elif style == "arc":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for index, level in enumerate(levels):
            inset = index * min(rect.width(), rect.height()) / max(1, bar_count * 3.2)
            arc_rect = rect.adjusted(inset, inset, -inset, -inset)
            arc_color = QColor(item.source.fill_color)
            arc_color.setAlpha(max(45, int(255 * level)))
            painter.setPen(QPen(arc_color, max(1.0, item.source.visualizer_line_width * 0.75)))
            painter.drawArc(arc_rect, 210 * 16, int(120 * 16 * level))
    else:
        for index, level in enumerate(levels):
            bar_height = rect.height() * level
            x = rect.left() + index * (bar_width + gap)
            if style == "led":
                segments = 8
                segment_gap = max(1.0, rect.height() * 0.025)
                segment_height = (rect.height() - segment_gap * (segments - 1)) / segments
                active = max(1, round(level * segments))
                for segment in range(active):
                    y = rect.bottom() - (segment + 1) * segment_height - segment * segment_gap
                    painter.setBrush(fill)
                    painter.drawRoundedRect(QRectF(x, y, bar_width, segment_height), 2, 2)
                continue
            if style == "center":
                bar_height = rect.height() * level
                painter.setBrush(fill)
                painter.drawRoundedRect(QRectF(x, rect.bottom() - bar_height, bar_width, bar_height), bar_width / 2, bar_width / 2)
                continue
            if style == "mirror":
                bar_height *= 0.48
                y = rect.center().y() - bar_height
                painter.setBrush(fill)
                painter.drawRoundedRect(QRectF(x, y, bar_width, bar_height), bar_width / 2, bar_width / 2)
                painter.drawRoundedRect(QRectF(x, rect.center().y(), bar_width, bar_height), bar_width / 2, bar_width / 2)
                continue
            if style == "dots":
                dot_size = max(3.0, min(bar_width * 1.35, rect.height() * 0.16))
                dot_count = max(1, int(level * 7))
                for dot_index in range(dot_count):
                    y = rect.bottom() - dot_size - dot_index * (dot_size + 3)
                    painter.setBrush(fill)
                    painter.drawEllipse(QRectF(x, y, dot_size, dot_size))
                continue
            y = rect.center().y() - bar_height / 2
            if style == "spectrum":
                spectrum_color = QColor.fromHsv(int(300 * index / max(1, bar_count - 1)), 210, 245)
                painter.setBrush(spectrum_color)
            else:
                painter.setBrush(fill)
            draw_width = bar_width * 0.62 if style == "capsule" else bar_width
            radius = draw_width / 2
            painter.drawRoundedRect(QRectF(x + (bar_width - draw_width) / 2, y, draw_width, bar_height), radius, radius)
    paint_selection_guide(item, painter, rect)
