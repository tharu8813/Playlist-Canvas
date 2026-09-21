"""Canvas renderer for SourceType.LYRICS, split out of SourceItem._paint_legacy."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter

from app.canvas.renderers.base import paint_background, paint_selection_guide

if TYPE_CHECKING:
    from app.canvas.source_item import SourceItem


def render(
    item: "SourceItem", painter: QPainter, option: object, widget: object | None = None,
) -> None:
    rect, _fill, _pen = paint_background(item, painter)
    painter.drawRoundedRect(rect, item.source.border_radius, item.source.border_radius)
    # Preview replaces the single editor placeholder with previous,
    # current, and next timed cues. Keep that expanded stack inside the
    # element's actual Canvas rectangle so its apparent position cannot
    # drift beyond the resize handles, especially for older 90 px-high
    # lyric elements.
    painter.save()
    painter.setClipRect(rect)
    lines = [line for line in (item._render_text() or item.source.subtitle_fallback).splitlines() if line.strip()]
    current_line = item.source.subtitle_current_line
    current_line_count = max(1, item.source.subtitle_current_line_count)
    has_current_line = 0 <= current_line < len(lines)
    if not has_current_line:
        current_line = -1
    line_height = item._lyric_line_height()
    anchor_line = item._subtitle_anchor_line
    anchor_count = max(1, item._subtitle_anchor_line_count)
    if 0 <= anchor_line < len(lines):
        # Keep the displayed cue vertically anchored. As the context
        # window changes, CanvasSnapshot animates its old position into
        # this one instead of re-centering the whole text block abruptly.
        y = (
            rect.center().y()
            - (anchor_line + anchor_count / 2.0) * line_height
            + item.source.subtitle_scroll_offset
        )
    else:
        total_height = line_height * len(lines)
        y = (
            rect.center().y() - total_height / 2
            + item.source.subtitle_scroll_offset
        )
    transition = max(0.0, min(1.0, item._subtitle_transition_progress))
    transition_style = item.source.subtitle_animation
    is_animated = transition_style in {"glow", "rise"}
    # Two deliberately different entrances.
    #  * glow: the line materialises in place — alpha 0, a small lift,
    #    a subtle upscale, and a soft blur that sharpens.
    #  * rise: no blur, no scale; a crisp, longer upward slide with a
    #    quicker partial fade.
    if transition_style == "rise":
        enter_alpha, enter_offset, enter_scale, enter_blur = 0.28, 22.0, 1.0, 0.0
    else:  # glow (also the migrated default for older styles)
        enter_alpha, enter_offset, enter_scale, enter_blur = 0.0, 4.0, 0.955, 7.0
    steady_previous_alpha = max(
        0.05, min(0.9, item.source.subtitle_previous_opacity),
    )
    # When no previous line is kept on screen, the outgoing cue leaves
    # entirely — fade it out over the transition instead of cutting it.
    leaving_previous_target = (
        steady_previous_alpha
        if current_line > 0 or item.source.subtitle_context_lines > 0
        else 0.0
    )
    for index, line in enumerate(lines):
        is_current = (
            has_current_line
            and current_line <= index < current_line + current_line_count
        )
        is_previous = has_current_line and index < current_line
        line_color = QColor(item.source.outline_color)
        if is_current and is_animated:
            line_color.setAlphaF(
                enter_alpha + (1.0 - enter_alpha) * transition
            )
        elif not is_current:
            is_leaving = (
                transition < 1.0
                and item._subtitle_leaving_line_count > 0
                and index < item._subtitle_leaving_line_count
            )
            immediate_previous = (
                is_previous
                and item._subtitle_previous_line_count > 0
                and current_line - item._subtitle_previous_line_count
                <= index < current_line
            )
            # Fade the oldest context cue to zero while it scrolls out.
            # The immediately previous cue separately cross-fades from
            # current emphasis toward its resting context opacity.
            if is_leaving:
                leaving_start_alpha = (
                    1.0
                    if current_line <= 0 and item.source.subtitle_context_lines == 0
                    else steady_previous_alpha
                )
                line_alpha = leaving_start_alpha * (1.0 - transition)
            elif immediate_previous and transition < 1.0:
                line_alpha = (
                    leaving_previous_target
                    + (1.0 - leaving_previous_target) * (1.0 - transition)
                )
            else:
                line_alpha = steady_previous_alpha
            line_color.setAlphaF(line_alpha)
            blur_radius = (
                max(
                    0,
                    round(
                        item.source.subtitle_previous_blur
                        * (transition if immediate_previous else 1.0)
                    ),
                )
                if is_previous else 0
            )
            if blur_radius:
                ghost = QColor(line_color)
                ghost.setAlpha(
                    line_color.alpha() // 3
                    if is_leaving else
                    max(10, line_color.alpha() // 3)
                )
                ghost_pixmap = item._lyric_ghost_pixmap(
                    line, ghost, blur_radius, rect.width() - 24, line_height,
                )
                painter.drawPixmap(
                    round(rect.left() + 12 - blur_radius), round(y - blur_radius), ghost_pixmap,
                )
        lyric_font = QFont(item._lyric_fonts["current" if is_current else "regular"])
        current_y = y
        line_transform_saved = False
        if is_current and is_animated:
            current_y += enter_offset * (1.0 - transition)
            line_scale = enter_scale + (1.0 - enter_scale) * transition
            if line_scale < 0.9999:
                line_center = QPointF(
                    rect.center().x(), current_y + line_height / 2.0,
                )
                painter.save()
                painter.translate(line_center)
                painter.scale(line_scale, line_scale)
                painter.translate(-line_center)
                line_transform_saved = True
            reveal_blur = enter_blur * (1.0 - transition)
            if reveal_blur >= 0.75:
                ghost = QColor(line_color)
                ghost.setAlpha(max(8, round(line_color.alpha() * 0.18)))
                ghost_pixmap = item._lyric_ghost_pixmap(
                    line, ghost, max(1, round(reveal_blur)), rect.width() - 24, line_height,
                )
                painter.drawPixmap(
                    round(rect.left() + 12 - reveal_blur),
                    round(current_y - reveal_blur), ghost_pixmap,
                )
        painter.setFont(lyric_font)
        painter.setPen(line_color)
        item._draw_text(
            painter,
            QRectF(rect.left() + 12, current_y, rect.width() - 24, line_height),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap,
            line,
        )
        if line_transform_saved:
            painter.restore()
        y += line_height
    painter.restore()
    paint_selection_guide(item, painter, rect)
