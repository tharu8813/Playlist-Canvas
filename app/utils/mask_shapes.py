"""Clip shapes ("masks") a source can be cut to, shared by Canvas and export layers."""

from __future__ import annotations

from math import cos, pi, sin

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPainterPath, QPolygonF


def _polygon(rect: QRectF, points: list[tuple[float, float]]) -> QPainterPath:
    """A closed path through points given in 0..1 units of ``rect``."""
    path = QPainterPath()
    path.addPolygon(QPolygonF([
        QPointF(rect.left() + x * rect.width(), rect.top() + y * rect.height())
        for x, y in points
    ]))
    path.closeSubpath()
    return path


def _regular(sides: int, inner: float | None = None, rotation: float = -pi / 2) -> list[tuple[float, float]]:
    """Points of a regular polygon (or a star when ``inner`` is set) in 0..1 units."""
    corners = sides * (2 if inner else 1)
    return [
        (
            0.5 + 0.5 * (inner if inner and index % 2 else 1.0) * cos(rotation + 2 * pi * index / corners),
            0.5 + 0.5 * (inner if inner and index % 2 else 1.0) * sin(rotation + 2 * pi * index / corners),
        )
        for index in range(corners)
    ]


def mask_path(rect: QRectF, shape: str) -> QPainterPath:
    """The region of ``rect`` that stays visible for ``shape``."""
    path = QPainterPath()
    if shape == "circle":
        path.addEllipse(rect)
    elif shape == "pill":
        radius = min(rect.width(), rect.height()) / 2.0
        path.addRoundedRect(rect, radius, radius)
    elif shape == "arch":
        radius = min(rect.width() / 2.0, rect.height())
        path.moveTo(rect.bottomLeft())
        path.lineTo(rect.left(), rect.top() + radius)
        path.arcTo(QRectF(rect.left(), rect.top(), rect.width(), radius * 2.0), 180.0, -180.0)
        path.lineTo(rect.bottomRight())
        path.closeSubpath()
    elif shape == "diamond":
        path = _polygon(rect, [(0.5, 0.0), (1.0, 0.5), (0.5, 1.0), (0.0, 0.5)])
    elif shape == "triangle":
        path = _polygon(rect, [(0.5, 0.0), (1.0, 1.0), (0.0, 1.0)])
    elif shape == "hexagon":
        path = _polygon(rect, _regular(6, rotation=0.0))
    elif shape == "star":
        path = _polygon(rect, _regular(5, inner=0.45))
    elif shape == "heart":
        width, height = rect.width(), rect.height()
        left, top = rect.left(), rect.top()
        path.moveTo(left + width * 0.5, top + height * 0.25)
        path.cubicTo(left + width * 0.5, top, left, top, left, top + height * 0.3)
        path.cubicTo(left, top + height * 0.6, left + width * 0.5, top + height * 0.8,
                     left + width * 0.5, top + height)
        path.cubicTo(left + width * 0.5, top + height * 0.8, left + width, top + height * 0.6,
                     left + width, top + height * 0.3)
        path.cubicTo(left + width, top, left + width * 0.5, top,
                     left + width * 0.5, top + height * 0.25)
        path.closeSubpath()
    else:
        path.addRect(rect)
    return path
