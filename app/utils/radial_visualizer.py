"""Circular (radial) visualizer bars shared by the Canvas and reactive export layers."""

from __future__ import annotations

from collections.abc import Sequence
from math import cos, pi, radians, sin

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen


def paint_radial_bars(
    painter: QPainter, rect: QRectF, levels: Sequence[float], color: QColor,
    line_width: float, inner_ratio: float,
) -> None:
    """Draw bars radiating out of a ring, mirrored left/right from the top.

    ``inner_ratio`` is the ring radius as a share of the largest circle that
    fits ``rect``; the space inside it is left free for a centred cover.
    """
    count = len(levels)
    if count == 0 or rect.isEmpty():
        return
    center = rect.center()
    outer = min(rect.width(), rect.height()) / 2.0
    inner = outer * max(0.1, min(0.95, inner_ratio))
    reach = max(1.0, outer - inner - line_width / 2.0)
    # Half the circle per side, so bass sits at the top and both sides match.
    step = 180.0 / count
    circumference_share = 2.0 * pi * inner / (count * 2)
    pen = QPen(color, max(1.0, min(line_width, circumference_share * 0.7)))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.save()
    painter.setPen(pen)
    for index, level in enumerate(levels):
        length = reach * max(0.03, min(1.0, float(level)))
        for side in (1.0, -1.0):
            angle = radians(-90.0 + side * (index + 0.5) * step)
            direction = QPointF(cos(angle), sin(angle))
            painter.drawLine(center + direction * inner, center + direction * (inner + length))
    painter.restore()
