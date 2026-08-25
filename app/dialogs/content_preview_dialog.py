"""Type-aware preview and file information for project content."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QFontDatabase, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QTextEdit,
    QVBoxLayout, QWidget,
)


class ContentPreviewDialog(QDialog):
    """Preview supported media without adding it to the project timeline."""

    def __init__(self, path: str, media_type: str, korean: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = Path(path)
        self.media_type = media_type
        self.player: QMediaPlayer | None = None
        self.audio_output: QAudioOutput | None = None
        self.setWindowTitle("콘텐츠 미리보기" if korean else "Content preview")
        self.resize(720, 560)
        layout = QVBoxLayout(self)
        title = QLabel(self.path.name)
        title.setObjectName("panelTitle")
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
            host = QWidget()
            host_layout = QVBoxLayout(host)
            self.player = QMediaPlayer(self)
            self.audio_output = QAudioOutput(self)
            self.player.setAudioOutput(self.audio_output)
            if self.media_type == "video":
                video = QVideoWidget()
                video.setMinimumHeight(360)
                self.player.setVideoOutput(video)
                host_layout.addWidget(video, 1)
            status = QLabel(
                "재생 버튼을 눌러 미리 봅니다." if korean else "Press Play to preview."
            )
            status.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row = QHBoxLayout()
            play = QPushButton("재생 / 일시정지" if korean else "Play / pause")
            stop = QPushButton("정지" if korean else "Stop")
            play.clicked.connect(self._toggle_playback)
            stop.clicked.connect(self.player.stop)
            row.addStretch(1)
            row.addWidget(play)
            row.addWidget(stop)
            row.addStretch(1)
            host_layout.addWidget(status)
            host_layout.addLayout(row)
            self.player.setSource(QUrl.fromLocalFile(str(self.path.resolve())))
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

    def done(self, result: int) -> None:
        if self.player is not None:
            self.player.stop()
        super().done(result)

    @staticmethod
    def _format_size(size: int) -> str:
        value = float(max(0, size))
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024.0 or unit == "GB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024.0
        return "0 B"

