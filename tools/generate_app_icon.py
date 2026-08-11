"""Generate the deterministic Playlist Canvas Windows icon."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen


def main() -> int:
    size = 256
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    background = QLinearGradient(34, 24, 224, 234)
    background.setColorAt(0.0, QColor("#6D5DFB"))
    background.setColorAt(0.55, QColor("#1685D1"))
    background.setColorAt(1.0, QColor("#06B6D4"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(background)
    painter.drawRoundedRect(QRectF(12, 12, 232, 232), 54, 54)

    painter.setBrush(QColor(10, 20, 38, 96))
    painter.drawRoundedRect(QRectF(35, 35, 186, 186), 42, 42)

    bars = ((58, 99, 14, 58), (81, 78, 14, 100), (104, 91, 14, 74),
            (127, 66, 14, 124), (150, 86, 14, 84), (173, 105, 14, 46))
    painter.setBrush(QColor("#DFF8FF"))
    for x, y, width, height in bars:
        painter.drawRoundedRect(QRectF(x, y, width, height), 7, 7)

    play = QPainterPath()
    play.moveTo(QPointF(103, 80))
    play.lineTo(QPointF(103, 176))
    play.lineTo(QPointF(177, 128))
    play.closeSubpath()
    painter.setPen(QPen(QColor(255, 255, 255, 220), 5, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(QColor(255, 255, 255, 242))
    painter.drawPath(play)
    painter.end()

    output = Path(__file__).resolve().parents[1] / "app" / "resources" / "app_icon.ico"
    output.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(output), "ICO"):
        raise RuntimeError("Qt could not write the Windows ICO asset.")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
