"""Canvas renderer for SourceType.LYRICS, split out of SourceItem._paint_legacy.

Every row is painted from one *emphasis* value (0 = context, 1 = current):
opacity, scale and blur all blend from it. A cue change is therefore one
continuous motion -- the outgoing line dims, shrinks and softens while the
incoming one brightens and grows -- instead of swapping fonts or alphas on a
single frame. CanvasSnapshot supplies the transient timing state.
"""

from __future__ import annotations

from math import pi, sin
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter

from app.canvas.renderers.base import (
    device_pixel_ratio, paint_background, paint_selection_guide,
)

if TYPE_CHECKING:
    from app.canvas.source_item import SourceItem

_ALIGNMENTS = {
    "left": Qt.AlignmentFlag.AlignLeft,
    "right": Qt.AlignmentFlag.AlignRight,
}
# Entrance travel for a cue that was not on screen before it started.
_RISE_DISTANCE = 22.0
_GLOW_DISTANCE = 6.0
# The glow style keeps a soft halo on the current line and blooms it on entry.
_GLOW_HALO = 0.3
_GLOW_BLOOM = 0.35
_VISIBLE = 0.004


def render(
    item: "SourceItem", painter: QPainter, option: object, widget: object | None = None,
) -> None:
    rect, _fill, _pen = paint_background(item, painter)
    painter.drawRoundedRect(rect, item.source.border_radius, item.source.border_radius)
    paint_lines(item, painter, rect)
    paint_selection_guide(item, painter, rect)


def _mix(start: QColor, end: QColor, amount: float) -> QColor:
    return QColor.fromRgbF(*(
        a + (b - a) * amount
        for a, b in zip(start.getRgbF(), end.getRgbF())
    ))


def paint_lines(item: "SourceItem", painter: QPainter, rect: QRectF) -> None:
    source = item.source
    # Preview replaces the single editor placeholder with previous, current,
    # and next timed cues. Keep that expanded stack inside the element's
    # actual Canvas rectangle so its apparent position cannot drift beyond the
    # resize handles, especially for older 90 px-high lyric elements.
    painter.save()
    painter.setClipRect(rect)
    lines = [
        line for line in (item._render_text() or source.subtitle_fallback).splitlines()
        if line.strip()
    ]
    current_line = source.subtitle_current_line
    current_line_count = max(1, source.subtitle_current_line_count)
    has_current_line = 0 <= current_line < len(lines)
    if not has_current_line:
        current_line = -1
    line_height = item._lyric_line_height()
    anchor_line = item._subtitle_anchor_line
    anchor_valid = 0 <= anchor_line < len(lines)
    if anchor_valid:
        # Keep the displayed cue vertically anchored. As the context window
        # changes, CanvasSnapshot animates its old position into this one
        # instead of re-centering the whole text block abruptly.
        y = (
            rect.center().y()
            - (anchor_line + max(1, item._subtitle_anchor_line_count) / 2.0) * line_height
            + source.subtitle_scroll_offset
        )
    else:
        y = rect.center().y() - line_height * len(lines) / 2 + source.subtitle_scroll_offset

    t = max(0.0, min(1.0, item._subtitle_transition_progress))
    transitioning = t < 1.0
    style = source.subtitle_animation
    rest_alpha = max(0.05, min(0.9, source.subtitle_previous_opacity))
    previous_blur = max(0.0, float(source.subtitle_previous_blur))
    # One font for every row: context rows are the same glyphs scaled down,
    # so wrapping and weight never jump when a row changes role.
    font = item._lyric_fonts["current"]
    rest_scale = item._lyric_fonts["regular"].pointSizeF() / max(1.0, font.pointSizeF())
    glow_radius = max(3.0, font.pointSizeF() * 0.22)
    text_color = QColor(source.outline_color)
    accent_color = QColor(source.subtitle_accent_color) if source.subtitle_accent_enabled else text_color
    alignment = _ALIGNMENTS.get(source.text_alignment, Qt.AlignmentFlag.AlignHCenter)
    flags = alignment | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap
    content_width = rect.width() - 24
    # Rows shrink toward the edge they are aligned to, so they stay flush.
    scale_x = {
        Qt.AlignmentFlag.AlignLeft: rect.left() + 12,
        Qt.AlignmentFlag.AlignRight: rect.right() - 12,
    }.get(alignment, rect.center().x())
    pixel_ratio = device_pixel_ratio(painter)
    base_opacity = painter.opacity()
    boundary = current_line if has_current_line else (anchor_line if anchor_valid else -1)
    previous_count = item._subtitle_previous_line_count if transitioning else 0
    leaving_count = item._subtitle_leaving_line_count if transitioning else 0
    entering_from = (
        len(lines) - item._subtitle_entering_line_count if transitioning else len(lines)
    )
    painter.setFont(font)

    for index, line in enumerate(lines):
        row = QRectF(rect.left() + 12, y, content_width, line_height)
        y += line_height
        is_current = (
            has_current_line and current_line <= index < current_line + current_line_count
        )
        offset = 0.0
        extra_scale = 1.0
        reveal = 0.0      # share of the row still shown as its soft glow
        glow = 0.0        # halo opacity behind the sharp row
        blur_mix = 0.0    # share of the row shown with the previous-line blur
        if is_current:
            if transitioning:
                emphasis = t
                if item._subtitle_incoming_visible:
                    # Already on screen as an upcoming row: the scroll moves
                    # it, so only brighten it from its resting opacity.
                    alpha = rest_alpha + (1.0 - rest_alpha) * t
                elif style == "rise":
                    alpha = min(1.0, t / 0.55)
                    offset = _RISE_DISTANCE * (1.0 - t)
                else:
                    alpha = t
                    offset = _GLOW_DISTANCE * (1.0 - t)
                    extra_scale = 0.96 + 0.04 * t
                    reveal = 1.0 - t
            else:
                emphasis = item._subtitle_emphasis
                alpha = rest_alpha + (1.0 - rest_alpha) * emphasis
            if style == "glow" and transitioning:
                glow = _GLOW_BLOOM * sin(pi * t)
        else:
            emphasis = 0.0
            alpha = rest_alpha
            if 0 <= current_line - previous_count <= index < current_line:
                # The cue that was current a moment ago hands its emphasis over.
                emphasis = item._subtitle_previous_emphasis * (1.0 - t)
                alpha = rest_alpha + (1.0 - rest_alpha) * emphasis
                blur_mix = t
            elif index < boundary:
                blur_mix = 1.0
            if index < leaving_count:
                alpha *= 1.0 - t
            elif index >= entering_from:
                alpha *= t
        if style == "glow":
            glow += _GLOW_HALO * emphasis * alpha
        if alpha <= _VISIBLE:
            continue
        if previous_blur < 0.5:
            blur_mix = 0.0

        painter.save()
        scale = (rest_scale + (1.0 - rest_scale) * emphasis) * extra_scale
        row.translate(0.0, offset)
        if abs(scale - 1.0) > 1e-4:
            center = QPointF(scale_x, row.center().y())
            painter.translate(center)
            painter.scale(scale, scale)
            painter.translate(-center)
        for radius, color, opacity in (
            (glow_radius, accent_color, alpha * reveal + glow),
            (previous_blur, text_color, alpha * blur_mix),
        ):
            if opacity > _VISIBLE:
                pixmap, margin = item._lyric_blur_pixmap(
                    line, color, radius, content_width, line_height, pixel_ratio, flags,
                )
                painter.setOpacity(base_opacity * min(1.0, opacity))
                painter.drawPixmap(QPointF(row.left() - margin, row.top() - margin), pixmap)
        sharp = alpha * (1.0 - reveal) * (1.0 - blur_mix)
        if sharp > _VISIBLE:
            # Opacity, not pen alpha, so the glyph outline dims with the fill.
            painter.setOpacity(base_opacity * sharp)
            painter.setPen(
                _mix(text_color, accent_color, emphasis)
                if source.subtitle_accent_enabled else text_color
            )
            item._draw_text(painter, row, flags, line)
        painter.restore()
    painter.restore()
