"""Small, non-intrusive Qt property animations for the workspace."""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget


class MotionController(QObject):
    """Owns short-lived animations so Qt does not garbage-collect them early."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._animations: list[QPropertyAnimation] = []

    def fade_in(self, widget: QWidget, duration: int = 180) -> None:
        """Gently fade a panel in after a major workspace state change."""
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.55)
        widget.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(duration)
        animation.setStartValue(0.55)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        def finished() -> None:
            if widget.graphicsEffect() is effect:
                widget.setGraphicsEffect(None)
            self._discard(animation)

        animation.finished.connect(finished)
        self._animations.append(animation)
        animation.start()

    def animate_width(self, widget: QWidget, start: int, end: int,
                      duration: int = 190) -> None:
        """Animate a sidebar's maximum width during expand/collapse."""
        animation = QPropertyAnimation(widget, b"maximumWidth", self)
        animation.setDuration(duration)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        animation.finished.connect(lambda: self._discard(animation))
        self._animations.append(animation)
        animation.start()

    def _discard(self, animation: QPropertyAnimation) -> None:
        if animation in self._animations:
            self._animations.remove(animation)
        animation.deleteLater()
