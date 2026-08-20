"""Modeless, synchronized live lyric preview for the LRC generator."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from app.services.lyrics_service import LyricsService
from app.utils.i18n import Language, Translator


class LrcLivePreviewDialog(QDialog):
    """Show timed lyrics in a separate window using the editor's media player."""

    def __init__(
        self,
        media_player: QMediaPlayer,
        cues_provider: Callable[[], list[dict[str, object]]],
        translator: Translator,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.media_player = media_player
        self.cues_provider = cues_provider
        self.translator = translator
        self.setMinimumSize(620, 330)
        self.resize(760, 420)

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 20)
        root.setSpacing(12)
        self.status_label = QLabel()
        self.status_label.setObjectName("mutedLabel")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.status_label)
        root.addStretch(1)

        self.previous_lyric = QLabel()
        self.previous_lyric.setObjectName("mutedLabel")
        self.current_lyric = QLabel()
        self.current_lyric.setObjectName("panelTitle")
        current_font = self.current_lyric.font()
        current_font.setPointSize(max(18, current_font.pointSize() + 8))
        current_font.setBold(True)
        self.current_lyric.setFont(current_font)
        self.next_lyric = QLabel()
        self.next_lyric.setObjectName("mutedLabel")
        for label in (self.previous_lyric, self.current_lyric, self.next_lyric):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            label.setMinimumHeight(42)
        self.current_lyric.setMinimumHeight(74)
        root.addWidget(self.previous_lyric)
        root.addWidget(self.current_lyric)
        root.addWidget(self.next_lyric)
        root.addStretch(1)

        controls = QHBoxLayout()
        self.restart_button = QPushButton()
        self.play_button = QPushButton()
        self.position_label = QLabel()
        self.position_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.close_button = QPushButton()
        controls.addWidget(self.restart_button)
        controls.addWidget(self.play_button)
        controls.addStretch(1)
        controls.addWidget(self.position_label)
        controls.addStretch(1)
        controls.addWidget(self.close_button)
        root.addLayout(controls)

        self.restart_button.clicked.connect(self.restart)
        self.play_button.clicked.connect(self.toggle_playback)
        self.close_button.clicked.connect(self.close)
        media_player.positionChanged.connect(self.refresh)
        media_player.durationChanged.connect(lambda _duration: self.refresh())
        media_player.playbackStateChanged.connect(self._playback_state_changed)
        translator.language_changed.connect(self.retranslate)
        self.retranslate()
        self.refresh()

    def restart(self) -> None:
        self.media_player.setPosition(0)
        self.media_player.play()

    def toggle_playback(self) -> None:
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
        else:
            self.media_player.play()

    def refresh(self, position: int | None = None) -> None:
        """Refresh from the latest editor timings and shared playback position."""
        milliseconds = self.media_player.position() if position is None else position
        cues = self.cues_provider()
        index = LyricsService.current_cue_index(cues, milliseconds / 1000.0)
        if index is None:
            self.previous_lyric.clear()
            self.current_lyric.setText(
                "재생 위치에 가사가 없습니다."
                if self.translator.language is Language.KOREAN
                else "No lyric at the current position."
            )
            self.next_lyric.setText(
                LyricsService.decode_line_breaks(cues[0]["text"]) if cues else ""
            )
        else:
            self.previous_lyric.setText(
                LyricsService.decode_line_breaks(cues[index - 1]["text"])
                if index > 0 else ""
            )
            self.current_lyric.setText(
                LyricsService.decode_line_breaks(cues[index]["text"])
            )
            self.next_lyric.setText(
                LyricsService.decode_line_breaks(cues[index + 1]["text"])
                if index + 1 < len(cues) else ""
            )
        self.position_label.setText(
            f"{self._clock(milliseconds)} / {self._clock(self.media_player.duration())}"
        )
        self.status_label.setText(
            f"기록된 가사 {len(cues)}줄"
            if self.translator.language is Language.KOREAN
            else f"{len(cues)} timed lyric line(s)"
        )

    def _playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        self.play_button.setText(
            ("일시정지" if self.translator.language is Language.KOREAN else "Pause")
            if state == QMediaPlayer.PlaybackState.PlayingState
            else ("재생" if self.translator.language is Language.KOREAN else "Play")
        )

    def retranslate(self) -> None:
        korean = self.translator.language is Language.KOREAN
        self.setWindowTitle("LRC 실시간 가사 미리보기" if korean else "LRC Live Lyrics Preview")
        self.restart_button.setText("처음부터 재생" if korean else "Play from start")
        self.close_button.setText("닫기" if korean else "Close")
        self._playback_state_changed(self.media_player.playbackState())
        self.refresh()

    @staticmethod
    def _clock(milliseconds: int) -> str:
        total_seconds = max(0, milliseconds) // 1000
        minutes, seconds = divmod(total_seconds, 60)
        return f"{minutes:02d}:{seconds:02d}"
