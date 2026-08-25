"""Small, non-intrusive Qt property animations for the workspace."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget


class MotionController(QObject):
    """Owns short-lived animations so Qt does not garbage-collect them early."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._animations: list[QPropertyAnimation] = []
        self._dimension_animations: dict[tuple[int, bytes], QPropertyAnimation] = {}

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

    def animate_width(
        self, widget: QWidget, start: int, end: int, duration: int = 190,
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        """Animate a sidebar's maximum width during expand/collapse."""
        self._animate_dimension(
            widget, b"maximumWidth", start, end, duration, on_finished,
        )

    def animate_height(
        self, widget: QWidget, start: int, end: int, duration: int = 190,
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        """Animate a workspace panel's maximum height during expand/collapse."""
        self._animate_dimension(
            widget, b"maximumHeight", start, end, duration, on_finished,
        )

    def _animate_dimension(
        self, widget: QWidget, property_name: bytes, start: int, end: int,
        duration: int, on_finished: Callable[[], None] | None,
    ) -> None:
        """Replace an in-flight size animation so rapid reversals stay coherent."""
        key = (id(widget), property_name)
        previous = self._dimension_animations.pop(key, None)
        if previous is not None:
            previous.stop()
            self._discard(previous)
        animation = QPropertyAnimation(widget, property_name, self)
        animation.setDuration(duration)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setEasingCurve(QEasingCurve.Type.InOutCubic)

        def finished() -> None:
            if self._dimension_animations.get(key) is animation:
                self._dimension_animations.pop(key, None)
            self._discard(animation)
            if on_finished is not None:
                on_finished()

        animation.finished.connect(finished)
        self._animations.append(animation)
        self._dimension_animations[key] = animation
        animation.start()

    def _discard(self, animation: QPropertyAnimation) -> None:
        if animation in self._animations:
            self._animations.remove(animation)
        animation.deleteLater()
