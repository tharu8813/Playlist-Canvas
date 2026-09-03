"""Offscreen thumbnail rendering for the design-preset picker."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap

from app.canvas.live_canvas import CanvasScene
from app.canvas.source_item import SourceItem
from app.models.source import Source


def render_preset_thumbnail(
    sources: list[Source], width: int, height: int,
) -> QPixmap:
    """Rasterize a preset's sources onto a small artboard-sized pixmap.

    Album covers render with their placeholder fill because no track artwork is
    available here; that is acceptable for a layout preview.
    """
    width = max(1, int(width))
    height = max(1, int(height))
    scene = CanvasScene()
    scene.show_grid = False
    # Text tokens (%title% …) render as their ``(Title)`` placeholders here,
    # the same way they appear on the editing canvas.
    items: list[SourceItem] = []
    for source in sources:
        item = SourceItem(Source.from_dict(source.to_dict()))
        scene.addItem(item)
        items.append(item)

    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.black)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    scene.render(
        painter,
        QRectF(0, 0, width, height),
        scene.artboard_rect,
        Qt.AspectRatioMode.IgnoreAspectRatio,
    )
    painter.end()

    for item in items:
        item.release_video_decoder()
        scene.removeItem(item)
    return QPixmap.fromImage(image)
