"""Canvas renderer for SourceType.IMAGE, split out of SourceItem._paint_legacy."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QPainter

from app.canvas.renderers.base import (
    paint_background,
    paint_generic_fallback,
    paint_image_content,
    paint_selection_guide,
)

if TYPE_CHECKING:
    from app.canvas.source_item import SourceItem


def render(
    item: "SourceItem", painter: QPainter, option: object, widget: object | None = None,
) -> None:
    rect, _fill, _pen = paint_background(item, painter)
    if not item._pixmap.isNull():
        paint_image_content(item, painter, rect, "rounded")
    else:
        paint_generic_fallback(item, painter, rect)
    paint_selection_guide(item, painter, rect)
