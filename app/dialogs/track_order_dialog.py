"""Dedicated playlist reordering dialog with an audio preview player."""

from __future__ import annotations

from html import escape
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, QSize
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from app.models.playlist import PlaylistTrack
from app.preview.album_art import extract_track_cover
from app.services.preview_audio_settings import preview_volume, save_preview_volume
from app.utils.i18n import Translator

_COVER_PX = 46


def _clock(milliseconds: int) -> str:
    total = max(0, milliseconds) // 1000
    return f"{total // 60:02d}:{total % 60:02d}"


class _TrackRow(QWidget):
    """One reorder row: position, cover thumbnail, and track metadata."""

    def __init__(self, track: PlaylistTrack, korean: bool) -> None:
        super().__init__()
        self.setObjectName("trackOrderRow")
        self.track_id = track.id
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 5, 10, 5)
        layout.setSpacing(10)
        self.number_label = QLabel()
        self.number_label.setObjectName("trackOrderNumber")
        self.number_label.setFixedWidth(30)
        self.number_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        cover_label = QLabel()
        cover_label.setFixedSize(_COVER_PX, _COVER_PX)
        cover_label.setScaledContents(True)
        pixmap = extract_track_cover(track.file_path, track.cover_path)
        if not pixmap.isNull():
            cover_label.setPixmap(pixmap.scaled(
                _COVER_PX, _COVER_PX, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            ))
        else:
            cover_label.setObjectName("trackOrderCoverEmpty")
            cover_label.setText("♪")
            cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        meta = QLabel(
            f"<b>{escape(track.title)}</b>"
            f"<br><span style='color:#8A97A6'>{escape(track.artist)}"
            f" · {escape(track.album)}</span>"
        )
        meta.setTextFormat(Qt.TextFormat.RichText)
        self.duration_label = QLabel(track.duration_label)
        self.duration_label.setObjectName("trackOrderDuration")
        layout.addWidget(self.number_label)
        layout.addWidget(cover_label)
        layout.addWidget(meta, 1)
        layout.addWidget(self.duration_label)
        self.setToolTip(
            f"{track.title}\n{track.artist} · {track.album}\n{Path(track.file_path)}"
        )

    def set_position(self, number: int) -> None:
        self.number_label.setText(f"{number:02d}")

    def set_now_playing(self, playing: bool) -> None:
        self.setProperty("nowPlaying", playing)
        self.style().unpolish(self)
        self.style().polish(self)


class TrackOrderDialog(QDialog):
    """Reorder the playlist with drag-and-drop, buttons, and an audio preview.

    ``exec()`` returns ``Accepted`` only after the user confirms the change
    summary; the resulting order is then available as :attr:`new_order`.
    """

    def __init__(
        self, tracks: list[PlaylistTrack], translator: Translator,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.translator = translator
        korean = translator.language.value == "ko"
        self._tracks_by_id: dict[str, PlaylistTrack] = {t.id: t for t in tracks}
        self._original_ids: list[str] = [t.id for t in tracks]
        self.new_order: list[str] = list(self._original_ids)
        self._playing_id: str | None = None

        self.setWindowTitle("트랙 순서 편집기" if korean else "Track order editor")
        self.setModal(True)
        self.setMinimumSize(560, 460)
        self.resize(680, 620)

        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(preview_volume() / 100.0)
        self.media_player = QMediaPlayer(self)
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.positionChanged.connect(self._on_position_changed)
        self.media_player.durationChanged.connect(self._on_duration_changed)
        self.media_player.playbackStateChanged.connect(self._on_playback_state)
        self.media_player.mediaStatusChanged.connect(self._on_media_status)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        hint = QLabel(
            "드래그하거나 선택 후 버튼으로 순서를 바꾸고, 트랙을 더블클릭하면 미리 듣습니다."
            if korean else
            "Drag rows or select and use the buttons to reorder. "
            "Double-click a track to preview it."
        )
        hint.setObjectName("mutedLabel")
        hint.setWordWrap(True)
        root.addWidget(hint)

        body = QHBoxLayout()
        body.setSpacing(10)
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("trackOrderList")
        self.list_widget.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.list_widget.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove
        )
        self.list_widget.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.setSpacing(2)
        self.list_widget.model().rowsMoved.connect(self._on_rows_moved)
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        body.addWidget(self.list_widget, 1)

        button_column = QVBoxLayout()
        button_column.setSpacing(6)
        self.top_button = QPushButton("맨 위로" if korean else "To top")
        self.up_button = QPushButton("위로" if korean else "Up")
        self.down_button = QPushButton("아래로" if korean else "Down")
        self.bottom_button = QPushButton("맨 아래로" if korean else "To bottom")
        for button, delta in (
            (self.top_button, "top"), (self.up_button, -1),
            (self.down_button, 1), (self.bottom_button, "bottom"),
        ):
            button.clicked.connect(lambda _c=False, d=delta: self._move_selected(d))
            button_column.addWidget(button)
        button_column.addStretch(1)
        body.addLayout(button_column)
        root.addLayout(body, 1)

        transport = QHBoxLayout()
        transport.setSpacing(8)
        self.prev_button = QPushButton("⏮")
        self.play_button = QPushButton("▶")
        self.next_button = QPushButton("⏭")
        for button in (self.prev_button, self.play_button, self.next_button):
            button.setFixedWidth(44)
        self.prev_button.clicked.connect(lambda: self._step_playing(-1))
        self.next_button.clicked.connect(lambda: self._step_playing(1))
        self.play_button.clicked.connect(self._toggle_playback)
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderMoved.connect(self.media_player.setPosition)
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setObjectName("mutedLabel")
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setFixedWidth(90)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(preview_volume())
        self.volume_slider.valueChanged.connect(self._set_volume)
        transport.addWidget(self.prev_button)
        transport.addWidget(self.play_button)
        transport.addWidget(self.next_button)
        transport.addWidget(self.position_slider, 1)
        transport.addWidget(self.time_label)
        transport.addWidget(QLabel("🔊"))
        transport.addWidget(self.volume_slider)
        root.addLayout(transport)

        self.buttons = QDialogButtonBox()
        self.save_button = self.buttons.addButton(
            "저장" if korean else "Save",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self.save_button.setObjectName("primaryButton")
        self.cancel_button = self.buttons.addButton(
            QDialogButtonBox.StandardButton.Cancel
        )
        self.cancel_button.setText("취소" if korean else "Cancel")
        self.save_button.clicked.connect(self._save)
        self.cancel_button.clicked.connect(self.reject)
        root.addWidget(self.buttons)

        self.setStyleSheet(
            "#trackOrderRow[nowPlaying=\"true\"] { background: rgba(47,158,68,0.22);"
            " border: 1px solid #2F9E44; border-radius: 6px; }"
        )
        self._rebuild_rows()

    # ---- row management -------------------------------------------------

    def _rebuild_rows(self) -> None:
        korean = self.translator.language.value == "ko"
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for track_id in self.new_order:
            track = self._tracks_by_id[track_id]
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, track_id)
            item.setSizeHint(QSize(0, _COVER_PX + 12))
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, _TrackRow(track, korean))
        self.list_widget.blockSignals(False)
        self._renumber()

    def _renumber(self) -> None:
        for row in range(self.list_widget.count()):
            widget = self.list_widget.itemWidget(self.list_widget.item(row))
            if isinstance(widget, _TrackRow):
                widget.set_position(row + 1)
                widget.set_now_playing(widget.track_id == self._playing_id)

    def _current_ids(self) -> list[str]:
        return [
            str(self.list_widget.item(row).data(Qt.ItemDataRole.UserRole))
            for row in range(self.list_widget.count())
        ]

    def _on_rows_moved(self, *_args: object) -> None:
        # QListWidget's internal move rebuilds the moved QListWidgetItems from
        # mime data and drops their setItemWidget() rows, so recreate every row
        # from the (correctly reordered) ids once the drop settles.
        self.new_order = self._current_ids()
        QTimer.singleShot(0, self._rebuild_rows)

    def _move_selected(self, delta: object) -> None:
        order = self._current_ids()
        selected = {
            self.list_widget.row(item)
            for item in self.list_widget.selectedItems()
        }
        if not selected:
            return
        moving_ids = {order[row] for row in selected}
        if delta == "top":
            block = [i for r, i in enumerate(order) if r in selected]
            new_order = block + [i for i in order if i not in moving_ids]
        elif delta == "bottom":
            block = [i for r, i in enumerate(order) if r in selected]
            new_order = [i for i in order if i not in moving_ids] + block
        else:
            new_order = order[:]
            step = int(delta)
            rows = sorted(selected, reverse=step > 0)
            for row in rows:
                neighbor = row + step
                if 0 <= neighbor < len(new_order) and neighbor not in selected:
                    new_order[row], new_order[neighbor] = (
                        new_order[neighbor], new_order[row],
                    )
        if new_order == order:
            return
        self.new_order = new_order
        self._rebuild_rows()
        for row, track_id in enumerate(new_order):
            if track_id in moving_ids:
                self.list_widget.item(row).setSelected(True)

    # ---- playback ----------------------------------------------------------

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        self._play_track(str(item.data(Qt.ItemDataRole.UserRole)))

    def _play_track(self, track_id: str) -> None:
        track = self._tracks_by_id.get(track_id)
        if track is None:
            return
        path = Path(track.file_path)
        self._playing_id = track_id
        self._renumber()
        if not path.is_file():
            self.media_player.stop()
            self.time_label.setText(
                "파일 없음" if self.translator.language.value == "ko"
                else "File missing"
            )
            return
        self.media_player.setSource(QUrl.fromLocalFile(str(path.resolve())))
        self.media_player.play()

    def _step_playing(self, direction: int) -> None:
        order = self._current_ids()
        if not order:
            return
        if self._playing_id in order:
            index = order.index(self._playing_id) + direction
        else:
            index = 0 if direction > 0 else len(order) - 1
        if 0 <= index < len(order):
            self._play_track(order[index])

    def _toggle_playback(self) -> None:
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
        elif self._playing_id is not None:
            self.media_player.play()
        else:
            self._step_playing(1)

    def _set_volume(self, value: int) -> None:
        self.audio_output.setVolume(save_preview_volume(value) / 100.0)

    def _on_position_changed(self, position_ms: int) -> None:
        if not self.position_slider.isSliderDown():
            self.position_slider.setValue(position_ms)
        self.time_label.setText(
            f"{_clock(position_ms)} / {_clock(self.position_slider.maximum())}"
        )

    def _on_duration_changed(self, duration_ms: int) -> None:
        self.position_slider.setRange(0, max(0, duration_ms))

    def _on_playback_state(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setText("⏸" if playing else "▶")

    def _on_media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._step_playing(1)

    # ---- save ------------------------------------------------------------

    def _save(self) -> None:
        order = self._current_ids()
        self.new_order = order
        korean = self.translator.language.value == "ko"
        if order == self._original_ids:
            self.reject()
            return
        original_index = {tid: i for i, tid in enumerate(self._original_ids)}
        moved = [
            (self._tracks_by_id[tid].title, original_index[tid] + 1, new_index + 1)
            for new_index, tid in enumerate(order)
            if original_index.get(tid) != new_index
        ]
        lines = "\n".join(
            f"· {title}: {before}번 → {after}번" if korean
            else f"· {title}: {before} → {after}"
            for title, before, after in moved
        )
        message = (
            f"다음 {len(moved)}개 곡의 순서가 바뀝니다:\n\n{lines}\n\n계속할까요?"
            if korean else
            f"{len(moved)} track(s) will move:\n\n{lines}\n\nApply these changes?"
        )
        box = QMessageBox(self)
        box.setWindowTitle("변경 사항 확인" if korean else "Confirm changes")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(message)
        continue_button = box.addButton(
            "계속하기" if korean else "Continue", QMessageBox.ButtonRole.AcceptRole
        )
        box.addButton(
            "취소" if korean else "Cancel", QMessageBox.ButtonRole.RejectRole
        )
        box.exec()
        if box.clickedButton() is continue_button:
            self.accept()

    def reject(self) -> None:  # noqa: D102
        self.media_player.stop()
        super().reject()

    def done(self, result: int) -> None:  # noqa: D102
        self.media_player.stop()
        super().done(result)
