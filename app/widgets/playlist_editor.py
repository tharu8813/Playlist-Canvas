"""Interactive, drag-sortable playlist editor widget."""

from __future__ import annotations

from html import escape
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import (
    QContextMenuEvent, QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent,
    QDropEvent, QKeyEvent, QMouseEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.models.playlist import PlaylistTrack
from app.preview.album_art import extract_track_cover
from app.services.project_content_service import LYRICS_EXTENSIONS
from app.services.playlist_service import AUDIO_EXTENSIONS, PlaylistService
from app.utils.i18n import Translator


class PlaylistList(QListWidget):
    """List view accepting audio-file drops and internal drag reordering."""

    files_dropped = Signal(list)
    lyrics_dropped = Signal(str, str)
    track_double_clicked = Signal(str)
    order_changed = Signal()
    reorder_requested = Signal(list)
    remove_requested = Signal()
    toggle_requested = Signal()
    details_requested = Signal(str)
    context_menu_requested = Signal(QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("playlistList")
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # DragDrop (not InternalMove) so external audio/lyrics drops from
        # Project Content or the file system reach dropEvent(); the handlers
        # below branch on ``event.source() is self`` for internal reordering.
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setAutoScroll(True)
        self.setAutoScrollMargin(42)
        self.setDropIndicatorShown(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setUniformItemSizes(True)
        self.setSpacing(4)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._drop_target_widget: QWidget | None = None
        self.model().rowsMoved.connect(self.order_changed)

    @staticmethod
    def _local_paths(event: QDragEnterEvent | QDragMoveEvent | QDropEvent) -> list[str]:
        if not event.mimeData().hasUrls():
            return []
        return [
            path
            for url in event.mimeData().urls()
            if url.isLocalFile() and (path := url.toLocalFile())
        ]

    @staticmethod
    def _refresh_style(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    def _set_drop_active(self, active: bool) -> None:
        if bool(self.property("dropActive")) == active:
            return
        self.setProperty("dropActive", active)
        self._refresh_style(self)
        self.viewport().update()

    def _set_drop_target(self, item: QListWidgetItem | None) -> None:
        widget = self.itemWidget(item) if item is not None else None
        if widget is self._drop_target_widget:
            return
        if self._drop_target_widget is not None:
            self._drop_target_widget.setProperty("dropTarget", False)
            self._refresh_style(self._drop_target_widget)
        self._drop_target_widget = widget
        if widget is not None:
            widget.setProperty("dropTarget", True)
            self._refresh_style(widget)

    def _clear_drop_feedback(self) -> None:
        self._set_drop_target(None)
        self._set_drop_active(False)

    def _update_external_drag(
        self, event: QDragEnterEvent | QDragMoveEvent,
    ) -> bool:
        paths = self._local_paths(event)
        lyrics_paths = [
            path for path in paths
            if Path(path).suffix.lower() in LYRICS_EXTENSIONS
        ]
        media_paths = [
            path for path in paths
            if Path(path).suffix.lower() in AUDIO_EXTENSIONS
        ]
        if not lyrics_paths and not media_paths:
            self._clear_drop_feedback()
            event.ignore()
            return False

        target_item = self.itemAt(event.position().toPoint())
        # Lyrics belong to one exact track.  Even inside the Playlist, keep the
        # forbidden cursor until the pointer is over a real track row.
        if lyrics_paths and target_item is None:
            self._clear_drop_feedback()
            event.ignore()
            return False

        self._set_drop_active(True)
        self._set_drop_target(target_item if lyrics_paths else None)
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        return True

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """Accept audio anywhere in the list and lyrics only on a track row."""
        if event.source() is self:
            self._clear_drop_feedback()
            super().dragEnterEvent(event)
            return
        # Accept the *enter* for any audio/lyrics payload, even when the pointer
        # is not over a row yet.  Rejecting here makes Qt stop delivering
        # dragMoveEvent, so a lyric drag that crosses the list border over the
        # spacing gap would stay forbidden even after reaching a track row.
        # dragMoveEvent() below does the real per-row targeting.
        paths = self._local_paths(event)
        if any(
            Path(path).suffix.lower() in LYRICS_EXTENSIONS
            or Path(path).suffix.lower() in AUDIO_EXTENSIONS
            for path in paths
        ):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            self._clear_drop_feedback()
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        """Highlight the exact lyric target while the drag cursor moves."""
        if event.source() is self:
            self._clear_drop_feedback()
            super().dragMoveEvent(event)
            return
        self._update_external_drag(event)

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        """Remove temporary drag-over highlighting when the pointer leaves."""
        self._clear_drop_feedback()
        super().dragLeaveEvent(event)

    def _reorder_from_drop(self, event: QDropEvent) -> None:
        """Move the selected rows to the drop point ourselves.

        In ``DragDrop`` mode ``QListWidget`` only performs an internal move when
        the negotiated drop action is exactly ``MoveAction``; a drag started
        from the list often arrives as ``CopyAction`` and the rows get
        duplicated instead of reordered.  Computing the target order here and
        handing it to the service keeps reordering deterministic.
        """
        order = [
            str(self.item(row).data(Qt.ItemDataRole.UserRole))
            for row in range(self.count())
        ]
        # Keep the dragged block in its visual order, not selection order.
        selected_rows = sorted(self.row(item) for item in self.selectedItems())
        moving = [order[row] for row in selected_rows if 0 <= row < len(order)]
        if not moving:
            event.ignore()
            return
        target = self.itemAt(event.position().toPoint())
        if target is None:
            insert_at = len(order)
        else:
            insert_at = self.row(target)
            rect = self.visualItemRect(target)
            if event.position().toPoint().y() > rect.center().y():
                insert_at += 1
        moving_set = set(moving)
        insert_at -= sum(1 for row, ident in enumerate(order)
                         if ident in moving_set and row < insert_at)
        remaining = [ident for ident in order if ident not in moving_set]
        new_order = remaining[:insert_at] + moving + remaining[insert_at:]
        if new_order == order:
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()
        self.reorder_requested.emit(new_order)

    def dropEvent(self, event: QDropEvent) -> None:
        """Import audio or attach lyrics only to the highlighted track row."""
        if event.source() is self:
            self._clear_drop_feedback()
            self._reorder_from_drop(event)
            return

        paths = self._local_paths(event)
        lyrics_paths = [
            path for path in paths
            if Path(path).suffix.lower() in LYRICS_EXTENSIONS
        ]
        media_paths = [
            path for path in paths
            if Path(path).suffix.lower() in AUDIO_EXTENSIONS
        ]
        target_item = self.itemAt(event.position().toPoint())

        # Do not emit a lyrics signal with an empty track id.  Ignoring here
        # keeps the drop forbidden and avoids the old "drop on a track" popup.
        if lyrics_paths and target_item is None:
            self._clear_drop_feedback()
            event.ignore()
            return

        target_id = (
            str(target_item.data(Qt.ItemDataRole.UserRole))
            if target_item is not None else ""
        )
        self._clear_drop_feedback()

        for path in lyrics_paths:
            self.lyrics_dropped.emit(path, target_id)
        if media_paths:
            self.files_dropped.emit(media_paths)
        if lyrics_paths or media_paths:
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            return
        event.ignore()

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """Right-click acts on the whole current selection, like a file list."""
        item = self.itemAt(self.viewport().mapFromGlobal(event.globalPos()))
        if item is not None and not item.isSelected():
            self.setCurrentItem(item)
            self.clearSelection()
            item.setSelected(True)
        self.context_menu_requested.emit(event.globalPos())
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Expose common playlist editing actions without fragile global shortcuts."""
        if event.key() == Qt.Key.Key_Delete:
            self.remove_requested.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Space:
            self.toggle_requested.emit()
            event.accept()
            return
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            current = self.currentItem()
            if current is not None:
                self.details_requested.emit(str(current.data(Qt.ItemDataRole.UserRole)))
                event.accept()
                return
        super().keyPressEvent(event)


class TrackRow(QWidget):
    """Compact, display-only visual row for a playlist track.

    Selection, double-click and drag reordering are all handled natively by
    the parent ``PlaylistList`` (an ExtendedSelection QListWidget), so this
    widget stays transparent to mouse input and never fights that logic.
    """

    def __init__(self, number: int, track: PlaylistTrack, korean: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("trackRow")
        self.track_id = track.id
        self.setProperty("trackDisabled", not track.enabled)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(9)
        number_label = QLabel(f"{number:02d}")
        number_label.setObjectName("trackNumber")
        cover_label = QLabel()
        cover_label.setObjectName("trackRowCover")
        cover_label.setFixedSize(34, 34)
        cover_label.setScaledContents(True)
        cover_pixmap = extract_track_cover(track.file_path, track.cover_path)
        if not cover_pixmap.isNull():
            cover_label.setPixmap(cover_pixmap.scaled(
                34, 34, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            ))
        else:
            cover_label.setText("♪")
            cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = escape(track.title)
        subtitle = f"{escape(track.artist)} · {escape(track.album)}"
        if not track.enabled:
            title = f'<span style="text-decoration:line-through">{title}</span>'
        metadata = QLabel(f"<b>{title}</b><br><span>{subtitle}</span>")
        metadata.setObjectName("trackMetadata")
        metadata.setTextFormat(Qt.TextFormat.RichText)
        duration = QLabel(track.duration_label)
        duration.setObjectName("mutedLabel")
        layout.addWidget(number_label)
        layout.addWidget(cover_label)
        layout.addWidget(metadata, 1)
        if not track.enabled:
            excluded = QLabel("제외됨" if korean else "Excluded")
            excluded.setObjectName("mutedLabel")
            layout.addWidget(excluded)
        if track.lyrics or track.lyrics_path:
            offset = track.lyrics_timing_offset_seconds
            lyric_badge = QLabel(
                f"가사 {offset:+.2f}s" if korean else f"Lyrics {offset:+.2f}s"
            )
            lyric_badge.setObjectName("mutedLabel")
            layout.addWidget(lyric_badge)
        layout.addWidget(duration)
        # Excluded rows are dimmed via the #trackRow[trackDisabled] stylesheet,
        # never setEnabled(False): a disabled child label swallows the
        # right-click so the export include/exclude menu could not be reached.
        self.setToolTip(
            f"{track.title}\n{track.artist} · {track.album}\n"
            f"{Path(track.file_path)}"
            + ("" if track.enabled else
               ("\n\n내보내기에서 제외됨 (우클릭 → 포함)" if korean
                else "\n\nExcluded from export (right-click to include)"))
        )

    # Let the parent list own selection, double-click and drag: forward the
    # button events to it instead of running a second, conflicting handler
    # (the old custom handler double-toggled Ctrl+click and broke multi-select).
    def mousePressEvent(self, event: QMouseEvent) -> None:
        event.ignore()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        event.ignore()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        event.ignore()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        event.ignore()


class PlaylistEditor(QFrame):
    """Playlist panel backed by a PlaylistService."""

    request_files = Signal()
    files_dropped = Signal(list)
    lyrics_dropped = Signal(str, str)
    track_double_clicked = Signal(str)

    def __init__(self, service: PlaylistService, translator: Translator,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("playlistStrip")
        self.service = service
        self.translator = translator
        self._ignore_order_signal = False
        self._pending_order_ids: list[str] = []
        self._order_timer = QTimer(self)
        self._order_timer.setSingleShot(True)
        self._order_timer.timeout.connect(self._apply_pending_order)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(140)
        self._search_timer.timeout.connect(self.refresh)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(7)
        header = QHBoxLayout()
        self.title = QLabel()
        self.title.setObjectName("panelTitle")
        self.summary = QLabel()
        self.summary.setObjectName("mutedLabel")
        self.add_button = QPushButton()
        self.order_editor_button = QPushButton()
        self.up_button = QPushButton("↑")
        self.down_button = QPushButton("↓")
        self.duplicate_button = QPushButton()
        self.details_button = QPushButton()
        self.remove_button = QPushButton()
        header.addWidget(self.title)
        header.addWidget(self.summary)
        header.addStretch()
        header.addWidget(self.add_button)
        header.addWidget(self.order_editor_button)
        header.addWidget(self.up_button)
        header.addWidget(self.down_button)
        header.addWidget(self.duplicate_button)
        header.addWidget(self.details_button)
        header.addWidget(self.remove_button)
        layout.addLayout(header)
        self.search_edit = QLineEdit()
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(lambda _text: self._search_timer.start())
        layout.addWidget(self.search_edit)
        self.list_widget = PlaylistList()
        self.list_widget.setMinimumHeight(150)
        layout.addWidget(self.list_widget)
        self.empty_label = QLabel()
        self.empty_label.setObjectName("mutedLabel")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty_label)
        self.add_button.clicked.connect(self.request_files)
        self.order_editor_button.clicked.connect(self._open_order_editor)
        self.up_button.clicked.connect(lambda: self._move_selected(-1))
        self.down_button.clicked.connect(lambda: self._move_selected(1))
        self.duplicate_button.clicked.connect(self.duplicate_selected)
        self.details_button.clicked.connect(self._show_selected_details)
        self.remove_button.clicked.connect(self.remove_selected)
        self.list_widget.files_dropped.connect(self.files_dropped)
        self.list_widget.lyrics_dropped.connect(self.lyrics_dropped)
        self.list_widget.order_changed.connect(self._sync_order)
        self.list_widget.reorder_requested.connect(self._apply_drop_order)
        self.list_widget.remove_requested.connect(self.remove_selected)
        self.list_widget.toggle_requested.connect(self._toggle_selected)
        self.list_widget.details_requested.connect(self.track_double_clicked)
        self.list_widget.context_menu_requested.connect(self._show_context_menu)
        self.list_widget.itemSelectionChanged.connect(self._update_action_state)
        self.list_widget.itemDoubleClicked.connect(lambda item: self.track_double_clicked.emit(item.data(Qt.ItemDataRole.UserRole)))
        service.playlist_changed.connect(self.refresh)
        translator.language_changed.connect(self.retranslate)
        self.retranslate()
        self.refresh()

    def retranslate(self) -> None:
        """Refresh static playlist chrome in the selected language."""
        korean = self.translator.language.value == "ko"
        self.title.setText(self.translator.text("playlist"))
        self.search_edit.setPlaceholderText(
            "제목, 아티스트 또는 앨범 검색…" if korean
            else "Search title, artist, or album…"
        )
        self.add_button.setText("+ 음악 추가" if korean else "+ Add music")
        self.order_editor_button.setText("순서 편집" if korean else "Reorder")
        self.order_editor_button.setToolTip(
            "커버·정보와 미리듣기가 있는 창에서 트랙 순서를 편집합니다."
            if korean else
            "Edit the track order in a window with covers, details, and preview."
        )
        self.duplicate_button.setText("복제" if korean else "Duplicate")
        self.details_button.setText(
            "곡 정보/설정" if korean else "Track information/settings"
        )
        self.remove_button.setText("삭제" if korean else "Remove")
        self.up_button.setToolTip("위로 이동" if korean else "Move up")
        self.down_button.setToolTip("아래로 이동" if korean else "Move down")
        self.empty_label.setText(
            "음악 파일을 추가하거나 이 영역으로 끌어오세요."
            if korean else "Add music files or drop them in this area."
        )
        self.refresh()

    def refresh(self) -> None:
        """Rebuild rows from service order and update inclusion summary."""
        selected_ids = set(self._selected_ids())
        current_id = (
            str(self.list_widget.currentItem().data(Qt.ItemDataRole.UserRole))
            if self.list_widget.currentItem() is not None else ""
        )
        scroll_position = self.list_widget.verticalScrollBar().value()
        self._ignore_order_signal = True
        try:
            # A service refresh can arrive while an external drag is still over
            # the list. Clear references to soon-to-be-deleted TrackRow widgets
            # before QListWidget.clear() rebuilds the presentation.
            self.list_widget._clear_drop_feedback()
            self.list_widget.clear()
            all_tracks = self.service.tracks
            query = self.search_edit.text().strip().casefold()
            tracks = [
                track for track in all_tracks
                if not query or query in " ".join(
                    (track.title, track.artist, track.album, Path(track.file_path).name)
                ).casefold()
            ]
            track_numbers = {
                track.id: number for number, track in enumerate(all_tracks, start=1)
            }
            for track in tracks:
                number = track_numbers[track.id]
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, track.id)
                row = TrackRow(number, track, self.translator.language.value == "ko")
                item.setSizeHint(row.sizeHint())
                self.list_widget.addItem(item)
                self.list_widget.setItemWidget(item, row)
                if track.id in selected_ids:
                    item.setSelected(True)
                if track.id == current_id:
                    self.list_widget.setCurrentItem(item)
            self.list_widget.setVisible(bool(tracks))
            self.empty_label.setVisible(not tracks)
            enabled = sum(track.enabled for track in all_tracks)
            korean = self.translator.language.value == "ko"
            filtered = f" · {len(tracks)}곡 표시" if korean and query else (
                f" · {len(tracks)} shown" if query else ""
            )
            self.summary.setText(
                (f"{len(all_tracks)}곡 중 {enabled}곡 사용{filtered}" if korean else
                 f"{enabled} enabled of {len(all_tracks)} tracks{filtered}")
            )
            self.empty_label.setText(
                ("검색 결과가 없습니다." if korean else "No matching tracks.")
                if query else
                ("음악 파일을 추가하거나 이 영역으로 끌어오세요." if korean
                 else "Add music files or drop them in this area.")
            )
            # A search filter breaks safe reordering, but external audio/lyrics
            # drops onto a specific track must still work.
            self.list_widget.setDragEnabled(not bool(query))
            self.list_widget.setDragDropMode(
                QAbstractItemView.DragDropMode.DropOnly if query else
                QAbstractItemView.DragDropMode.DragDrop
            )
            QTimer.singleShot(
                0, lambda value=scroll_position: self.list_widget.verticalScrollBar().setValue(value)
            )
        finally:
            self._ignore_order_signal = False
        self._update_action_state()

    def _selected_ids(self) -> list[str]:
        return [item.data(Qt.ItemDataRole.UserRole) for item in self.list_widget.selectedItems()]

    def duplicate_selected(self) -> None:
        self.service.duplicate(self._selected_ids())

    def remove_selected(self) -> None:
        self.service.remove(self._selected_ids())

    def _sync_order(self) -> None:
        if self._ignore_order_signal:
            return
        ordered_ids = [
            self.list_widget.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self.list_widget.count())
        ]
        self._pending_order_ids = [str(identifier) for identifier in ordered_ids]
        self._order_timer.start(0)

    def _apply_pending_order(self) -> None:
        if self._pending_order_ids:
            values = self._pending_order_ids
            self._pending_order_ids = []
            self.service.reorder(values)

    def _apply_drop_order(self, ordered_ids: list) -> None:
        """Commit a drag-and-drop reorder computed by the list widget."""
        values = [str(identifier) for identifier in ordered_ids if identifier]
        if values:
            self.service.reorder(values)

    def _open_order_editor(self) -> None:
        """Open the dedicated reorder window with covers and an audio preview."""
        tracks = list(self.service.tracks)
        if len(tracks) < 2:
            return
        from app.dialogs.track_order_dialog import TrackOrderDialog

        dialog = TrackOrderDialog(tracks, self.translator, self)
        accepted = dialog.exec() == dialog.DialogCode.Accepted
        new_order = list(dialog.new_order)
        dialog.deleteLater()
        if accepted:
            self.service.reorder(new_order)

    def _show_context_menu(self, global_pos: QPoint) -> None:
        """Right-click actions for the current selection."""
        selected = self._selected_ids()
        if not selected:
            return
        korean = self.translator.language.value == "ko"
        tracks = [track for track in self.service.tracks if track.id in selected]
        menu = QMenu(self)
        if len(selected) == 1:
            details = menu.addAction("곡 정보/설정" if korean else "Track information/settings")
            details.triggered.connect(lambda: self.track_double_clicked.emit(selected[0]))
            up = menu.addAction("위로 이동" if korean else "Move up")
            up.triggered.connect(lambda: self._move_selected(-1))
            down = menu.addAction("아래로 이동" if korean else "Move down")
            down.triggered.connect(lambda: self._move_selected(1))
        duplicate = menu.addAction(
            f"복제 ({len(selected)})" if korean and len(selected) > 1
            else ("복제" if korean else "Duplicate")
        )
        duplicate.triggered.connect(self.duplicate_selected)
        menu.addSeparator()
        any_disabled = any(not track.enabled for track in tracks)
        include_label = (
            ("내보내기에 포함" if korean else "Include in export")
            if any_disabled else
            ("내보내기에서 제외" if korean else "Exclude from export")
        )
        toggle = menu.addAction(include_label)
        toggle.triggered.connect(self._toggle_selected)
        menu.addSeparator()
        remove = menu.addAction(
            f"삭제 ({len(selected)})" if korean and len(selected) > 1
            else ("삭제" if korean else "Remove")
        )
        remove.triggered.connect(self.remove_selected)
        menu.exec(global_pos)

    def _move_selected(self, direction: int) -> None:
        selected = self._selected_ids()
        if len(selected) == 1:
            self.service.move_track(selected[0], direction)

    def _toggle_selected(self) -> None:
        selected = set(self._selected_ids())
        tracks = [track for track in self.service.tracks if track.id in selected]
        if not tracks:
            return
        enabled = any(not track.enabled for track in tracks)
        self.service.set_enabled_many((track.id for track in tracks), enabled)

    def _show_selected_details(self) -> None:
        selected = self._selected_ids()
        if len(selected) == 1:
            self.track_double_clicked.emit(selected[0])

    def _update_action_state(self) -> None:
        selected = self._selected_ids()
        self.order_editor_button.setEnabled(len(self.service.tracks) >= 2)
        self.duplicate_button.setEnabled(bool(selected))
        self.details_button.setEnabled(len(selected) == 1)
        self.remove_button.setEnabled(bool(selected))
        self.up_button.setEnabled(len(selected) == 1)
        self.down_button.setEnabled(len(selected) == 1)
