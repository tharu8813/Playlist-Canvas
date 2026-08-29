"""Non-destructive animation preview directly on a Canvas source item."""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve, QObject, QPointF, QParallelAnimationGroup, QPauseAnimation,
    QPropertyAnimation, QSequentialAnimationGroup, Signal,
)

from app.canvas.source_item import SourceItem
from app.models.source import Source
from app.animation.curves import (
    hidden_opacity_factor, hidden_rotation_offset, hidden_scale_factor,
    slide_distance,
)


class CanvasAnimationPreviewController(QObject):
    """Animate graphics properties and restore the item exactly afterward."""

    finished = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._group: QSequentialAnimationGroup | None = None
        self._item: SourceItem | None = None
        self._original: tuple[QPointF, float, float, float, bool] | None = None

    @property
    def active(self) -> bool:
        return self._group is not None

    def preview(self, item: SourceItem, source: Source) -> bool:
        """Play configured entrance and exit styles without changing the model."""
        if self.active or (
            source.animation_in == "none" and source.animation_out == "none"
        ):
            return False
        self._item = item
        self._original = (
            QPointF(item.pos()), item.scale(), item.rotation(), item.opacity(),
            item.isSelected(),
        )
        item._suppress_position_sync = True
        self._set_selected_without_signal(item, False)

        entrance_duration = max(
            100, min(3000, round(source.animation_in_duration * 1000)),
        )
        exit_duration = max(
            100, min(3000, round(source.animation_out_duration * 1000)),
        )
        sequence = QSequentialAnimationGroup(self)
        if source.animation_in != "none":
            sequence.addAnimation(
                self._phase(
                    item, source, source.animation_in, entrance_duration, entering=True,
                )
            )
        if source.animation_out != "none":
            sequence.addAnimation(QPauseAnimation(320))
            sequence.addAnimation(
                self._phase(
                    item, source, source.animation_out, exit_duration, entering=False,
                )
            )
        sequence.finished.connect(self._restore)
        self._group = sequence
        sequence.start()
        return True

    def cancel(self) -> None:
        if self._group is not None:
            self._group.stop()
            self._restore()

    def _phase(
        self, item: SourceItem, source: Source, style: str, duration: int,
        entering: bool,
    ) -> QParallelAnimationGroup:
        normal_position = QPointF(source.x, source.y)
        normal_scale = source.scale
        distance = slide_distance(source.width, source.height)
        offset = {
            "slide_left": QPointF(-distance, 0.0),
            "slide_right": QPointF(distance, 0.0),
            "slide_up": QPointF(0.0, -distance),
            "slide_down": QPointF(0.0, distance),
        }.get(style, QPointF())
        hidden_position = normal_position + offset
        hidden_scale = normal_scale * hidden_scale_factor(style)
        normal_rotation = source.rotation
        hidden_rotation = normal_rotation + hidden_rotation_offset(style, entering)
        # Source opacity is already applied inside SourceItem.paint(). Graphics
        # opacity is only the animation multiplier; including source.opacity here
        # would square semi-transparent elements during preview/export.
        normal_opacity = 1.0
        hidden_opacity = normal_opacity * hidden_opacity_factor(style)

        group = QParallelAnimationGroup()
        motion_easing = (
            QEasingCurve.Type.OutQuint if entering
            else QEasingCurve.Type.InQuint
        )

        def add_property_animation(
            property_name: bytes, start_value: object, end_value: object,
            easing: QEasingCurve.Type,
        ) -> None:
            """Add only properties that actually move during this style."""
            if start_value == end_value:
                return
            animation = QPropertyAnimation(item, property_name)
            animation.setDuration(duration)
            animation.setStartValue(start_value)
            animation.setEndValue(end_value)
            animation.setEasingCurve(easing)
            group.addAnimation(animation)

        if entering:
            item.setPos(hidden_position)
            item.setScale(hidden_scale)
            item.setRotation(hidden_rotation)
            item.setOpacity(hidden_opacity)
            start_position, end_position = hidden_position, normal_position
            start_scale, end_scale = hidden_scale, normal_scale
            start_rotation, end_rotation = hidden_rotation, normal_rotation
            start_opacity, end_opacity = hidden_opacity, normal_opacity
        else:
            item.setPos(normal_position)
            item.setScale(normal_scale)
            item.setRotation(normal_rotation)
            item.setOpacity(normal_opacity)
            start_position, end_position = normal_position, hidden_position
            start_scale, end_scale = normal_scale, hidden_scale
            start_rotation, end_rotation = normal_rotation, hidden_rotation
            start_opacity, end_opacity = normal_opacity, hidden_opacity

        add_property_animation(b"pos", start_position, end_position, motion_easing)
        add_property_animation(b"scale", start_scale, end_scale, motion_easing)
        add_property_animation(
            b"rotation", start_rotation, end_rotation, motion_easing,
        )
        add_property_animation(
            b"opacity", start_opacity, end_opacity,
            QEasingCurve.Type.InOutCubic,
        )
        return group

    def _restore(self) -> None:
        group = self._group
        item = self._item
        original = self._original
        self._group = None
        self._item = None
        self._original = None
        if item is not None and original is not None:
            position, scale, rotation, opacity, selected = original
            item.setPos(position)
            item.setScale(scale)
            item.setRotation(rotation)
            item.setOpacity(opacity)
            item._suppress_position_sync = False
            self._set_selected_without_signal(item, selected)
            item.update()
        if group is not None:
            group.deleteLater()
        self.finished.emit()

    @staticmethod
    def _set_selected_without_signal(item: SourceItem, selected: bool) -> None:
        scene = item.scene()
        if scene is None:
            item.setSelected(selected)
            return
        blocked = scene.blockSignals(True)
        try:
            item.setSelected(selected)
        finally:
            scene.blockSignals(blocked)
