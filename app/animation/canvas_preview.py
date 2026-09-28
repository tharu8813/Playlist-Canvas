"""Non-destructive animation preview directly on a Canvas source item."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import (
    QObject, QPauseAnimation, QPointF, QSequentialAnimationGroup, QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QTransform

from app.canvas.source_item import SourceItem
from app.models.source import Source
from app.animation.curves import AnimationPose, animation_pose, loop_pose


class CanvasAnimationPreviewController(QObject):
    """Animate graphics properties and restore the item exactly afterward."""

    finished = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._group: QSequentialAnimationGroup | None = None
        self._item: SourceItem | None = None
        self._original: tuple[QPointF, float, float, float, QTransform, bool] | None = None

    @property
    def active(self) -> bool:
        return self._group is not None

    def preview(self, item: SourceItem, source: Source) -> bool:
        """Play the entrance, a stretch of the loop motion and the exit."""
        if self.active or (
            source.animation_in == "none" and source.animation_out == "none"
            and source.loop_motion == "none"
        ):
            return False
        self._item = item
        self._original = (
            QPointF(item.pos()), item.scale(), item.rotation(), item.opacity(),
            item.transform(), item.isSelected(),
        )
        item._suppress_position_sync = True
        self._set_selected_without_signal(item, False)

        def milliseconds(seconds: float) -> int:
            return max(100, min(3000, round(seconds * 1000)))

        width, height = source.width, source.height
        sequence = QSequentialAnimationGroup(self)
        if source.animation_in != "none":
            self._apply(item, source, animation_pose(source.animation_in, 0.0, True, width, height))
            sequence.addAnimation(self._phase(
                item, source, milliseconds(source.animation_in_duration),
                lambda progress: animation_pose(
                    source.animation_in, progress, True, width, height,
                ),
            ))
        if source.loop_motion != "none":
            loop_seconds = max(1.5, min(6.0, source.loop_motion_period * 2.0))
            sequence.addAnimation(self._phase(
                item, source, round(loop_seconds * 1000),
                lambda progress: loop_pose(
                    source.loop_motion, progress * loop_seconds, source.loop_motion_period,
                    source.loop_motion_amount, width, height,
                ),
            ))
        if source.animation_out != "none":
            sequence.addAnimation(QPauseAnimation(320))
            sequence.addAnimation(self._phase(
                item, source, milliseconds(source.animation_out_duration),
                lambda progress: animation_pose(
                    source.animation_out, progress, False, width, height,
                ),
            ))
        sequence.finished.connect(self._restore)
        self._group = sequence
        sequence.start()
        return True

    def cancel(self) -> None:
        if self._group is not None:
            self._group.stop()
            self._restore()

    def _phase(
        self, item: SourceItem, source: Source, duration: int,
        pose_at: Callable[[float], AnimationPose],
    ) -> QVariantAnimation:
        animation = QVariantAnimation()
        animation.setDuration(duration)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.valueChanged.connect(
            lambda value: self._apply(item, source, pose_at(float(value)))
        )
        return animation

    @staticmethod
    def _apply(item: SourceItem, source: Source, pose: AnimationPose) -> None:
        # Source opacity is already applied inside SourceItem.paint(). Graphics
        # opacity is only the animation multiplier; including source.opacity
        # here would square semi-transparent elements.
        item.setPos(source.x + pose.dx, source.y + pose.dy)
        item.setScale(source.scale * pose.scale)
        item.setRotation(source.rotation + pose.rotation)
        item.setOpacity(pose.opacity)
        center_x = source.width / 2.0
        item.setTransform(
            QTransform().translate(center_x, 0.0).scale(pose.scale_x, 1.0).translate(-center_x, 0.0)
            if pose.scale_x != 1.0 else QTransform()
        )

    def _restore(self) -> None:
        group = self._group
        item = self._item
        original = self._original
        self._group = None
        self._item = None
        self._original = None
        if item is not None and original is not None:
            position, scale, rotation, opacity, transform, selected = original
            item.setPos(position)
            item.setScale(scale)
            item.setRotation(rotation)
            item.setOpacity(opacity)
            item.setTransform(transform)
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
