"""Shared audio level-meter drawing for Canvas, preview, and export."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen


def _level_color(level: float, low: str, mid: str, high: str) -> QColor:
    if level >= 0.82:
        return QColor(high)
    if level >= 0.58:
        return QColor(mid)
    return QColor(low)


def paint_level_meter(
    painter: QPainter,
    rect: QRectF,
    levels: Sequence[float],
    peaks: Sequence[float] | None = None,
    *,
    style: str = "gradient",
    orientation: str = "vertical",
    segments: int = 16,
    gap: float = 4.0,
    track_color: str = "#263244",
    low_color: str = "#22C55E",
    mid_color: str = "#FACC15",
    high_color: str = "#EF4444",
    show_peak: bool = True,
) -> None:
    """Draw mono or stereo levels using one consistent visual implementation."""
    channel_count = max(1, len(levels))
    gap = max(0.0, min(30.0, float(gap)))
    segments = max(3, min(64, int(segments)))
    vertical = orientation != "horizontal"
    for channel, raw_level in enumerate(levels):
        level = max(0.0, min(1.0, float(raw_level)))
        if vertical:
            channel_width = max(1.0, (rect.width() - gap * (channel_count - 1)) / channel_count)
            channel_rect = QRectF(
                rect.left() + channel * (channel_width + gap), rect.top(),
                channel_width, rect.height(),
            )
        else:
            channel_height = max(1.0, (rect.height() - gap * (channel_count - 1)) / channel_count)
            channel_rect = QRectF(
                rect.left(), rect.top() + channel * (channel_height + gap),
                rect.width(), channel_height,
            )
        radius = min(channel_rect.width(), channel_rect.height()) * 0.18
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(track_color))
        painter.drawRoundedRect(channel_rect, radius, radius)

        if style in {"led", "segments"}:
            for segment in range(segments):
                ratio = (segment + 1) / segments
                if ratio > level:
                    continue
                segment_gap = max(1.0, (channel_rect.height() if vertical else channel_rect.width()) * 0.008)
                if vertical:
                    segment_height = max(
                        1.0, (channel_rect.height() - segment_gap * (segments - 1)) / segments,
                    )
                    segment_rect = QRectF(
                        channel_rect.left(),
                        channel_rect.bottom() - (segment + 1) * segment_height - segment * segment_gap,
                        channel_rect.width(), segment_height,
                    )
                else:
                    segment_width = max(
                        1.0, (channel_rect.width() - segment_gap * (segments - 1)) / segments,
                    )
                    segment_rect = QRectF(
                        channel_rect.left() + segment * (segment_width + segment_gap),
                        channel_rect.top(), segment_width, channel_rect.height(),
                    )
                painter.setBrush(_level_color(ratio, low_color, mid_color, high_color))
                painter.drawRoundedRect(segment_rect, min(2.0, radius), min(2.0, radius))
        else:
            if vertical:
                active_rect = QRectF(
                    channel_rect.left(), channel_rect.bottom() - channel_rect.height() * level,
                    channel_rect.width(), channel_rect.height() * level,
                )
            else:
                active_rect = QRectF(
                    channel_rect.left(), channel_rect.top(),
                    channel_rect.width() * level, channel_rect.height(),
                )
            if style == "gradient":
                gradient = QLinearGradient(
                    active_rect.left(), active_rect.bottom(), active_rect.left(), active_rect.top()
                ) if vertical else QLinearGradient(
                    active_rect.left(), active_rect.top(), active_rect.right(), active_rect.top()
                )
                gradient.setColorAt(0.0, QColor(low_color))
                gradient.setColorAt(0.62, QColor(mid_color))
                gradient.setColorAt(1.0, QColor(high_color))
                painter.setBrush(gradient)
            else:
                painter.setBrush(_level_color(level, low_color, mid_color, high_color))
            painter.drawRoundedRect(active_rect, radius, radius)

        if show_peak and peaks is not None and channel < len(peaks):
            peak = max(0.0, min(1.0, float(peaks[channel])))
            painter.setPen(QPen(QColor(high_color if peak >= 0.82 else mid_color), 2.0))
            if vertical:
                y = channel_rect.bottom() - channel_rect.height() * peak
                painter.drawLine(channel_rect.left(), y, channel_rect.right(), y)
            else:
                x = channel_rect.left() + channel_rect.width() * peak
                painter.drawLine(x, channel_rect.top(), x, channel_rect.bottom())

