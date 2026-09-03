"""Type-aware preview and file information for project content."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QFontDatabase, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QSlider, QStyle, QTextEdit, QVBoxLayout, QWidget,
)

from app.services.preview_audio_settings import preview_volume, save_preview_volume


class _SeekSlider(QSlider):
    """Horizontal media slider that also seeks when its empty groove is clicked."""

    seek_requested = Signal(int)

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().mousePressEvent(event)
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.orientation() == Qt.Orientation.Horizontal
            and self.width() > 0
        ):
            ratio = max(0.0, min(1.0, event.position().x() / self.width()))
            value = round(self.minimum() + ratio * (self.maximum() - self.minimum()))
            self.setValue(value)
            self.seek_requested.emit(value)


class ContentPreviewDialog(QDialog):
    """Preview supported media without adding it to the project timeline."""

    def __init__(self, path: str, media_type: str, korean: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = Path(path)
        self.media_type = media_type
        self.korean = korean
        self.player: QMediaPlayer | None = None
        self.audio_output: QAudioOutput | None = None
        self._duration_ms = 0
        saved_volume = preview_volume()
        self._saved_volume = saved_volume
        self._last_nonzero_volume = saved_volume if saved_volume > 0 else 80
        self.setWindowTitle("콘텐츠 미리보기" if korean else "Content preview")
        self.resize(820, 650 if media_type == "video" else 500)
        self.setMinimumSize(600, 400 if media_type == "video" else 360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        title = QLabel(self.path.name)
        title.setObjectName("panelTitle")
        title.setWordWrap(True)
        layout.addWidget(title)
        preview = self._create_preview(korean)
        layout.addWidget(preview, 1)
        available = self.path.is_file()
        size = self.path.stat().st_size if available else 0
        info = QLabel(
            (f"유형: {media_type} · 크기: {self._format_size(size)}\n경로: {self.path}"
             if korean else
             f"Type: {media_type} · Size: {self._format_size(size)}\nPath: {self.path}")
        )
        info.setObjectName("mutedLabel")
        info.setWordWrap(True)
        info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(info)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(
            "닫기" if korean else "Close"
        )
        layout.addWidget(buttons)

    def _create_preview(self, korean: bool) -> QWidget:
        if not self.path.is_file():
            label = QLabel("파일을 찾을 수 없습니다." if korean else "The file is missing.")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            return label
        if self.media_type == "image":
            label = QLabel()
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pixmap = QPixmap(str(self.path))
            label.setPixmap(pixmap.scaled(
                660, 410, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
            return label
        if self.media_type in {"audio", "video"}:
            host = QFrame()
            host.setObjectName("contentPlayerCard")
            host_layout = QVBoxLayout(host)
            host_layout.setContentsMargins(14, 14, 14, 14)
            host_layout.setSpacing(10)
            self.player = QMediaPlayer(self)
            self.audio_output = QAudioOutput(self)
            self.audio_output.setVolume(self._saved_volume / 100.0)
            self.player.setAudioOutput(self.audio_output)
            if self.media_type == "video":
                video = QVideoWidget()
                video.setObjectName("contentVideoSurface")
                video.setMinimumHeight(360)
                self.player.setVideoOutput(video)
                host_layout.addWidget(video, 1)
            else:
                audio_stage = QFrame()
                audio_stage.setObjectName("contentAudioStage")
                audio_stage.setMinimumHeight(112)
                stage_layout = QVBoxLayout(audio_stage)
                stage_layout.setContentsMargins(18, 16, 18, 16)
                media_mark = QLabel("♫")
                media_mark.setObjectName("contentAudioMark")
                media_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
                audio_name = QLabel(self.path.stem)
                audio_name.setObjectName("contentAudioName")
                audio_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
                audio_name.setWordWrap(True)
                stage_layout.addStretch(1)
                stage_layout.addWidget(media_mark)
                stage_layout.addWidget(audio_name)
                stage_layout.addStretch(1)
                host_layout.addWidget(audio_stage, 1)

            self.status_label = QLabel(
                "미디어를 불러오는 중…" if korean else "Loading media…"
            )
            self.status_label.setObjectName("contentPlaybackStatus")
            self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.status_label.setWordWrap(True)
            host_layout.addWidget(self.status_label)

            timeline_row = QHBoxLayout()
            timeline_row.setSpacing(9)
            self.elapsed_label = QLabel("00:00")
            self.elapsed_label.setObjectName("contentTimeLabel")
            self.elapsed_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.elapsed_label.setMinimumWidth(52)
            self.position_slider = _SeekSlider(Qt.Orientation.Horizontal)
            self.position_slider.setObjectName("contentSeekSlider")
            self.position_slider.setRange(0, 0)
            self.position_slider.setSingleStep(1000)
            self.position_slider.setPageStep(5000)
            self.position_slider.setTracking(True)
            self.position_slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.duration_label = QLabel("00:00")
            self.duration_label.setObjectName("contentTimeLabel")
            self.duration_label.setMinimumWidth(52)
            timeline_row.addWidget(self.elapsed_label)
            timeline_row.addWidget(self.position_slider, 1)
            timeline_row.addWidget(self.duration_label)
            host_layout.addLayout(timeline_row)

            transport_row = QHBoxLayout()
            transport_row.setSpacing(7)
            transport_row.addStretch(1)
            self.backward_button = QPushButton("5초 이전" if korean else "Back 5s")
            self.play_button = QPushButton("재생" if korean else "Play")
            self.play_button.setObjectName("primaryButton")
            self.stop_button = QPushButton("정지" if korean else "Stop")
            self.forward_button = QPushButton("5초 이후" if korean else "Forward 5s")
            self.backward_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSeekBackward))
            self.play_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
            self.stop_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
            self.forward_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSeekForward))
            self.backward_button.setToolTip("현재 위치에서 5초 전으로 이동" if korean else "Seek backward 5 seconds")
            self.play_button.setToolTip("재생 또는 일시정지" if korean else "Play or pause")
            self.stop_button.setToolTip("재생을 멈추고 처음으로 이동" if korean else "Stop and return to the beginning")
            self.forward_button.setToolTip("현재 위치에서 5초 후로 이동" if korean else "Seek forward 5 seconds")
            for button in (
                self.backward_button, self.play_button, self.stop_button,
                self.forward_button,
            ):
                button.setMinimumHeight(34)
                transport_row.addWidget(button)
            transport_row.addStretch(1)
            host_layout.addLayout(transport_row)

            volume_row = QHBoxLayout()
            volume_row.setSpacing(9)
            self.mute_button = QPushButton("음소거" if korean else "Mute")
            self.mute_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume))
            self.mute_button.setMinimumWidth(96)
            self.mute_button.setToolTip("소리를 끄거나 다시 켭니다." if korean else "Mute or restore audio.")
            self.volume_slider = QSlider(Qt.Orientation.Horizontal)
            self.volume_slider.setObjectName("contentVolumeSlider")
            self.volume_slider.setRange(0, 100)
            self.volume_slider.setValue(self._saved_volume)
            self.volume_slider.setSingleStep(2)
            self.volume_slider.setPageStep(10)
            self.volume_value_label = QLabel(f"{self._saved_volume}%")
            self.volume_value_label.setObjectName("contentTimeLabel")
            self.volume_value_label.setMinimumWidth(42)
            self.volume_value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            volume_row.addWidget(self.mute_button)
            volume_row.addWidget(self.volume_slider, 1)
            volume_row.addWidget(self.volume_value_label)
            host_layout.addLayout(volume_row)

            self.play_button.clicked.connect(self._toggle_playback)
            self.stop_button.clicked.connect(self._stop_playback)
            self.backward_button.clicked.connect(lambda: self._skip(-5000))
            self.forward_button.clicked.connect(lambda: self._skip(5000))
            self.position_slider.seek_requested.connect(self._seek_to)
            self.position_slider.sliderMoved.connect(self._preview_seek_time)
            self.position_slider.sliderReleased.connect(
                lambda: self._seek_to(self.position_slider.value())
            )
            self.volume_slider.valueChanged.connect(self._set_volume)
            self.mute_button.clicked.connect(self._toggle_mute)
            self.player.positionChanged.connect(self._position_changed)
            self.player.durationChanged.connect(self._duration_changed)
            self.player.playbackStateChanged.connect(self._playback_state_changed)
            self.player.mediaStatusChanged.connect(self._media_status_changed)
            self.player.errorOccurred.connect(self._playback_error)
            self.player.setSource(QUrl.fromLocalFile(str(self.path.resolve())))
            self._sync_mute_button()
            host.setStyleSheet(
                "QFrame#contentPlayerCard { background: palette(base); border: 1px solid palette(mid); border-radius: 11px; }"
                "QFrame#contentAudioStage, QVideoWidget#contentVideoSurface { background: palette(alternate-base); border: 0; border-radius: 8px; }"
                "QLabel#contentAudioMark { color: palette(highlight); font-size: 34px; font-weight: 700; border: 0; }"
                "QLabel#contentAudioName { font-size: 14px; font-weight: 650; border: 0; }"
                "QLabel#contentPlaybackStatus { color: palette(mid); font-size: 11px; border: 0; }"
                "QLabel#contentTimeLabel { font-family: monospace; font-size: 11px; border: 0; }"
                "QSlider#contentSeekSlider::groove:horizontal, QSlider#contentVolumeSlider::groove:horizontal {"
                " height: 6px; border-radius: 3px; background: palette(mid); }"
                "QSlider#contentSeekSlider::sub-page:horizontal, QSlider#contentVolumeSlider::sub-page:horizontal {"
                " background: palette(highlight); border-radius: 3px; }"
                "QSlider#contentSeekSlider::handle:horizontal, QSlider#contentVolumeSlider::handle:horizontal {"
                " width: 16px; margin: -5px 0; border-radius: 8px; background: palette(highlight); border: 2px solid palette(base); }"
            )
            return host
        text = QTextEdit()
        text.setReadOnly(True)
        if self.media_type == "font":
            font_id = QFontDatabase.addApplicationFont(str(self.path))
            families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
            font = text.font()
            if families:
                font.setFamily(families[0])
            font.setPointSize(24)
            text.setFont(font)
            text.setPlainText("가나다라마바사 ABCDEFG abcdefg 0123456789")
        else:
            try:
                text.setPlainText(self.path.read_text(encoding="utf-8", errors="replace")[:20_000])
            except OSError as error:
                text.setPlainText(str(error))
        return text

    def _toggle_playback(self) -> None:
        if self.player is None:
            return
        if self.player.playbackState() is QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _stop_playback(self) -> None:
        if self.player is None:
            return
        self.player.stop()
        self.player.setPosition(0)
        self._position_changed(0)
        self.status_label.setText("정지됨" if self.korean else "Stopped")

    def _skip(self, offset_ms: int) -> None:
        if self.player is None:
            return
        self._seek_to(self.player.position() + offset_ms)

    def _seek_to(self, position_ms: int) -> None:
        if self.player is None:
            return
        position = max(0, min(max(0, self._duration_ms), int(position_ms)))
        self.player.setPosition(position)
        self._position_changed(position)

    def _preview_seek_time(self, position_ms: int) -> None:
        self.elapsed_label.setText(self._format_time(position_ms))

    def _position_changed(self, position_ms: int) -> None:
        position = max(0, int(position_ms))
        if not self.position_slider.isSliderDown():
            self.position_slider.setValue(position)
        self.elapsed_label.setText(self._format_time(position))

    def _duration_changed(self, duration_ms: int) -> None:
        self._duration_ms = max(0, int(duration_ms))
        self.position_slider.setRange(0, self._duration_ms)
        self.duration_label.setText(self._format_time(self._duration_ms))

    def _playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setText(
            ("일시정지" if self.korean else "Pause")
            if playing else ("재생" if self.korean else "Play")
        )
        self.play_button.setIcon(self.style().standardIcon(
            QStyle.StandardPixmap.SP_MediaPause
            if playing else QStyle.StandardPixmap.SP_MediaPlay
        ))
        if playing:
            self.status_label.setText("재생 중" if self.korean else "Playing")
        elif self.player is not None and self.player.mediaStatus() != QMediaPlayer.MediaStatus.EndOfMedia:
            self.status_label.setText("일시정지됨" if self.korean else "Paused")

    def _media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status in {
            QMediaPlayer.MediaStatus.LoadingMedia,
            QMediaPlayer.MediaStatus.BufferingMedia,
            QMediaPlayer.MediaStatus.StalledMedia,
        }:
            self.status_label.setText(
                "미디어를 준비하는 중…" if self.korean else "Preparing media…"
            )
        elif status in {QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia}:
            if self.player is not None and self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                self.status_label.setText(
                    "재생할 준비가 되었습니다." if self.korean else "Ready to play."
                )
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.status_label.setText("재생이 끝났습니다." if self.korean else "Playback finished.")
            self._position_changed(self._duration_ms)
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self.status_label.setText(
                "이 미디어를 재생할 수 없습니다." if self.korean else "This media cannot be played."
            )

    def _set_volume(self, value: int) -> None:
        if self.audio_output is None:
            return
        normalized = save_preview_volume(value)
        if normalized > 0:
            self._last_nonzero_volume = normalized
            if self.audio_output.isMuted():
                self.audio_output.setMuted(False)
        self.audio_output.setVolume(normalized / 100.0)
        self.volume_value_label.setText(f"{normalized}%")
        self._sync_mute_button()

    def _toggle_mute(self) -> None:
        if self.audio_output is None:
            return
        muted = self.audio_output.isMuted() or self.volume_slider.value() == 0
        if muted:
            self.audio_output.setMuted(False)
            if self.volume_slider.value() == 0:
                self.volume_slider.setValue(self._last_nonzero_volume)
        else:
            self.audio_output.setMuted(True)
        self._sync_mute_button()

    def _sync_mute_button(self) -> None:
        if self.audio_output is None:
            return
        muted = self.audio_output.isMuted() or self.volume_slider.value() == 0
        self.mute_button.setText(
            ("음소거 해제" if self.korean else "Unmute")
            if muted else ("음소거" if self.korean else "Mute")
        )
        self.mute_button.setIcon(self.style().standardIcon(
            QStyle.StandardPixmap.SP_MediaVolumeMuted
            if muted else QStyle.StandardPixmap.SP_MediaVolume
        ))

    def _playback_error(self, _error: QMediaPlayer.Error, message: str = "") -> None:
        fallback = "미디어를 재생할 수 없습니다." if self.korean else "Could not play this media."
        self.status_label.setText(message.strip() or fallback)
        for widget in (
            self.play_button, self.stop_button, self.backward_button,
            self.forward_button, self.position_slider,
        ):
            widget.setEnabled(False)

    def done(self, result: int) -> None:
        if self.player is not None:
            self.player.stop()
            self.player.setVideoOutput(None)
            self.player.setAudioOutput(None)
            self.player.setSource(QUrl())
        super().done(result)

    @staticmethod
    def _format_size(size: int) -> str:
        value = float(max(0, size))
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024.0 or unit == "GB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024.0
        return "0 B"

    @staticmethod
    def _format_time(position_ms: int) -> str:
        total_seconds = max(0, int(position_ms)) // 1000
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

