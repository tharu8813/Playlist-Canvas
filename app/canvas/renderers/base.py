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
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen

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


def paint_image_content(item: "SourceItem", painter: QPainter, rect: QRectF, frame_style: str) -> None:
    """Draw the item's pixmap clipped to frame_style. Extracted verbatim from
    _paint_legacy's shared image_backed_types branch (frame_style was that
    branch's own SourceType.ALBUM_COVER special case, now a parameter)."""
    display_rect = rect
    clip_path = QPainterPath()
    if frame_style == "circle":
        painter.drawEllipse(rect)
        clip_path.addEllipse(rect)
    elif frame_style == "polaroid":
        painter.setPen(QPen(QColor("#FFFFFF"), max(1.0, item.source.outline_width)))
        painter.setBrush(QColor("#F8FAFC"))
        painter.drawRoundedRect(rect, 5, 5)
        display_rect = rect.adjusted(14, 14, -14, -46)
        clip_path.addRect(display_rect)
    else:
        painter.drawRoundedRect(rect, item.source.border_radius, item.source.border_radius)
        clip_path.addRoundedRect(rect, item.source.border_radius, item.source.border_radius)
    painter.setClipPath(clip_path)
    if item.source.image_fit_mode == "stretch":
        target = display_rect
    else:
        ratio = item._pixmap.width() / max(1, item._pixmap.height())
        rect_ratio = display_rect.width() / max(1, display_rect.height())
        contain = item.source.image_fit_mode == "contain"
        width_limited = ratio > rect_ratio
        if contain == width_limited:
            target = QRectF(display_rect.left(), display_rect.center().y() - display_rect.width() / ratio / 2,
                            display_rect.width(), display_rect.width() / ratio)
        else:
            target = QRectF(display_rect.center().x() - display_rect.height() * ratio / 2, display_rect.top(),
                            display_rect.height() * ratio, display_rect.height())
    painter.drawPixmap(target, item._pixmap, item._pixmap.rect())
    painter.setClipping(False)
    if frame_style == "glass":
        painter.setBrush(QColor(255, 255, 255, 40))
        painter.setPen(QPen(QColor(255, 255, 255, 180), 1.5))
        painter.drawRoundedRect(rect, item.source.border_radius, item.source.border_radius)


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
