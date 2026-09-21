"""Shared Canvas paint preamble/epilogue for per-type renderers.

Extracted verbatim from SourceItem._paint_legacy's shared setup (opacity,
fill/gradient/pen, shadow) and shared selection-guide overlay, so per-type
renderer modules do not each re-derive them. No pixels change: a type
using paint_background()/paint_selection_guide() around its own drawing
produces the same output as the equivalent branch used to inside
_paint_legacy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPen

from app.models.source import SourceType

if TYPE_CHECKING:
    from app.canvas.source_item import SourceItem


def paint_background(item: "SourceItem", painter: QPainter) -> tuple[QRectF, QBrush, QPen]:
    """Apply opacity, resolve fill/pen, paint the shadow. Returns (rect, fill, pen).

    Pairs with paint_selection_guide(), which closes the painter.save() this
    opens.
    """
    rect = item.content_rect()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.save()
    # Multiply, never replace: the scene sets the painter opacity to this
    # item's animated QGraphicsItem.opacity() before paint(), and the entrance
    # / exit fades rely on that being carried through.
    painter.setOpacity(painter.opacity() * item.source.opacity)
    # Colour / gradient backgrounds carry no bitmap, so brightness and
    # contrast are folded into the paint colour here instead.
    tint = (
        item._adjust_fill_color
        if item.source.source_type is SourceType.BACKGROUND and item._pixmap.isNull()
        else (lambda color: color)
    )
    fill = QBrush(tint(QColor(item.source.fill_color)))
    if item.source.gradient.enabled:
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0, tint(QColor(item.source.gradient.start_color)))
        gradient.setColorAt(1, tint(QColor(item.source.gradient.end_color)))
        fill = QBrush(gradient)
    pen = (
        QPen(QColor(item.source.outline_color), item.source.outline_width)
        if item.source.outline_width > 0
        else QPen(Qt.PenStyle.NoPen)
    )
    painter.setPen(pen)
    painter.setBrush(fill)

    if item.source.shadow.enabled:
        shadow_color = QColor(item.source.shadow.color)
        shadow_color.setAlphaF(max(0.0, min(1.0, item.source.shadow.opacity)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shadow_color)
        shadow_rect = rect.translated(item.source.shadow.offset_x, item.source.shadow.offset_y)
        spread = max(0.0, item.source.shadow.blur_radius * 0.18)
        painter.drawRoundedRect(
            shadow_rect.adjusted(-spread, -spread, spread, spread),
            item.source.border_radius + spread,
            item.source.border_radius + spread,
        )
        painter.setPen(pen)
        painter.setBrush(fill)
    return rect, fill, pen


def paint_selection_guide(item: "SourceItem", painter: QPainter, rect: QRectF) -> None:
    """Close the painter.save() from paint_background() and draw the selection overlay."""
    painter.restore()
    if not item.isSelected():
        return
    guide_color = QColor("#55B8FF")
    if item.source.opacity <= 0.25:
        guide_color = QColor(255, 255, 255, 190)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(guide_color, 1.5, Qt.PenStyle.DashLine))
    painter.drawRect(rect)
    painter.setPen(QPen(QColor("#FFFFFF"), 1.0))
    painter.setBrush(guide_color)
    for handle_rect in item.resize_handle_rects().values():
        painter.drawRect(handle_rect)
    painter.setPen(QPen(guide_color, 1.5))
    painter.drawLine(QPointF(item.source.width / 2, 0), QPointF(item.source.width / 2, -23))
    painter.setBrush(guide_color)
    painter.drawEllipse(item.rotation_handle_rect())
