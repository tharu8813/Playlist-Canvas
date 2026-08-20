"""Track metadata, lyrics attachment, and per-track synchronization editor."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.models.playlist import PlaylistTrack
from app.dialogs.lrc_generator_dialog import LrcGeneratorDialog
from app.services.lyrics_service import LyricsError, LyricsService
from app.services.preview_audio_settings import preview_volume, save_preview_volume
from app.utils.i18n import Translator


class TrackDetailsDialog(QDialog):
    """Edit timed lyrics and synchronization without mutating the track on Cancel."""

    def __init__(
        self, track: PlaylistTrack, translator: Translator,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.track = track
        self.translator = translator
        self.selected_lyrics_path = track.lyrics_path
        self.selected_lyrics = [cue.copy() for cue in track.lyrics]
        self.selected_timing_offset = float(track.lyrics_timing_offset_seconds)
        self.selected_title = track.title
        self.selected_artist = track.artist
        self.selected_album = track.album
        saved_volume = preview_volume()
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(saved_volume / 100.0)
        self.media_player = QMediaPlayer(self)
        self.media_player.setAudioOutput(self.audio_output)
        self._audio_available = Path(track.file_path).is_file()
        if self._audio_available:
            self.media_player.setSource(QUrl.fromLocalFile(str(Path(track.file_path).resolve())))
        self.setMinimumSize(780, 540)
        self.resize(860, 600)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        self.info_group = QGroupBox()
        info_form = QFormLayout(self.info_group)
        info_form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        info_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.info_name_labels: list[QLabel] = []
        self.title_edit = QLineEdit(track.title)
        self.artist_edit = QLineEdit(track.artist)
        self.album_edit = QLineEdit(track.album)
        self.metadata_edits = (self.title_edit, self.artist_edit, self.album_edit)
        for field in self.metadata_edits:
            field.setObjectName("trackMetadataEdit")
            field.setClearButtonEnabled(True)
            field.setPlaceholderText("—")
            field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.file_label = QLabel(track.file_path.strip() or "—")
        self.file_label.setToolTip(track.file_path)
        self.duration_label = QLabel(track.duration_label)
        self.info_labels = [
            self.title_edit, self.artist_edit, self.album_edit,
            self.file_label, self.duration_label,
        ]
        for value, field in zip(
            (track.title, track.artist, track.album, track.file_path, track.duration_label),
            self.info_labels,
        ):
            name_label = QLabel()
            name_label.setObjectName("trackInfoName")
            name_label.setMinimumWidth(78)
            name_label.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
            )
            if isinstance(field, QLabel):
                field.setObjectName("trackInfoValue")
                field.setWordWrap(True)
                field.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            self.info_name_labels.append(name_label)
            info_form.addRow(name_label, field)
        root.addWidget(self.info_group)

        self.lyrics_group = QGroupBox()
        lyrics_layout = QVBoxLayout(self.lyrics_group)
        path_row = QHBoxLayout()
        self.lyrics_path = QLabel()
        self.lyrics_path.setObjectName("mutedLabel")
        self.lyrics_path.setWordWrap(True)
        self.load_button = QPushButton()
        self.edit_lrc_button = QPushButton()
        self.export_lrc_button = QPushButton()
        self.clear_button = QPushButton()
        path_row.addWidget(self.lyrics_path, 1)
        path_row.addWidget(self.load_button)
        path_row.addWidget(self.clear_button)
        lyrics_layout.addLayout(path_row)
        lyrics_action_row = QHBoxLayout()
        lyrics_action_row.addStretch(1)
        lyrics_action_row.addWidget(self.edit_lrc_button)
        lyrics_action_row.addWidget(self.export_lrc_button)
        lyrics_layout.addLayout(lyrics_action_row)

        timing_form = QFormLayout()
        self.timing_label = QLabel()
        self.timing_offset_spin = QDoubleSpinBox()
        self.timing_offset_spin.setRange(-30.0, 30.0)
        self.timing_offset_spin.setDecimals(2)
        self.timing_offset_spin.setSingleStep(0.05)
        self.timing_offset_spin.setSuffix(" s")
        self.timing_offset_spin.setKeyboardTracking(False)
        self.timing_offset_spin.setValue(self.selected_timing_offset)
        timing_controls = QWidget()
        timing_controls_layout = QHBoxLayout(timing_controls)
        timing_controls_layout.setContentsMargins(0, 0, 0, 0)
        timing_controls_layout.setSpacing(5)
        self.earlier_button = QPushButton("−0.10")
        self.later_button = QPushButton("+0.10")
        self.reset_button = QPushButton()
        timing_controls_layout.addWidget(self.timing_offset_spin, 1)
        timing_controls_layout.addWidget(self.earlier_button)
        timing_controls_layout.addWidget(self.later_button)
        timing_controls_layout.addWidget(self.reset_button)
        timing_form.addRow(self.timing_label, timing_controls)
        lyrics_layout.addLayout(timing_form)

        self.timing_help = QLabel()
        self.timing_help.setObjectName("mutedLabel")
        self.timing_help.setWordWrap(True)
        lyrics_layout.addWidget(self.timing_help)

        self.playback_group = QGroupBox()
        playback_layout = QVBoxLayout(self.playback_group)
        playback_layout.setSpacing(7)
        self.playback_status = QLabel()
        self.playback_status.setObjectName("mutedLabel")
        self.playback_status.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.lyrics_preview_host = QWidget()
        self.lyrics_preview_layout = QVBoxLayout(self.lyrics_preview_host)
        self.lyrics_preview_layout.setContentsMargins(4, 0, 4, 0)
        self.lyrics_preview_layout.setSpacing(3)
        self.previous_lyric = QLabel()
        self.previous_lyric.setObjectName("mutedLabel")
        self.previous_lyric.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.previous_lyric.setWordWrap(True)
        self.previous_lyric.setMaximumHeight(54)
        self.current_lyric = QLabel()
        self.current_lyric.setObjectName("panelTitle")
        self.current_lyric.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.current_lyric.setWordWrap(True)
        self.current_lyric.setMinimumHeight(42)
        self.current_lyric.setMaximumHeight(84)
        self.next_lyric = QLabel()
        self.next_lyric.setObjectName("mutedLabel")
        self.next_lyric.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.next_lyric.setWordWrap(True)
        self.next_lyric.setMaximumHeight(54)
        self.lyrics_preview_layout.addWidget(self.previous_lyric)
        self.lyrics_preview_layout.addWidget(self.current_lyric)
        self.lyrics_preview_layout.addWidget(self.next_lyric)
        playback_layout.addWidget(self.playback_status)
        playback_layout.addStretch(1)
        playback_layout.addWidget(self.lyrics_preview_host)
        playback_layout.addStretch(1)

        transport_row = QHBoxLayout()
        self.play_button = QPushButton()
        self.stop_button = QPushButton()
        self.playback_slider = QSlider(Qt.Orientation.Horizontal)
        self.playback_slider.setObjectName("trackPlaybackSlider")
        self.playback_slider.setRange(0, max(1, round(track.duration_seconds * 1000)))
        self.playback_slider.setSingleStep(100)
        self.playback_slider.setPageStep(5_000)
        self.playback_slider.setTracking(True)
        self.playback_slider.setMinimumWidth(220)
        self.playback_slider.setMinimumHeight(30)
        self.playback_slider.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.playback_slider.setStyleSheet(
            "QSlider#trackPlaybackSlider::groove:horizontal {"
            "height: 8px; border-radius: 4px; background: #76859A; }"
            "QSlider#trackPlaybackSlider::sub-page:horizontal {"
            "border-radius: 4px; background: #1685D1; }"
            "QSlider#trackPlaybackSlider::handle:horizontal {"
            "width: 18px; margin: -6px 0; border-radius: 9px; "
            "background: #1685D1; border: 2px solid #FFFFFF; }"
        )
        self.playback_time = QLabel()
        self.playback_time.setObjectName("mutedLabel")
        self.playback_time.setMinimumWidth(78)
        self.playback_time.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        transport_row.addWidget(self.play_button)
        transport_row.addWidget(self.stop_button)
        transport_row.addStretch(1)
        transport_row.addWidget(self.playback_time)
        playback_layout.addLayout(transport_row)
        playback_layout.addWidget(self.playback_slider)

        volume_row = QHBoxLayout()
        self.volume_label = QLabel()
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(saved_volume)
        self.volume_value = QLabel(f"{saved_volume}%")
        self.volume_value.setObjectName("mutedLabel")
        self.volume_value.setMinimumWidth(42)
        self.volume_value.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        volume_row.addWidget(self.volume_label)
        volume_row.addWidget(self.volume_slider, 1)
        volume_row.addWidget(self.volume_value)
        playback_layout.addLayout(volume_row)

        self.cue_summary = QLabel()
        self.cue_summary.setObjectName("mutedLabel")
        lyrics_layout.addWidget(self.cue_summary)
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        lyrics_layout.addWidget(self.preview, 1)
        content_row = QHBoxLayout()
        content_row.setSpacing(10)
        content_row.addWidget(self.lyrics_group, 5)
        content_row.addWidget(self.playback_group, 4)
        root.addLayout(content_row, 1)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

        self.load_button.clicked.connect(self._load_lyrics)
        self.edit_lrc_button.clicked.connect(self._edit_in_lrc_generator)
        self.export_lrc_button.clicked.connect(self._export_current_lyrics_as_lrc)
        self.clear_button.clicked.connect(self._clear_lyrics)
        self.earlier_button.clicked.connect(lambda: self._nudge_timing(-0.1))
        self.later_button.clicked.connect(lambda: self._nudge_timing(0.1))
        self.reset_button.clicked.connect(lambda: self.timing_offset_spin.setValue(0.0))
        self.timing_offset_spin.valueChanged.connect(self._refresh_preview)
        self.play_button.clicked.connect(self._toggle_playback)
        self.stop_button.clicked.connect(self._stop_playback)
        self.volume_slider.valueChanged.connect(self._set_volume)
        self.playback_slider.sliderMoved.connect(self._playback_position_changed)
        self.playback_slider.sliderReleased.connect(self._seek_playback)
        self.media_player.positionChanged.connect(self._playback_position_changed)
        self.media_player.durationChanged.connect(self._playback_duration_changed)
        self.media_player.playbackStateChanged.connect(self._playback_state_changed)
        self.media_player.errorOccurred.connect(self._playback_error)
        translator.language_changed.connect(self.retranslate)
        self.retranslate()
        self._refresh_preview()
        self._playback_duration_changed(round(track.duration_seconds * 1000))

    def _nudge_timing(self, delta: float) -> None:
        self.timing_offset_spin.setValue(self.timing_offset_spin.value() + delta)

    def _load_lyrics(self) -> None:
        korean = self.translator.language.value == "ko"
        path, _ = QFileDialog.getOpenFileName(
            self,
            "가사/자막 불러오기" if korean else "Load lyrics/subtitles",
            self.selected_lyrics_path,
            "Lyrics / subtitles (*.lrc *.srt *.vtt)",
        )
        if not path:
            return
        try:
            cues = LyricsService.load(path)
        except LyricsError as error:
            self.preview.setPlainText(str(error))
            return
        self.selected_lyrics_path = str(Path(path).resolve())
        self.selected_lyrics = [cue.copy() for cue in cues]
        self._refresh_preview()

    def _clear_lyrics(self) -> None:
        self.selected_lyrics_path = ""
        self.selected_lyrics = []
        self._refresh_preview()

    def _edit_in_lrc_generator(self) -> None:
        """Round-trip this track's timed lyrics through the LRC generator."""
        self.media_player.pause()
        dialog = LrcGeneratorDialog(
            [],
            self.translator,
            self,
            track_edit_mode=True,
            initial_audio_path=self.track.file_path,
            initial_cues=self.selected_lyrics,
            initial_title=self.title_edit.text().strip(),
            initial_artist=self.artist_edit.text().strip(),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        edited_cues = dialog.timed_cues()
        if edited_cues:
            self.selected_lyrics = [cue.copy() for cue in edited_cues]
        if dialog.saved_paths:
            self.selected_lyrics_path = str(dialog.saved_paths[-1].resolve())
        self._refresh_preview()

    def _export_current_lyrics_as_lrc(self) -> None:
        """Convert the attached/embedded cues to LRC with this track's offset applied."""
        korean = self.translator.language.value == "ko"
        if not self.selected_lyrics:
            QMessageBox.warning(
                self,
                "내보낼 가사 없음" if korean else "No lyrics to export",
                "먼저 시간 정보가 있는 가사를 등록하세요."
                if korean else "Attach timed lyrics before exporting an LRC file.",
            )
            return
        source = Path(self.selected_lyrics_path) if self.selected_lyrics_path else Path(self.track.file_path)
        default_name = source.with_suffix(".lrc")
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "현재 가사를 LRC로 내보내기" if korean else "Export current lyrics as LRC",
            str(default_name),
            "LRC lyrics (*.lrc)",
        )
        if not selected:
            return
        offset = self.timing_offset_spin.value()
        adjusted_cues: list[dict[str, object]] = []
        for cue in self.selected_lyrics:
            start = max(0.0, float(cue.get("start", 0.0)) - offset)
            end = max(start, float(cue.get("end", start + 8.0)) - offset)
            adjusted_cues.append({
                "start": start,
                "end": end,
                "text": LyricsService.decode_line_breaks(cue.get("text", "")),
            })
        try:
            saved = LyricsService.save_lrc(
                selected,
                adjusted_cues,
                title=self.title_edit.text().strip(),
                artist=self.artist_edit.text().strip(),
            )
        except LyricsError as error:
            QMessageBox.critical(
                self,
                "LRC 내보내기 오류" if korean else "LRC export error",
                str(error),
            )
            return
        QMessageBox.information(
            self,
            "LRC 내보내기 완료" if korean else "LRC export complete",
            f"현재 가사를 LRC 파일로 저장했습니다.\n{saved}"
            if korean else f"The current lyrics were saved as an LRC file.\n{saved}",
        )

    def _toggle_playback(self) -> None:
        """Play or pause the selected track without leaving the settings form."""
        if not self._audio_available:
            return
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
        else:
            self.media_player.play()

    def _set_volume(self, value: int) -> None:
        """Apply and persist the volume shared with the full-video preview."""
        value = save_preview_volume(value)
        self.audio_output.setVolume(value / 100.0)
        self.volume_value.setText(f"{value}%")

    def _stop_playback(self) -> None:
        """Stop audio and return the synchronized preview to the track start."""
        self.media_player.stop()
        self.media_player.setPosition(0)
        self._playback_position_changed(0)

    def _seek_playback(self) -> None:
        """Seek audio to the position chosen on the preview slider."""
        if self._audio_available:
            self.media_player.setPosition(self.playback_slider.value())
        self._playback_position_changed(self.playback_slider.value())

    def _playback_position_changed(self, position_ms: int) -> None:
        if not self.playback_slider.isSliderDown():
            self.playback_slider.setValue(max(0, position_ms))
        total_ms = max(self.playback_slider.maximum(), round(self.track.duration_seconds * 1000))
        self.playback_time.setText(
            f"{self._clock(position_ms)} / {self._clock(total_ms)}"
        )
        self._update_live_lyrics(position_ms)

    def _playback_duration_changed(self, duration_ms: int) -> None:
        duration = max(1, duration_ms, round(self.track.duration_seconds * 1000))
        self.playback_slider.setRange(0, duration)
        self._playback_position_changed(self.media_player.position())

    def _playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        korean = self.translator.language.value == "ko"
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        if playing:
            self.play_button.setText("일시정지" if korean else "Pause")
        else:
            self.play_button.setText("재생" if korean else "Play")

    def _playback_error(
        self, _error: QMediaPlayer.Error, message: str = "",
    ) -> None:
        if not message:
            return
        korean = self.translator.language.value == "ko"
        self.playback_status.setText(
            f"오디오 재생 오류: {message}" if korean else f"Audio playback error: {message}"
        )

    def _update_live_lyrics(self, position_ms: int | None = None) -> None:
        """Display the cue synchronized to audio and the unsaved timing offset."""
        korean = self.translator.language.value == "ko"
        if not self.selected_lyrics:
            self.previous_lyric.clear()
            self.next_lyric.clear()
            self.current_lyric.setText(
                "시간 정보가 있는 가사를 불러오세요."
                if korean else "Load timed lyrics to preview them here."
            )
            return
        position = self.media_player.position() if position_ms is None else position_ms
        lyric_seconds = max(
            0.0, position / 1000.0 + self.timing_offset_spin.value()
        )
        cue_index = LyricsService.display_cue_index(self.selected_lyrics, lyric_seconds)
        if cue_index is None:
            return

        def cue_text(index: int) -> str:
            return LyricsService.decode_line_breaks(
                self.selected_lyrics[index].get("text", "")
            ).strip()

        self.previous_lyric.setText(cue_text(cue_index - 1) if cue_index > 0 else "")
        self.current_lyric.setText(cue_text(cue_index))
        self.next_lyric.setText(
            cue_text(cue_index + 1) if cue_index + 1 < len(self.selected_lyrics) else ""
        )

    @staticmethod
    def _clock(milliseconds: int) -> str:
        total_seconds = max(0, milliseconds // 1000)
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return (
            f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            if hours else f"{minutes:02d}:{seconds:02d}"
        )

    @staticmethod
    def _timestamp(seconds: float) -> str:
        milliseconds = max(0, round(seconds * 1000))
        minutes, remainder = divmod(milliseconds, 60_000)
        whole_seconds, fraction = divmod(remainder, 1000)
        return f"{minutes:02d}:{whole_seconds:02d}.{fraction:03d}"

    def _refresh_preview(self, _value: float = 0.0) -> None:
        korean = self.translator.language.value == "ko"
        offset = self.timing_offset_spin.value()
        self.lyrics_path.setText(
            self.selected_lyrics_path
            or ("연결된 가사 파일이 없습니다." if korean else "No lyric file attached.")
        )
        self.clear_button.setEnabled(bool(self.selected_lyrics_path or self.selected_lyrics))
        self.edit_lrc_button.setEnabled(self._audio_available or bool(self.selected_lyrics))
        self.export_lrc_button.setEnabled(bool(self.selected_lyrics))
        count = len(self.selected_lyrics)
        self.cue_summary.setText(
            f"{count}개 타임코드 · 보정 적용 미리보기"
            if korean else f"{count} timed cues · adjusted preview"
        )
        self._update_live_lyrics()
        if not self.selected_lyrics:
            self.preview.setPlainText(
                "시간 정보가 있는 가사를 불러오세요."
                if korean else "Load lyrics containing timing information."
            )
            return
        lines: list[str] = []
        for cue in self.selected_lyrics[:120]:
            # A positive offset advances lyrics, so their display timestamp is
            # cue time minus the offset.
            start = float(cue.get("start", 0.0)) - offset
            text = LyricsService.decode_line_breaks(
                cue.get("text", "")
            ).replace("\n", " / ").strip()
            lines.append(f"[{self._timestamp(start)}]  {text}")
        if count > 120:
            lines.append(f"… +{count - 120}")
        self.preview.setPlainText("\n".join(lines))

    def _accept(self) -> None:
        korean = self.translator.language.value == "ko"
        title = self.title_edit.text().strip()
        if not title:
            QMessageBox.warning(
                self,
                "곡 제목 필요" if korean else "Track title required",
                "곡 제목을 입력하세요." if korean else "Enter a title for this track.",
            )
            self.title_edit.setFocus()
            return
        self.selected_title = title
        self.selected_artist = self.artist_edit.text().strip()
        self.selected_album = self.album_edit.text().strip()
        self.selected_timing_offset = self.timing_offset_spin.value()
        self.accept()

    def done(self, result: int) -> None:
        """Never leave preview audio playing after the form closes."""
        self.media_player.stop()
        super().done(result)

    def retranslate(self) -> None:
        korean = self.translator.language.value == "ko"
        self.setWindowTitle("곡/가사 설정" if korean else "Track and lyrics settings")
        self.info_group.setTitle("곡 정보" if korean else "Track information")
        info_names = (
            ("제목", "아티스트", "앨범", "파일", "재생 시간")
            if korean else ("Title", "Artist", "Album", "File", "Duration")
        )
        for label, name in zip(self.info_name_labels, info_names):
            label.setText(name)
        metadata_tip = (
            "프로젝트에 저장할 곡 정보를 직접 수정할 수 있습니다. 원본 오디오 파일의 태그는 변경되지 않습니다."
            if korean else
            "Edit the track information stored in this project. The source audio file tags are not changed."
        )
        for field in self.metadata_edits:
            field.setToolTip(metadata_tip)
        self.lyrics_group.setTitle("가사 / 자막" if korean else "Lyrics / subtitles")
        self.playback_group.setTitle("노래와 가사 미리보기" if korean else "Audio and lyrics preview")
        self.load_button.setText("파일 불러오기…" if korean else "Load file…")
        self.edit_lrc_button.setText(
            "LRC 생성기로 편집…" if korean else "Edit in LRC Generator…"
        )
        self.edit_lrc_button.setToolTip(
            "현재 곡의 오디오와 등록된 가사·타이밍을 LRC 생성기에서 편집합니다."
            if korean else
            "Edit this track's audio, lyrics, and timing in the LRC generator."
        )
        self.export_lrc_button.setText(
            "LRC로 내보내기…" if korean else "Export as LRC…"
        )
        self.export_lrc_button.setToolTip(
            "현재 곡의 타이밍 보정을 적용하고 여러 줄 가사는 문자 \\n으로 저장합니다."
            if korean else
            "Applies this track's timing offset and stores multi-line lyrics using the literal \\n characters."
        )
        self.clear_button.setText("연결 해제" if korean else "Detach")
        self.timing_label.setText("곡별 타이밍 보정" if korean else "Per-track timing offset")
        self.reset_button.setText("초기화" if korean else "Reset")
        self.play_button.setText("재생" if korean else "Play")
        self.stop_button.setText("정지" if korean else "Stop")
        self.volume_label.setText("볼륨" if korean else "Volume")
        self.play_button.setEnabled(self._audio_available)
        self.stop_button.setEnabled(self._audio_available)
        self.playback_slider.setEnabled(self._audio_available)
        if self._audio_available:
            self.playback_status.setText(
                "재생 위치에 맞춰 가사를 표시합니다."
                if korean else "Lyrics follow the current playback position."
            )
        else:
            self.playback_status.setText(
                "음원 파일을 찾을 수 없어 재생할 수 없습니다."
                if korean else "The audio file is missing and cannot be played."
            )
        self._playback_state_changed(self.media_player.playbackState())
        self.earlier_button.setToolTip(
            "가사를 0.1초 늦게 표시" if korean else "Show lyrics 0.1 seconds later"
        )
        self.later_button.setToolTip(
            "가사를 0.1초 빠르게 표시" if korean else "Show lyrics 0.1 seconds earlier"
        )
        self.timing_help.setText(
            "양수 값은 가사를 더 빠르게, 음수 값은 더 늦게 표시합니다. 이 값은 현재 곡에만 적용되며 가사 요소의 공통 보정값과 합산됩니다."
            if korean else
            "Positive values show lyrics earlier; negative values show them later. This applies only to the current track and is added to the Lyrics source's global offset."
        )
        save_button = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        if save_button is not None:
            save_button.setText("적용" if korean else "Apply")
        cancel_button = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_button is not None:
            cancel_button.setText("취소" if korean else "Cancel")
        self._refresh_preview()
