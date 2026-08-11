"""Timeline editor for playlist tracks and visual source display ranges."""

from __future__ import annotations

import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.models.source import Source
from app.services.playlist_service import PlaylistService
from app.services.source_store import SourceStore
from app.utils.i18n import Translator


class TimelineSpinBox(QDoubleSpinBox):
    """Seconds input that accepts and displays a friendly ``MM:SS`` timecode."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setRange(0.0, 86_400.0)
        self.setDecimals(2)
        self.setSingleStep(1.0)
        self.setKeyboardTracking(False)
        self.setMinimumWidth(82)

    def textFromValue(self, value: float) -> str:
        """Format seconds into hour-aware timecode."""
        seconds = max(0, round(value))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def valueFromText(self, text: str) -> float:
        """Parse plain seconds, ``MM:SS``, or ``HH:MM:SS`` input."""
        parts = [part.strip() for part in text.split(":")]
        try:
            if len(parts) == 1:
                return float(parts[0])
            if len(parts) == 2:
                return float(parts[0]) * 60 + float(parts[1])
            if len(parts) == 3:
                return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        except ValueError:
            return self.value()
        return self.value()

    def validate(self, text: str, position: int) -> tuple[QValidator.State, str, int]:
        """Accept partial numeric timecode while the user is typing."""
        if re.fullmatch(r"[0-9:.]*", text):
            return (QValidator.State.Acceptable, text, position)
        return super().validate(text, position)


class TimelinePanel(QFrame):
    """Two-track editor: music timing above and source display timing below."""

    def __init__(self, playlist: PlaylistService, sources: SourceStore,
                 translator: Translator, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("timelineStrip")
        self.playlist = playlist
        self.sources = sources
        self.translator = translator
        self._refreshing = False
        self._refresh_pending = False
        self.setMinimumHeight(250)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(10)
        header = QHBoxLayout()
        self.title = QLabel()
        self.title.setObjectName("panelTitle")
        self.summary = QLabel()
        self.summary.setObjectName("mutedLabel")
        self.up_button = QPushButton("↑")
        self.down_button = QPushButton("↓")
        self.up_button.setObjectName("timelineMoveButton")
        self.down_button.setObjectName("timelineMoveButton")
        header.addWidget(self.title)
        header.addWidget(self.summary)
        header.addStretch()
        header.addWidget(self.up_button)
        header.addWidget(self.down_button)
        layout.addLayout(header)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("timelineTabs")
        self.tabs.setDocumentMode(True)
        self.track_table = QTableWidget(0, 5)
        self.track_table.setObjectName("timelineTrackTable")
        self._configure_table(self.track_table)
        self.source_table = QTableWidget(0, 3)
        self.source_table.setObjectName("timelineSourceTable")
        self._configure_table(self.source_table)
        self.track_page = self._table_page(self.track_table)
        self.source_page = self._table_page(self.source_table)
        self.tabs.addTab(self.track_page, "")
        self.tabs.addTab(self.source_page, "")
        layout.addWidget(self.tabs, 1)
        self.up_button.clicked.connect(lambda: self._move_selected_track(-1))
        self.down_button.clicked.connect(lambda: self._move_selected_track(1))
        self.source_table.itemSelectionChanged.connect(self._select_source_on_canvas)
        playlist.playlist_changed.connect(self.schedule_refresh)
        sources.source_added.connect(lambda _source: self.schedule_refresh())
        sources.source_removed.connect(lambda _source_id: self.schedule_refresh())
        sources.source_changed.connect(lambda _source: self.schedule_refresh())
        sources.sources_replaced.connect(self.schedule_refresh)
        translator.language_changed.connect(self.retranslate)
        self.retranslate()
        self.refresh()

    @staticmethod
    def _table_page(table: QTableWidget) -> QWidget:
        """Place one timing table inside a clean tab page without extra chrome."""
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 6, 0, 0)
        page_layout.addWidget(table)
        return page

    @staticmethod
    def _configure_table(table: QTableWidget) -> None:
        """Apply consistent compact editor-table behavior to timeline sections."""
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setFrameShape(QFrame.Shape.NoFrame)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(38)
        table.horizontalHeader().setFixedHeight(30)

    def retranslate(self) -> None:
        """Refresh static labels in the current language."""
        korean = self.translator.language.value == "ko"
        self.title.setText("타임라인" if korean else "Timeline")
        self.track_table.setHorizontalHeaderLabels(
            ["#", "트랙" if korean else "Track", "시작" if korean else "Start",
             "길이" if korean else "Duration", "종료" if korean else "End"]
        )
        self.source_table.setHorizontalHeaderLabels(
            ["소스" if korean else "Source", "시작" if korean else "Start",
             "지속 시간" if korean else "Duration"]
        )
        self.tabs.setTabText(0, "음악 타임라인" if korean else "Music timeline")
        self.tabs.setTabText(1, "소스 타이밍" if korean else "Source timing")
        self.up_button.setToolTip("위로 이동" if korean else "Move up")
        self.down_button.setToolTip("아래로 이동" if korean else "Move down")
        self.refresh()

    def refresh(self) -> None:
        """Rebuild timeline rows from the playlist and source stores."""
        if self._refreshing:
            return
        self._refreshing = True
        try:
            timeline_tracks = self.playlist.timeline_tracks()
            self.track_table.setRowCount(len(timeline_tracks))
            for row, (track, start, end) in enumerate(timeline_tracks):
                number = QTableWidgetItem(str(row + 1))
                name = QTableWidgetItem(f"{track.title} — {track.artist}")
                number.setData(Qt.ItemDataRole.UserRole, track.id)
                self.track_table.setItem(row, 0, number)
                self.track_table.setItem(row, 1, name)
                start_editor = TimelineSpinBox()
                minimum_start = self.playlist.minimum_start_time(track.id)
                start_editor.setMinimum(minimum_start)
                start_editor.setValue(max(start, minimum_start))
                start_editor.setToolTip(
                    f"Minimum {TimelineSpinBox().textFromValue(minimum_start)}"
                )
                start_editor.valueChanged.connect(
                    lambda value, identifier=track.id: self._on_track_start_changed(identifier, value)
                )
                self.track_table.setCellWidget(row, 2, start_editor)
                duration = QTableWidgetItem(track.duration_label)
                end_item = QTableWidgetItem(TimelineSpinBox().textFromValue(end))
                self.track_table.setItem(row, 3, duration)
                self.track_table.setItem(row, 4, end_item)
            self.track_table.setColumnWidth(0, 44)
            self.track_table.setColumnWidth(1, 330)
            self.track_table.setColumnWidth(2, 110)
            self.track_table.setColumnWidth(3, 82)
            self.track_table.horizontalHeader().setStretchLastSection(True)

            source_list = self.sources.sources()
            self.source_table.setRowCount(len(source_list))
            for row, source in enumerate(source_list):
                source_item = QTableWidgetItem(source.name)
                source_item.setData(Qt.ItemDataRole.UserRole, source.id)
                self.source_table.setItem(row, 0, source_item)
                start_editor = TimelineSpinBox()
                start_editor.setValue(source.timeline_start)
                start_editor.valueChanged.connect(
                    lambda value, identifier=source.id: self._on_source_timing_changed(
                        identifier, "timeline_start", value
                    )
                )
                duration_editor = TimelineSpinBox()
                duration_editor.setSpecialValueText("Full")
                duration_editor.setValue(source.timeline_duration)
                duration_editor.valueChanged.connect(
                    lambda value, identifier=source.id: self._on_source_timing_changed(
                        identifier, "timeline_duration", value
                    )
                )
                self.source_table.setCellWidget(row, 1, start_editor)
                self.source_table.setCellWidget(row, 2, duration_editor)
            self.source_table.setColumnWidth(0, 320)
            self.source_table.setColumnWidth(1, 112)
            self.source_table.horizontalHeader().setStretchLastSection(True)
            total = max((end for _track, _start, end in timeline_tracks), default=0.0)
            self.summary.setText(
                f"{len(timeline_tracks)}곡 · 총 {TimelineSpinBox().textFromValue(total)}"
                if self.translator.language.value == "ko" else
                f"{len(timeline_tracks)} tracks · {TimelineSpinBox().textFromValue(total)}"
            )
        finally:
            self._refreshing = False

    def schedule_refresh(self) -> None:
        """Coalesce store signals so Undo does not rebuild tables repeatedly."""
        if self._refresh_pending:
            return
        self._refresh_pending = True

        def perform_refresh() -> None:
            self._refresh_pending = False
            self.refresh()

        QTimer.singleShot(0, perform_refresh)

    def _selected_track_id(self) -> str | None:
        row = self.track_table.currentRow()
        item = self.track_table.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _move_selected_track(self, direction: int) -> None:
        track_id = self._selected_track_id()
        if track_id:
            self.playlist.move_track(track_id, direction)

    def _on_track_start_changed(self, track_id: str, value: float) -> None:
        """Commit an edited track start without rebuilding table widgets mid-signal."""
        if self._refreshing:
            return
        self._refreshing = True
        try:
            self.playlist.set_start_time(track_id, value)
        finally:
            self._refreshing = False
        QTimer.singleShot(0, self.refresh)

    def _on_source_timing_changed(self, source_id: str, field: str, value: float) -> None:
        """Commit one source timing field without destroying the active spin box."""
        if self._refreshing:
            return
        self._refreshing = True
        try:
            self.sources.update(source_id, **{field: value})
        finally:
            self._refreshing = False
        QTimer.singleShot(0, self.refresh)

    def _select_source_on_canvas(self) -> None:
        """Select a source in Canvas and Inspector from its timeline row."""
        if self._refreshing:
            return
        row = self.source_table.currentRow()
        item = self.source_table.item(row, 0) if row >= 0 else None
        if item:
            self.sources.select(item.data(Qt.ItemDataRole.UserRole))
