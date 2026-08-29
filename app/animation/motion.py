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
        # QGraphicsEffect is exclusive per widget. Never replace an existing
        # shadow/blur effect just to play a cosmetic fade.
        if widget.graphicsEffect() is not None:
            return
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
        start_value = int(start)
        if previous is not None:
            previous.stop()
            start_value = self._dimension_value(widget, property_name, start_value)
            self._discard(previous)

        end_value = int(end)
        if start_value == end_value:
            widget.setProperty(property_name.decode("ascii"), end_value)
            if on_finished is not None:
                on_finished()
            return

        full_distance = max(1, abs(end_value - int(start)))
        remaining_distance = abs(end_value - start_value)
        effective_duration = max(
            80,
            round(max(1, duration) * min(1.0, remaining_distance / full_distance)),
        )
        animation = QPropertyAnimation(widget, property_name, self)
        animation.setDuration(effective_duration)
        animation.setStartValue(start_value)
        animation.setEndValue(end_value)
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

    @staticmethod
    def _dimension_value(
        widget: QWidget, property_name: bytes, fallback: int,
    ) -> int:
        if property_name == b"maximumWidth":
            return int(widget.maximumWidth())
        if property_name == b"maximumHeight":
            return int(widget.maximumHeight())
        value = widget.property(property_name.decode("ascii"))
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    def _discard(self, animation: QPropertyAnimation) -> None:
        if animation in self._animations:
            self._animations.remove(animation)
        animation.deleteLater()
