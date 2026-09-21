"""Canvas renderer for SourceType.NOW_PLAYING, split out of
SourceItem._paint_legacy."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen

from app.canvas.renderers.base import paint_background, paint_selection_guide

if TYPE_CHECKING:
    from app.canvas.source_item import SourceItem


def render(
    item: "SourceItem", painter: QPainter, option: object, widget: object | None = None,
) -> None:
    rect, _fill, _pen = paint_background(item, painter)
    card_color = QColor(item.source.fill_color)
    text_color = QColor(item.source.outline_color)
    if item.source.now_playing_style == "minimal":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(Qt.PenStyle.NoPen)
    elif item.source.now_playing_style == "glass":
        if card_color.lightness() > 185:
            card_color = QColor("#1B2638")
            text_color = QColor("#F8FAFC")
        card_color.setAlpha(205)
        painter.setBrush(card_color)
        outline = QColor(text_color)
        outline.setAlpha(185)
        painter.setPen(QPen(outline, 1.5))
    elif card_color.lightness() > 220 and text_color.lightness() > 190:
        text_color = QColor("#172033")
    painter.drawRoundedRect(rect, item.source.border_radius, item.source.border_radius)
    lines = [line for line in (item._render_text() or "NOW PLAYING").splitlines() if line.strip()]
    label = lines[0] if lines else "NOW PLAYING"
    title = lines[1] if len(lines) > 1 else item.source.name
    details = " · ".join(lines[2:]) if len(lines) > 2 else ""
    painter.setPen(text_color)
    label_font = QFont(item.source.font_family, max(9, min(20, int(item.source.font_size * 0.56))))
    label_font.setWeight(QFont.Weight.DemiBold)
    painter.setFont(label_font)
    item._draw_text(painter, rect.adjusted(16, 12, -16, -8), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, label.upper())
    title_font = QFont(item.source.font_family, max(14, min(52, int(item.source.font_size * 1.22))))
    title_font.setWeight(QFont.Weight.Bold)
    painter.setFont(title_font)
    item._draw_text(painter, rect.adjusted(16, rect.height() * 0.25, -16, -rect.height() * 0.34),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap, title)
    if details:
        detail_color = QColor(text_color)
        detail_color.setAlpha(190)
        painter.setPen(detail_color)
        detail_font = QFont(item.source.font_family, max(10, min(24, int(item.source.font_size * 0.68))))
        painter.setFont(detail_font)
        item._draw_text(painter, rect.adjusted(16, rect.height() * 0.66, -16, -10),
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom | Qt.TextFlag.TextWordWrap, details)
    paint_selection_guide(item, painter, rect)
