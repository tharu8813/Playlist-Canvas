"""Live canvas and graphics scene for the Phase 1A editor."""

from __future__ import annotations

from math import ceil, floor
from pathlib import Path

from PySide6.QtCore import QLineF, QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QAction, QColor, QContextMenuEvent, QCursor, QDragEnterEvent, QDropEvent,
    QKeySequence, QPainter, QPen, QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication, QGraphicsScene, QGraphicsView, QLabel, QMenu,
)

from app.canvas.source_item import SourceItem
from app.models.source import Source
from app.services.project_content_service import LYRICS_EXTENSIONS
from app.services.source_store import SourceStore
from app.utils.i18n import Translator
from app.widgets.source_template_button import (
    SOURCE_TEMPLATE_MIME,
    read_source_template_mime,
)


class CanvasScene(QGraphicsScene):
    """Scene that renders an artboard inside a gridded editing workspace."""

    artboard_rect = QRectF(0, 0, 1280, 720)

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        # Canvas items move and animate frequently.  A linear lookup is faster
        # than continually maintaining Qt's BSP tree at this project scale.
        self.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.NoIndex)
        self.setSceneRect(-240, -180, 1760, 1080)
        self.show_grid = True
        self.snap_enabled = True
        self.workspace_color = QColor("#171B22")
        self.artboard_color = QColor("#202733")
        self.grid_color = QColor(255, 255, 255, 18)
        self.artboard_border_color = QColor("#5F6B7A")
        self.suppress_render_background = False
        # The editing canvas shows text tokens (``%title%`` …) as readable
        # ``(제목)`` / ``(Title)`` placeholders; this only picks their language.
        self.placeholder_labels_korean = False
        self.guide_x: float | None = None
        self.guide_y: float | None = None
        self._painted_guide_x: float | None = None
        self._painted_guide_y: float | None = None
        self._interactive_items: set[SourceItem] = set()
        self._interactive_anchor: SourceItem | None = None
        self._interactive_snap_delta = QPointF()
        self._snap_candidates_x: list[float] | None = None
        self._snap_candidates_y: list[float] | None = None

    def set_placeholder_language(self, korean: bool) -> None:
        """Repaint token placeholders such as ``%title%`` in the given language."""
        korean = bool(korean)
        if korean == self.placeholder_labels_korean:
            return
        self.placeholder_labels_korean = korean
        for item in self.items():
            if isinstance(item, SourceItem):
                item.update()

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        """Draw workspace, artboard and grid behind source items."""
        if self.suppress_render_background:
            return
        painter.fillRect(rect, self.workspace_color)
        painter.fillRect(self.artboard_rect, self.artboard_color)
        painter.setPen(QPen(self.artboard_border_color, 2))
        painter.drawRect(self.artboard_rect)

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        """Draw editor-only grid and temporary alignment guides above backgrounds."""
        if self.show_grid:
            grid_pen = QPen(self.grid_color, 1)
            grid_pen.setCosmetic(True)
            painter.setPen(grid_pen)
            step = 40
            x_positions, y_positions = self.grid_positions(rect, step)
            for x in x_positions:
                painter.drawLine(QLineF(x, rect.top(), x, rect.bottom()))
            for y in y_positions:
                painter.drawLine(QLineF(rect.left(), y, rect.right(), y))
        painter.setPen(QPen(QColor("#F5C542"), 1.5, Qt.PenStyle.DashLine))
        if self.guide_x is not None:
            painter.drawLine(self.guide_x, self.artboard_rect.top(), self.guide_x,
                             self.artboard_rect.bottom())
        if self.guide_y is not None:
            painter.drawLine(self.artboard_rect.left(), self.guide_y,
                             self.artboard_rect.right(), self.guide_y)

    @staticmethod
    def grid_positions(rect: QRectF, step: int = 40) -> tuple[range, range]:
        """Return origin-anchored grid coordinates covering an exposed scene area."""
        safe_step = max(1, int(step))

        def axis(start: float, end: float) -> range:
            first = floor(start / safe_step) * safe_step
            last = ceil(end / safe_step) * safe_step
            return range(first, last + safe_step, safe_step)

        return axis(rect.left(), rect.right()), axis(rect.top(), rect.bottom())

    def snap_position(self, item: SourceItem, position: QPointF) -> QPointF:
        """Snap a moving item to the grid and nearby item edges/centres."""
        if (item in self._interactive_items and self._interactive_anchor is not None
                and item is not self._interactive_anchor):
            # Preserve the spacing of a multi-selection by applying the snapped
            # adjustment chosen by the grabbed item to every selected item.
            return position + self._interactive_snap_delta
        self.guide_x = None
        self.guide_y = None
        if (not self.snap_enabled or QApplication.keyboardModifiers()
                & Qt.KeyboardModifier.AltModifier):
            if item is self._interactive_anchor:
                self._interactive_snap_delta = QPointF()
            return position
        threshold = 7.0
        grid = 10.0
        x = round(position.x() / grid) * grid
        y = round(position.y() / grid) * grid
        item_width = item.source.width * item.source.scale
        item_height = item.source.height * item.source.scale
        candidates_x, candidates_y = self._alignment_candidates(item)
        own_x = (x, x + item_width / 2, x + item_width)
        own_y = (y, y + item_height / 2, y + item_height)
        for target in candidates_x:
            nearest = min(own_x, key=lambda edge: abs(edge - target))
            if abs(nearest - target) <= threshold:
                x += target - nearest
                self.guide_x = target
                break
        for target in candidates_y:
            nearest = min(own_y, key=lambda edge: abs(edge - target))
            if abs(nearest - target) <= threshold:
                y += target - nearest
                self.guide_y = target
                break
        snapped = QPointF(x, y)
        if item is self._interactive_anchor:
            self._interactive_snap_delta = snapped - position
        return snapped

    def snap_resize(self, item: SourceItem, width: float, height: float,
                    handle: str = "se") -> tuple[float, float]:
        """Snap an active resize edge to the grid and visible alignment edges."""
        self.guide_x = None
        self.guide_y = None
        if (not self.snap_enabled or QApplication.keyboardModifiers()
                & Qt.KeyboardModifier.AltModifier):
            return width, height

        grid = 10.0
        threshold = 7.0
        # Rotated sources retain predictable local-grid sizing.  Scene-edge alignment
        # would otherwise require projecting each rotated edge and feel jumpy.
        horizontal = "left" if "w" in handle else "right" if "e" in handle else None
        vertical = "top" if "n" in handle else "bottom" if "s" in handle else None
        if abs(item.rotation()) > 0.01:
            if horizontal is not None:
                width = round(width / grid) * grid
            if vertical is not None:
                height = round(height / grid) * grid
            return max(32.0, width), max(24.0, height)

        scale = max(0.1, item.source.scale)
        content_top_left = item.mapToScene(QPointF(0, 0))
        content_bottom_right = item.mapToScene(
            QPointF(item.source.width, item.source.height)
        )
        if horizontal == "right":
            edge = content_top_left.x() + width * scale
            width += (round(edge / grid) * grid - edge) / scale
        elif horizontal == "left":
            edge = content_bottom_right.x() - width * scale
            width += (edge - round(edge / grid) * grid) / scale
        if vertical == "bottom":
            edge = content_top_left.y() + height * scale
            height += (round(edge / grid) * grid - edge) / scale
        elif vertical == "top":
            edge = content_bottom_right.y() - height * scale
            height += (edge - round(edge / grid) * grid) / scale

        candidates_x, candidates_y = self._alignment_candidates(item)

        if horizontal == "right":
            edge = content_top_left.x() + width * scale
            for target in candidates_x:
                if abs(edge - target) <= threshold:
                    width += (target - edge) / scale
                    self.guide_x = target
                    break
        elif horizontal == "left":
            edge = content_bottom_right.x() - width * scale
            for target in candidates_x:
                if abs(edge - target) <= threshold:
                    width += (edge - target) / scale
                    self.guide_x = target
                    break
        if vertical == "bottom":
            edge = content_top_left.y() + height * scale
            for target in candidates_y:
                if abs(edge - target) <= threshold:
                    height += (target - edge) / scale
                    self.guide_y = target
                    break
        elif vertical == "top":
            edge = content_bottom_right.y() - height * scale
            for target in candidates_y:
                if abs(edge - target) <= threshold:
                    height += (edge - target) / scale
                    self.guide_y = target
                    break
        return max(32.0, width), max(24.0, height)

    def update_alignment_guides(self, item: SourceItem) -> None:
        """Repaint only old/new guide strips instead of the complete scene."""
        if (item in self._interactive_items and self._interactive_anchor is not None
                and item is not self._interactive_anchor):
            return
        for rect in self._guide_update_rects(
            self._painted_guide_x, self._painted_guide_y,
            self.guide_x, self.guide_y,
        ):
            self.update(rect)
        self._painted_guide_x = self.guide_x
        self._painted_guide_y = self.guide_y

    def clear_alignment_guides(self) -> None:
        """Remove guides when a drag ends."""
        if any(value is not None for value in (
            self.guide_x, self.guide_y,
            self._painted_guide_x, self._painted_guide_y,
        )):
            for rect in self._guide_update_rects(
                self._painted_guide_x, self._painted_guide_y, None, None,
            ):
                self.update(rect)
        self.guide_x = None
        self.guide_y = None
        self._painted_guide_x = None
        self._painted_guide_y = None

    def begin_item_interaction(
        self, item: SourceItem, include_selection: bool = False,
    ) -> None:
        """Cache stable snap targets and defer model notifications during a gesture."""
        candidates = (
            [entry for entry in self.selectedItems()
             if isinstance(entry, SourceItem) and not entry.source.locked]
            if include_selection and item.isSelected() else [item]
        )
        if item not in candidates:
            candidates.append(item)
        self._interactive_items = set(candidates)
        self._interactive_anchor = item
        self._interactive_snap_delta = QPointF()
        for entry in self._interactive_items:
            entry._begin_user_interaction()
        self._snap_candidates_x, self._snap_candidates_y = self._build_alignment_candidates(
            self._interactive_items
        )

    def finish_item_interaction(self) -> None:
        """Publish one consolidated model change per transformed source."""
        items = list(self._interactive_items)
        self._interactive_items.clear()
        self._interactive_anchor = None
        self._interactive_snap_delta = QPointF()
        self._snap_candidates_x = None
        self._snap_candidates_y = None
        self.clear_alignment_guides()
        for item in items:
            item._commit_user_interaction()

    def is_item_interactive(self, item: SourceItem) -> bool:
        return item in self._interactive_items

    def _alignment_candidates(self, item: SourceItem) -> tuple[list[float], list[float]]:
        if (item in self._interactive_items
                and self._snap_candidates_x is not None
                and self._snap_candidates_y is not None):
            return self._snap_candidates_x, self._snap_candidates_y
        return self._build_alignment_candidates({item})

    def _build_alignment_candidates(
        self, excluded: set[SourceItem],
    ) -> tuple[list[float], list[float]]:
        candidates_x = [
            self.artboard_rect.left(), self.artboard_rect.center().x(),
            self.artboard_rect.right(),
        ]
        candidates_y = [
            self.artboard_rect.top(), self.artboard_rect.center().y(),
            self.artboard_rect.bottom(),
        ]
        for other in self.items():
            if (not isinstance(other, SourceItem) or other in excluded
                    or not other.isVisible()):
                continue
            bounds = other.sceneBoundingRect()
            candidates_x.extend((bounds.left(), bounds.center().x(), bounds.right()))
            candidates_y.extend((bounds.top(), bounds.center().y(), bounds.bottom()))
        return candidates_x, candidates_y

    def _guide_update_rects(
        self, old_x: float | None, old_y: float | None,
        new_x: float | None, new_y: float | None,
    ) -> list[QRectF]:
        margin = 4.0
        rects: list[QRectF] = []
        for x in {old_x, new_x} - {None}:
            rects.append(QRectF(
                float(x) - margin, self.artboard_rect.top(), margin * 2,
                self.artboard_rect.height(),
            ))
        for y in {old_y, new_y} - {None}:
            rects.append(QRectF(
                self.artboard_rect.left(), float(y) - margin,
                self.artboard_rect.width(), margin * 2,
            ))
        return rects

    def set_artboard_size(self, width: float, height: float) -> None:
        """Apply a saved artboard size and retain a generous editing workspace."""
        width = max(64.0, width)
        height = max(64.0, height)
        self.artboard_rect = QRectF(0, 0, width, height)
        self.setSceneRect(-240, -180, width + 480, height + 360)
        self.update()

    def set_theme_colors(self, workspace: QColor, artboard: QColor,
                         grid: QColor, border: QColor) -> None:
        """Apply theme-aware colors to the graphics workspace and artboard."""
        self.workspace_color = workspace
        self.artboard_color = artboard
        self.grid_color = grid
        self.artboard_border_color = border
        self.update()


class LiveCanvas(QGraphicsView):
    """Zoomable and pannable canvas synchronized with a SourceStore."""

    selection_changed = Signal(object)
    zoom_changed = Signal(float)
    files_dropped = Signal(list, object)
    source_template_dropped = Signal(str, str, object)
    cut_requested = Signal()
    copy_requested = Signal()
    paste_requested = Signal()
    command_requested = Signal(str)
    nudge_requested = Signal(float, float)
    edit_requested = Signal(str)
    rename_requested = Signal()

    _ARROW_DELTAS = {
        Qt.Key.Key_Left: (-1.0, 0.0),
        Qt.Key.Key_Right: (1.0, 0.0),
        Qt.Key.Key_Up: (0.0, -1.0),
        Qt.Key.Key_Down: (0.0, 1.0),
    }

    def __init__(self, store: SourceStore, translator: Translator,
                 parent: object | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.translator = translator
        self.scene_model = CanvasScene(self)
        self.setScene(self.scene_model)
        self._items: dict[str, SourceItem] = {}
        self._retired_items: dict[str, SourceItem] = {}
        self._panning = False
        self._space_panning = False
        self._setting_selection = False
        self._scene_selection_pending = False
        self._pan_origin = QPoint()
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#171B22"))
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        # QGraphicsView is a QAbstractScrollArea, so the viewport is the widget
        # physically under the pointer during an external drag.  Explicitly
        # enable drops on both the view and its viewport to make file drags
        # reliable across Qt/PySide versions.
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.scene_model.selectionChanged.connect(self._on_selection_changed)
        self.scene_model.selectionChanged.connect(self.scene_model.clear_alignment_guides)
        store.source_added.connect(self.add_source)
        store.source_removed.connect(self.remove_source)
        store.source_changed.connect(self.update_source)
        store.sources_replaced.connect(self.replace_sources)
        store.selection_set_changed.connect(self._select_sources_from_store)

        self._interaction_hint = QLabel(self.viewport())
        self._interaction_hint.setObjectName("canvasInteractionHint")
        self._interaction_hint.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )
        self._interaction_hint.setStyleSheet(
            "background: rgba(16,20,28,0.86); color: #F5F7FA;"
            " padding: 3px 7px; border-radius: 5px; font-weight: 600;"
        )
        self._interaction_hint.hide()

    def add_source(self, source: Source) -> None:
        """Create or reactivate a source graphics item."""
        item = self._retired_items.pop(source.id, None)
        if item is None:
            item = SourceItem(source)
            item.changed_by_user.connect(self._on_item_changed)
            item.duplicate_requested.connect(self._duplicate_source_at)
            item.edit_requested.connect(self.edit_requested)
            item.interaction_hint.connect(self._show_interaction_hint)
            self.scene_model.addItem(item)
        else:
            item.source = source
            item.apply_source()
        self._items[source.id] = item

    def remove_source(self, source_id: str) -> None:
        """Remove a source graphics item."""
        item = self._items.pop(source_id, None)
        if item:
            self._retire_item(source_id, item)

    def replace_sources(self) -> None:
        """Safely rebuild all graphics items after a bulk project or preset change."""
        self._setting_selection = True
        try:
            self.scene_model.blockSignals(True)
            self.scene_model.clearSelection()
            expected = {source.id: source for source in self.store.sources()}
            for source_id, item in list(self._items.items()):
                source = expected.pop(source_id, None)
                if source is None:
                    self._items.pop(source_id)
                    self._retire_item(source_id, item)
                    continue
                item.source = source
                item.apply_source()
            for source in expected.values():
                self.add_source(source)
        finally:
            self.scene_model.blockSignals(False)
            self._setting_selection = False
        self.scene_model.clear_alignment_guides()

    def _retire_item(self, source_id: str, item: SourceItem) -> None:
        """Hide removed items for safe reuse by Undo instead of deleting Qt objects."""
        item.setSelected(False)
        item.setVisible(False)
        item.release_video_decoder()
        self._retired_items[source_id] = item

    def prune_retired_items(self, retained_source_ids: set[str]) -> None:
        """Destroy retired items that no remaining Undo snapshot can restore."""
        for source_id in tuple(self._retired_items):
            if source_id in retained_source_ids:
                continue
            item = self._retired_items.pop(source_id)
            item.release_video_decoder()
            self.scene_model.removeItem(item)
            item.deleteLater()

    def release_video_decoders(self) -> None:
        """Release every active or retired decoder before the Canvas closes."""
        for item in {*self._items.values(), *self._retired_items.values()}:
            item.release_video_decoder()

    def update_source(self, source: Source) -> None:
        """Refresh canvas representation after an Inspector update."""
        item = self._items.get(source.id)
        if item:
            item.apply_source()

    def _show_interaction_hint(self, text: str) -> None:
        """Float a size / angle read-out near the pointer while dragging a handle."""
        if not text:
            self._interaction_hint.hide()
            return
        self._interaction_hint.setText(text)
        self._interaction_hint.adjustSize()
        pointer = self.viewport().mapFromGlobal(QCursor.pos())
        x = pointer.x() + 16
        y = pointer.y() + 16
        hint = self._interaction_hint
        x = max(4, min(x, self.viewport().width() - hint.width() - 4))
        y = max(4, min(y, self.viewport().height() - hint.height() - 4))
        hint.move(x, y)
        hint.show()
        hint.raise_()

    def _on_item_changed(self, source_id: str, changes: dict) -> None:
        self.store.update(source_id, **changes)

    def _duplicate_source_at(self, source_id: str, x: float, y: float) -> None:
        """Create a copy at a Ctrl-drag drop point while the original stays put."""
        source = self.store.get(source_id)
        if source is None:
            return
        payload = source.to_dict()
        payload.pop("id", None)
        copied = Source.from_dict(payload)
        copied.name = f"{source.name} copy"
        copied.x = x
        copied.y = y
        copied.z_index = max((entry.z_index for entry in self.store.sources()), default=0) + 1
        self.store.add(copied)

    def _on_selection_changed(self) -> None:
        if self._setting_selection:
            return
        items = [item for item in self.scene_model.selectedItems() if isinstance(item, SourceItem)]
        selected_ids = [item.source.id for item in items]
        previous_ids = set(self.store.selected_ids)
        added_ids = [source_id for source_id in selected_ids if source_id not in previous_ids]
        active_id = (
            added_ids[-1] if added_ids else
            self.store.selected.id
            if self.store.selected and self.store.selected.id in selected_ids else
            selected_ids[-1] if selected_ids else None
        )
        self._scene_selection_pending = True
        try:
            self.store.select_many(selected_ids, active_id)
        finally:
            self._scene_selection_pending = False
        self.selection_changed.emit(items)

    def _select_sources_from_store(
        self, source_ids: object, active: Source | None,
    ) -> None:
        """Reflect the shared Layer/Canvas selection without signal recursion."""
        del active
        if self._scene_selection_pending:
            return
        selected = set(source_ids if isinstance(source_ids, (tuple, list)) else ())
        self._setting_selection = True
        try:
            self.scene_model.blockSignals(True)
            try:
                for source_id, item in self._items.items():
                    item.setSelected(source_id in selected)
            finally:
                self.scene_model.blockSignals(False)
        finally:
            self._setting_selection = False
        items = [
            self._items[source_id] for source_id in source_ids
            if source_id in self._items
        ] if isinstance(source_ids, (tuple, list)) else []
        self.selection_changed.emit(items)

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Zoom with Ctrl+wheel; standard scrolling otherwise."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            current = self.transform().m11()
            if 0.2 <= current * factor <= 3.0:
                self.scale(factor, factor)
                self.zoom_changed.emit(self.transform().m11())
            event.accept()
            return
        super().wheelEvent(event)

    @staticmethod
    def _local_drop_paths(event: QDragEnterEvent | QDropEvent) -> list[str]:
        """Return non-empty local paths from a URL drag payload."""
        if not event.mimeData().hasUrls():
            return []
        return [
            path
            for url in event.mimeData().urls()
            if url.isLocalFile() and (path := url.toLocalFile())
        ]

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """Accept palette templates and local-file copies over the Canvas."""
        if event.mimeData().hasFormat(SOURCE_TEMPLATE_MIME):
            if read_source_template_mime(event.mimeData()) is not None:
                event.setDropAction(Qt.DropAction.CopyAction)
                event.accept()
                return
            event.ignore()
            return

        # Project Content deliberately starts a CopyAction drag.  Do not rely
        # on acceptProposedAction() here: a source/model action mismatch can
        # otherwise leave the Canvas showing the forbidden-drop cursor.
        paths = self._local_drop_paths(event)
        if paths and not any(
            Path(path).suffix.lower() in LYRICS_EXTENSIONS for path in paths
        ):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            return
        event.ignore()

    def dragMoveEvent(self, event: QDragEnterEvent) -> None:
        """Maintain an active copy cursor over the editable Canvas."""
        if event.mimeData().hasFormat(SOURCE_TEMPLATE_MIME):
            if read_source_template_mime(event.mimeData()) is not None:
                event.setDropAction(Qt.DropAction.CopyAction)
                event.accept()
                return
            event.ignore()
            return
        paths = self._local_drop_paths(event)
        if paths and not any(
            Path(path).suffix.lower() in LYRICS_EXTENSIONS for path in paths
        ):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        """Forward dropped templates or local paths with their Canvas position."""
        template = read_source_template_mime(event.mimeData())
        if template is not None:
            source_type, parent_type = template
            self.source_template_dropped.emit(
                source_type, parent_type,
                self.mapToScene(event.position().toPoint()),
            )
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            return

        paths = self._local_drop_paths(event)
        if not paths or any(
            Path(path).suffix.lower() in LYRICS_EXTENSIONS for path in paths
        ):
            event.ignore()
            return
        self.files_dropped.emit(paths, self.mapToScene(event.position().toPoint()))
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """Show commands without changing selection from a right-click."""
        item = self.itemAt(event.pos())
        if (
            not isinstance(item, SourceItem)
            or item.source.id not in self.store.selected_ids
        ):
            self._create_context_menu(None).exec(event.globalPos())
            event.accept()
            return
        self._create_context_menu(item).exec(event.globalPos())
        event.accept()

    def _create_context_menu(self, item: SourceItem | None) -> QMenu:
        """Build a localized Canvas context menu with stable command identifiers."""
        korean = self.translator.language.value == "ko"
        menu = QMenu(self)
        if item is None:
            self._add_context_action(
                menu, "붙여넣기" if korean else "Paste", "paste", "Ctrl+V",
            )
            self._add_context_action(
                menu, "모두 선택" if korean else "Select all", "select_all", "Ctrl+A",
            )
            self._add_context_action(
                menu, "모든 잠금 해제" if korean else "Unlock all layers",
                "unlock_all_layers",
                enabled=any(source.locked for source in self.store.sources()),
            )
            menu.addSeparator()
            self._add_context_action(
                menu, "캔버스에 맞추기" if korean else "Fit Canvas", "fit_canvas", "Ctrl+0",
            )
            return menu

        selected = [
            source for source in self.store.sources()
            if source.id in set(self.store.selected_ids)
        ]
        selected_count = len(selected)
        editable_count = sum(not source.locked for source in selected)

        self._add_context_action(menu, "잘라내기" if korean else "Cut", "cut", "Ctrl+X",
                                 enabled=editable_count > 0)
        self._add_context_action(menu, "복사" if korean else "Copy", "copy", "Ctrl+C",
                                 enabled=editable_count > 0)
        self._add_context_action(menu, "붙여넣기" if korean else "Paste", "paste", "Ctrl+V")
        menu.addSeparator()

        self._add_context_action(
            menu, "복제" if korean else "Duplicate", "duplicate", "Ctrl+D",
            enabled=editable_count > 0,
        )
        self._add_context_action(
            menu, "삭제" if korean else "Delete", "delete", "Delete",
            enabled=editable_count > 0,
        )
        menu.addSeparator()

        layer_menu = QMenu("레이어 순서" if korean else "Layer order", menu)
        menu.addMenu(layer_menu)
        self._add_context_action(
            layer_menu, "한 단계 앞으로" if korean else "Move forward", "move_forward",
            enabled=editable_count > 0,
        )
        self._add_context_action(
            layer_menu, "한 단계 뒤로" if korean else "Move backward", "move_backward",
            enabled=editable_count > 0,
        )
        layer_menu.addSeparator()
        self._add_context_action(
            layer_menu, "맨 앞으로" if korean else "Bring to front", "bring_front", "Ctrl+]",
            enabled=editable_count > 0,
        )
        self._add_context_action(
            layer_menu, "맨 뒤로" if korean else "Send to back", "send_back", "Ctrl+[",
            enabled=editable_count > 0,
        )

        align_menu = QMenu("정렬" if korean else "Align", menu)
        menu.addMenu(align_menu)
        self._add_context_action(
            align_menu, "캔버스 가로 중앙" if korean else "Center on Canvas horizontally",
            "center_horizontal", "Ctrl+Shift+H", enabled=editable_count > 0,
        )
        self._add_context_action(
            align_menu, "캔버스 세로 중앙" if korean else "Center on Canvas vertically",
            "center_vertical", "Ctrl+Shift+V", enabled=editable_count > 0,
        )
        align_menu.addSeparator()
        alignment_labels = (
            (("왼쪽 맞춤", "align_left"), ("가로 중앙 맞춤", "align_hcenter"),
             ("오른쪽 맞춤", "align_right"), ("위쪽 맞춤", "align_top"),
             ("세로 중앙 맞춤", "align_vcenter"), ("아래쪽 맞춤", "align_bottom"))
            if korean else
            (("Align left edges", "align_left"), ("Align horizontal centers", "align_hcenter"),
             ("Align right edges", "align_right"), ("Align top edges", "align_top"),
             ("Align vertical centers", "align_vcenter"), ("Align bottom edges", "align_bottom"))
        )
        for label, command in alignment_labels:
            self._add_context_action(
                align_menu, label, command, enabled=editable_count >= 2,
            )
        align_menu.addSeparator()
        distribute_labels = (
            (("가로 간격 균등", "distribute_horizontal"),
             ("세로 간격 균등", "distribute_vertical"))
            if korean else
            (("Distribute horizontal spacing", "distribute_horizontal"),
             ("Distribute vertical spacing", "distribute_vertical"))
        )
        for label, command in distribute_labels:
            self._add_context_action(
                align_menu, label, command, enabled=editable_count >= 3,
            )

        organize_menu = QMenu("그룹" if korean else "Group", menu)
        menu.addMenu(organize_menu)
        self._add_context_action(
            organize_menu, "그룹 만들기" if korean else "Group selected", "group", "Ctrl+G",
            enabled=editable_count >= 2,
        )
        self._add_context_action(
            organize_menu, "그룹 해제" if korean else "Ungroup", "ungroup", "Ctrl+Shift+G",
            enabled=any(source.group_id for source in selected),
        )

        menu.addSeparator()
        visible = self._add_context_action(
            menu, "표시" if korean else "Visible", "toggle_visible",
            checkable=True,
        )
        visible.setCheckable(True)
        visible.setChecked(bool(selected) and all(source.visible for source in selected))
        locked = self._add_context_action(
            menu, "잠금" if korean else "Locked", "toggle_lock", "Ctrl+L",
            checkable=True,
        )
        locked.setChecked(bool(selected) and all(source.locked for source in selected))
        menu.addSeparator()
        self._add_context_action(
            menu, "모두 선택" if korean else "Select all", "select_all", "Ctrl+A",
        )
        # PySide can release submenu wrappers after this factory returns even
        # though Qt owns them. Retain wrappers with the root menu for its lifetime.
        menu._owned_submenus = [layer_menu, align_menu, organize_menu]  # type: ignore[attr-defined]
        return menu

    def _add_context_action(
        self, menu: QMenu, label: str, command: str, shortcut: str = "",
        *, enabled: bool = True, checkable: bool = False,
    ) -> QAction:
        action = menu.addAction(label)
        action.setData(command)
        action.setEnabled(enabled)
        action.setCheckable(checkable)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(
            lambda _checked=False, name=command: self._dispatch_context_command(name)
        )
        return action

    def _dispatch_context_command(self, command: str) -> None:
        if command == "cut":
            self.cut_requested.emit()
        elif command == "copy":
            self.copy_requested.emit()
        elif command == "paste":
            self.paste_requested.emit()
        else:
            self.command_requested.emit(command)

    def mousePressEvent(self, event: object) -> None:
        """Handle navigation and reserve source selection for left-clicks."""
        button = event.button()  # type: ignore[union-attr]
        modifiers = event.modifiers()  # type: ignore[union-attr]
        # QGraphicsView forwards right-button presses to scene items by default,
        # which selects the item before contextMenuEvent runs.  Consume the
        # press here so right-click only opens a menu and never changes either
        # the primary selection or a multi-selection.
        if button == Qt.MouseButton.RightButton:
            event.accept()  # type: ignore[union-attr]
            return
        if button == Qt.MouseButton.MiddleButton or (
            button == Qt.MouseButton.LeftButton and self._space_panning
        ):
            self._panning = True
            self._pan_origin = event.pos()  # type: ignore[union-attr]
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()  # type: ignore[union-attr]
            return
        # A selected resize handle is an editing control, even when another
        # higher-Z source overlaps the same scene position.  Temporarily remove
        # other sources from mouse hit testing for this synchronous press; once
        # the selected item becomes the scene mouse grabber, move/release events
        # continue to reach it normally.
        handle_target: tuple[SourceItem, str] | None = None
        if button == Qt.MouseButton.LeftButton:
            selected = sorted(
                (
                    item for item in self.scene_model.selectedItems()
                    if isinstance(item, SourceItem) and item.isVisible()
                ),
                key=lambda item: item.zValue(),
                reverse=True,
            )
            handle_target = next(
                (
                    (item, handle)
                    for item in selected
                    if (handle := self._edit_handle_at_view_position(
                        item, event.position().toPoint()  # type: ignore[union-attr]
                    )) is not None
                ),
                None,
            )
        if handle_target is None:
            super().mousePressEvent(event)  # type: ignore[arg-type]
            return

        target_item, target_handle = handle_target
        target_item.prioritize_edit_handle_for_next_press(target_handle)
        blocked_items: list[tuple[SourceItem, Qt.MouseButtons]] = []
        for item in self._items.values():
            if item is target_item:
                continue
            buttons = item.acceptedMouseButtons()
            if buttons != Qt.MouseButton.NoButton:
                blocked_items.append((item, buttons))
                item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        try:
            super().mousePressEvent(event)  # type: ignore[arg-type]
        finally:
            # Clear a value that was not consumed (for example if Qt discarded
            # the press while the scene was changing underneath the pointer).
            target_item.prioritize_edit_handle_for_next_press(None)
            for item, buttons in blocked_items:
                item.setAcceptedMouseButtons(buttons)

    def mouseMoveEvent(self, event: object) -> None:
        """Move the viewport during panning."""
        if self._panning:
            delta = event.pos() - self._pan_origin  # type: ignore[union-attr]
            self._pan_origin = event.pos()  # type: ignore[union-attr]
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()  # type: ignore[union-attr]
            return
        super().mouseMoveEvent(event)  # type: ignore[arg-type]
        if (self._space_panning
                or event.buttons() != Qt.MouseButton.NoButton):  # type: ignore[union-attr]
            return
        # Mirror the press-time handle priority so an overlapping, higher-Z item
        # cannot hide the cursor of a selected source's visible edit handle.
        selected = sorted(
            (
                item for item in self.scene_model.selectedItems()
                if isinstance(item, SourceItem) and item.isVisible()
            ),
            key=lambda item: item.zValue(),
            reverse=True,
        )
        hovered = next(
            (
                (item, handle)
                for item in selected
                if (handle := self._edit_handle_at_view_position(
                    item, event.position().toPoint()  # type: ignore[union-attr]
                )) is not None
            ),
            None,
        )
        if hovered is None:
            self.viewport().unsetCursor()
            return
        item, handle = hovered
        self.viewport().setCursor(
            SourceItem.cursor_for_edit_handle(handle, item.rotation())
        )

    def _edit_handle_at_view_position(self, item: SourceItem, position: QPoint) -> str | None:
        """Hit-test edit handles with a stable two-pixel screen tolerance.

        A handle's item-space center can land between device pixels. At a low
        canvas zoom, converting that rounded viewport point back to item space
        may otherwise miss even though the pointer visibly covers the handle.
        """
        scene_position = self.mapToScene(position)
        local_position = item.mapFromScene(scene_position)
        local_x = item.mapFromScene(self.mapToScene(position + QPoint(2, 0)))
        local_y = item.mapFromScene(self.mapToScene(position + QPoint(0, 2)))
        tolerance = max(
            QLineF(local_position, local_x).length(),
            QLineF(local_position, local_y).length(),
        )
        return item.edit_handle_at(local_position, tolerance)

    def mouseReleaseEvent(self, event: object) -> None:
        """End panning."""
        if self._panning:
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()  # type: ignore[union-attr]
            return
        super().mouseReleaseEvent(event)  # type: ignore[arg-type]
        self.viewport().unsetCursor()

    def keyPressEvent(self, event: object) -> None:
        """Space+drag hand-tool panning and arrow-key nudging of the selection."""
        key = event.key()  # type: ignore[union-attr]
        if key == Qt.Key.Key_Space:
            self._space_panning = True
            if not self._panning:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()  # type: ignore[union-attr]
            return
        modifiers = event.modifiers()  # type: ignore[union-attr]
        blocked = (
            Qt.KeyboardModifier.ControlModifier
            | Qt.KeyboardModifier.AltModifier
        )
        if key in self._ARROW_DELTAS and not (modifiers & blocked):
            step = 10.0 if modifiers & Qt.KeyboardModifier.ShiftModifier else 1.0
            delta_x, delta_y = self._ARROW_DELTAS[key]
            self.nudge_requested.emit(delta_x * step, delta_y * step)
            event.accept()  # type: ignore[union-attr]
            return
        if key == Qt.Key.Key_F2:
            self.rename_requested.emit()
            event.accept()  # type: ignore[union-attr]
            return
        super().keyPressEvent(event)  # type: ignore[arg-type]

    def keyReleaseEvent(self, event: object) -> None:
        if event.key() == Qt.Key.Key_Space:  # type: ignore[union-attr]
            self._space_panning = False
            if not self._panning:
                self.unsetCursor()
            event.accept()  # type: ignore[union-attr]
            return
        super().keyReleaseEvent(event)  # type: ignore[arg-type]
        self.scene_model.clear_alignment_guides()

    def fit_artboard(self) -> None:
        """Fit the complete artboard into the view."""
        self.fitInView(self.scene_model.artboard_rect.adjusted(-30, -30, 30, 30),
                       Qt.AspectRatioMode.KeepAspectRatio)
        self.zoom_changed.emit(self.transform().m11())

    def set_zoom(self, zoom: float) -> None:
        """Restore a stored absolute zoom factor."""
        zoom = min(3.0, max(0.2, zoom))
        self.resetTransform()
        self.scale(zoom, zoom)
        self.zoom_changed.emit(self.transform().m11())

    def zoom_by(self, factor: float) -> None:
        """Zoom a bounded step around the view centre (keyboard / status-bar)."""
        current = self.transform().m11()
        target = min(3.0, max(0.2, current * factor))
        relative = target / current
        if abs(relative - 1.0) < 1e-6:
            return
        previous_anchor = self.transformationAnchor()
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.scale(relative, relative)
        self.setTransformationAnchor(previous_anchor)
        self.zoom_changed.emit(self.transform().m11())

    def set_theme_colors(self, workspace: QColor, artboard: QColor,
                         grid: QColor, border: QColor) -> None:
        """Apply colors to the view and its underlying graphics scene."""
        self.setBackgroundBrush(workspace)
        self.scene_model.set_theme_colors(workspace, artboard, grid, border)
