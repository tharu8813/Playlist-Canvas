"""Interactive, drag-sortable playlist editor widget."""

from __future__ import annotations

from html import escape
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.models.playlist import PlaylistTrack
from app.services.playlist_service import AUDIO_EXTENSIONS, PlaylistService
from app.utils.i18n import Translator


class PlaylistList(QListWidget):
    """List view accepting audio-file drops and internal drag reordering."""

    files_dropped = Signal(list)
    track_double_clicked = Signal(str)
    order_changed = Signal()
    remove_requested = Signal()
    toggle_requested = Signal()
    details_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setSpacing(4)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.model().rowsMoved.connect(self.order_changed)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """Accept URLs from Explorer and preserve normal internal dragging."""
        if event.mimeData().hasUrls() or event.source() is self:
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event: QDragEnterEvent) -> None:
        """Keep a valid drag indicator for supported source data."""
        self.dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        """Import dropped audio files or delegate an internal move to Qt."""
        if event.mimeData().hasUrls() and event.source() is not self:
            paths = [url.toLocalFile() for url in event.mimeData().urls()]
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

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
    """Compact visual row for a playlist track."""

    toggled = Signal(str, bool)
    clicked = Signal(str, object)
    activated = Signal(str)

    def __init__(self, number: int, track: PlaylistTrack, korean: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("trackRow")
        self.track_id = track.id
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(9)
        self.enabled_box = QCheckBox()
        self.enabled_box.setChecked(track.enabled)
        self.enabled_box.toggled.connect(lambda checked: self.toggled.emit(track.id, checked))
        number_label = QLabel(f"{number:02d}")
        number_label.setObjectName("trackNumber")
        metadata = QLabel(
            f"<b>{escape(track.title)}</b><br>"
            f"<span>{escape(track.artist)} · {escape(track.album)}</span>"
        )
        metadata.setObjectName("trackMetadata")
        metadata.setTextFormat(Qt.TextFormat.RichText)
        duration = QLabel(track.duration_label)
        duration.setObjectName("mutedLabel")
        layout.addWidget(self.enabled_box)
        layout.addWidget(number_label)
        layout.addWidget(metadata, 1)
        if track.lyrics or track.lyrics_path:
            offset = track.lyrics_timing_offset_seconds
            lyric_badge = QLabel(
                f"가사 {offset:+.2f}s" if korean else f"Lyrics {offset:+.2f}s"
            )
            lyric_badge.setObjectName("mutedLabel")
            lyric_badge.setToolTip(
                "더블클릭하거나 곡 정보/설정에서 메타데이터·커버·가사를 편집합니다."
                if korean else
                "Double-click or use Track/Lyrics Settings to adjust timing."
            )
            lyric_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(lyric_badge)
        layout.addWidget(duration)
        for label in (number_label, metadata, duration):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setToolTip(
            f"{track.title}\n{track.artist} · {track.album}\n{Path(track.file_path)}"
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.clicked.emit(self.track_id, event.modifiers())
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self.activated.emit(self.track_id)
        event.accept()


class PlaylistEditor(QFrame):
    """Playlist panel backed by a PlaylistService."""

    request_files = Signal()
    files_dropped = Signal(list)
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
        self.up_button = QPushButton("↑")
        self.down_button = QPushButton("↓")
        self.duplicate_button = QPushButton()
        self.details_button = QPushButton()
        self.remove_button = QPushButton()
        header.addWidget(self.title)
        header.addWidget(self.summary)
        header.addStretch()
        header.addWidget(self.add_button)
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
        self.up_button.clicked.connect(lambda: self._move_selected(-1))
        self.down_button.clicked.connect(lambda: self._move_selected(1))
        self.duplicate_button.clicked.connect(self.duplicate_selected)
        self.details_button.clicked.connect(self._show_selected_details)
        self.remove_button.clicked.connect(self.remove_selected)
        self.list_widget.files_dropped.connect(self.files_dropped)
        self.list_widget.order_changed.connect(self._sync_order)
        self.list_widget.remove_requested.connect(self.remove_selected)
        self.list_widget.toggle_requested.connect(self._toggle_selected)
        self.list_widget.details_requested.connect(self.track_double_clicked)
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
                row.toggled.connect(self._queue_enabled_change)
                row.clicked.connect(self._select_track_row)
                row.activated.connect(self.track_double_clicked)
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
            self.list_widget.setDragEnabled(not bool(query))
            self.list_widget.setDragDropMode(
                QAbstractItemView.DragDropMode.DropOnly if query else
                QAbstractItemView.DragDropMode.InternalMove
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

    def _queue_enabled_change(self, track_id: str, enabled: bool) -> None:
        """Avoid rebuilding and deleting a checkbox during its own signal."""
        QTimer.singleShot(0, lambda: self.service.set_enabled(track_id, enabled))

    def _select_track_row(self, track_id: str, modifiers: object) -> None:
        target = next(
            (self.list_widget.item(row) for row in range(self.list_widget.count())
             if self.list_widget.item(row).data(Qt.ItemDataRole.UserRole) == track_id),
            None,
        )
        if target is None:
            return
        keyboard_modifiers = Qt.KeyboardModifier(modifiers)
        if keyboard_modifiers & Qt.KeyboardModifier.ShiftModifier:
            anchor = max(0, self.list_widget.currentRow())
            target_row = self.list_widget.row(target)
            self.list_widget.clearSelection()
            for row in range(min(anchor, target_row), max(anchor, target_row) + 1):
                self.list_widget.item(row).setSelected(True)
        elif keyboard_modifiers & Qt.KeyboardModifier.ControlModifier:
            target.setSelected(not target.isSelected())
        else:
            self.list_widget.clearSelection()
            target.setSelected(True)
        self.list_widget.setCurrentItem(target)
        self.list_widget.setFocus(Qt.FocusReason.MouseFocusReason)

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
        self.duplicate_button.setEnabled(bool(selected))
        self.details_button.setEnabled(len(selected) == 1)
        self.remove_button.setEnabled(bool(selected))
        self.up_button.setEnabled(len(selected) == 1)
        self.down_button.setEnabled(len(selected) == 1)
