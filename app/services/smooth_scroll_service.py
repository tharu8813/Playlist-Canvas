"""Application-wide animated mouse-wheel scrolling for Qt scroll areas."""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve, QEvent, QObject, QPropertyAnimation, Qt,
)
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractScrollArea, QApplication, QScrollBar, QWidget,
)


class SmoothScrollService(QObject):
    """Animate wheel input while preserving native dragging and special gestures."""

    def __init__(
        self, enabled: bool = True, duration_ms: int = 180,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._enabled = bool(enabled)
        self._duration_ms = self._validated_duration(duration_ms)
        self._installed = False
        self._animations: dict[QScrollBar, QPropertyAnimation] = {}
        self._targets: dict[QScrollBar, int] = {}

    @staticmethod
    def _validated_duration(duration_ms: int) -> int:
        return max(80, min(420, int(duration_ms)))

    def install(self) -> None:
        application = QApplication.instance()
        if application is not None and not self._installed:
            application.installEventFilter(self)
            self._installed = True

    def uninstall(self) -> None:
        application = QApplication.instance()
        if application is not None and self._installed:
            application.removeEventFilter(self)
        self._installed = False
        self._stop_animations()

    def configure(self, enabled: bool, duration_ms: int) -> None:
        """Apply persisted preferences immediately to all future wheel events."""
        self._enabled = bool(enabled)
        self._duration_ms = self._validated_duration(duration_ms)
        if not self._enabled:
            self._stop_animations()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def duration_ms(self) -> int:
        return self._duration_ms

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if not self._enabled or event.type() != QEvent.Type.Wheel:
            return super().eventFilter(watched, event)
        if not isinstance(event, QWheelEvent) or not isinstance(watched, QWidget):
            return super().eventFilter(watched, event)
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Keep Canvas Ctrl+wheel zoom and widget-specific Ctrl gestures native.
            return super().eventFilter(watched, event)

        area = self._ancestor_scroll_area(watched)
        if area is None:
            return super().eventFilter(watched, event)
        self._prepare_scroll_area(area)
        horizontal_hint = (
            isinstance(watched, QScrollBar)
            and watched.orientation() == Qt.Orientation.Horizontal
        )
        horizontal, amount = self._wheel_amount(area, event, horizontal_hint)
        if amount == 0:
            return super().eventFilter(watched, event)
        target = self._scroll_target(area, horizontal, amount)
        if target is None:
            return super().eventFilter(watched, event)
        bar, value = target
        self._animate(bar, value)
        event.accept()
        return True

    @staticmethod
    def _ancestor_scroll_area(widget: QWidget | None) -> QAbstractScrollArea | None:
        current = widget
        while current is not None:
            if isinstance(current, QAbstractScrollArea):
                return current
            current = current.parentWidget()
        return None

    @staticmethod
    def _prepare_scroll_area(area: QAbstractScrollArea) -> None:
        """Make item views animate in pixels instead of jumping whole rows."""
        if not isinstance(area, QAbstractItemView):
            return
        if area.verticalScrollMode() != QAbstractItemView.ScrollMode.ScrollPerPixel:
            area.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        if area.horizontalScrollMode() != QAbstractItemView.ScrollMode.ScrollPerPixel:
            area.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

    @staticmethod
    def _wheel_amount(
        area: QAbstractScrollArea, event: QWheelEvent,
        horizontal_hint: bool = False,
    ) -> tuple[bool, int]:
        pixel = event.pixelDelta()
        angle = event.angleDelta()
        shift_horizontal = bool(
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        )
        horizontal = horizontal_hint or shift_horizontal
        if not horizontal:
            horizontal = abs(pixel.x() or angle.x()) > abs(pixel.y() or angle.y())
        raw = pixel.x() if horizontal else pixel.y()
        if horizontal and raw == 0 and shift_horizontal:
            raw = pixel.y()
        bar = area.horizontalScrollBar() if horizontal else area.verticalScrollBar()
        if raw == 0:
            raw = angle.x() if horizontal else angle.y()
            if horizontal and raw == 0 and shift_horizontal:
                raw = angle.y()
            if isinstance(area, QAbstractItemView):
                # Item views often report a one-pixel singleStep immediately
                # after switching from row scrolling. Use a readable wheel
                # distance while retaining high-resolution touchpad deltas.
                wheel_step = max(36, min(72, area.fontMetrics().height() * 3))
            else:
                wheel_step = max(1, bar.singleStep()) * 3
            raw = round((raw / 120.0) * wheel_step)
        return horizontal, -int(raw)

    def _scroll_target(
        self, area: QAbstractScrollArea, horizontal: bool, amount: int,
    ) -> tuple[QScrollBar, int] | None:
        current_area: QAbstractScrollArea | None = area
        while current_area is not None:
            bar = (
                current_area.horizontalScrollBar()
                if horizontal else current_area.verticalScrollBar()
            )
            active_target = self._targets.get(bar, bar.value())
            target = max(bar.minimum(), min(bar.maximum(), active_target + amount))
            if target != active_target:
                return bar, target
            if bar.value() != active_target:
                # The inner area is still travelling to its boundary.
                return bar, active_target
            current_area = self._ancestor_scroll_area(current_area.parentWidget())
        return None

    def _animate(self, bar: QScrollBar, target: int) -> None:
        running = self._animations.pop(bar, None)
        if running is not None:
            running.stop()
            running.deleteLater()
        self._targets[bar] = target
        animation = QPropertyAnimation(bar, b"value", self)
        animation.setDuration(self._duration_ms)
        animation.setStartValue(bar.value())
        animation.setEndValue(target)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.finished.connect(
            lambda active=animation, scroll_bar=bar: self._animation_finished(
                scroll_bar, active
            )
        )
        self._animations[bar] = animation
        animation.start()

    def _animation_finished(
        self, bar: QScrollBar, animation: QPropertyAnimation,
    ) -> None:
        if self._animations.get(bar) is not animation:
            return
        self._animations.pop(bar, None)
        self._targets.pop(bar, None)
        animation.deleteLater()

    def _stop_animations(self) -> None:
        for animation in self._animations.values():
            animation.stop()
            animation.deleteLater()
        self._animations.clear()
        self._targets.clear()
