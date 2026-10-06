"""Non-blocking playback and Bluetooth notices for timing-sensitive UIs."""

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtMultimedia import QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import QLabel, QSizePolicy

from app.services.audio_output_device import is_bluetooth_output
from app.services.system_audio_state import system_audio_state


class AudioSyncWarning(QLabel):
    def __init__(self, parent=None, *, audio_output=None, media_player=None,
                 volume_slider=None, translator=None, korean=True):
        super().__init__(parent)
        self.setObjectName("warningLabel")
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self.setContentsMargins(8, 4, 8, 4)
        policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self._translator = translator
        self._korean = korean
        self._audio_output = None
        self._player = None
        self._volume_slider = volume_slider
        self._bluetooth_identity = None
        self._bluetooth = False
        self._devices = QMediaDevices(self)
        self._devices.audioOutputsChanged.connect(self._devices_changed)
        self._system_timer = QTimer(self)
        self._system_timer.setInterval(1000)
        self._system_timer.timeout.connect(self._poll_system)
        if sys.platform == "win32":
            self._system_timer.start()
        if volume_slider is not None:
            volume_slider.valueChanged.connect(self.refresh)
        if translator is not None:
            translator.language_changed.connect(self.refresh)
        self.bind_output(audio_output)
        self.bind_player(media_player)

    def _devices_changed(self):
        self._bluetooth_identity = None
        self.refresh()

    def _poll_system(self):
        # The notice itself is hidden when healthy. Monitor the containing
        # window instead, and avoid native queries for closed/hidden editors.
        if self.window().isVisible():
            self.refresh()

    def bind_output(self, output):
        """AutoMix creates its player lazily; follow its output once it exists."""
        if self._audio_output is not None:
            for signal in (self._audio_output.deviceChanged, self._audio_output.volumeChanged,
                           self._audio_output.mutedChanged):
                signal.disconnect(self.refresh)
        self._audio_output = output
        if output is not None:
            for signal in (output.deviceChanged, output.volumeChanged, output.mutedChanged):
                signal.connect(self.refresh)
        self.refresh()

    def bind_player(self, player):
        signals = ("errorChanged", "sourceChanged", "mediaStatusChanged", "hasAudioChanged")
        if self._player is not None:
            for name in signals:
                getattr(self._player, name).disconnect(self.refresh)
            self._player.audioOutputChanged.disconnect(self._player_output_changed)
        self._player = player
        if player is not None:
            for name in signals:
                getattr(player, name).connect(self.refresh)
            player.audioOutputChanged.connect(self._player_output_changed)
            self._player_output_changed()
        else:
            self.refresh()

    def _player_output_changed(self):
        self.bind_output(self._player.audioOutput())

    def refresh(self, *_):
        device = self._audio_output.device() if self._audio_output is not None else None
        if device is None or device.isNull():
            device = QMediaDevices.defaultAudioOutput()
        reason, korean_text, english_text, detail = self._notice(device)
        korean = self._translator.is_korean if self._translator is not None else self._korean
        message = korean_text if korean else english_text
        self.setProperty("reason", reason)
        text = "⚠ " + message if message else ""
        # Polling should not trigger layout/repaint work for an unchanged state.
        if self.text() != text:
            self.setText(text)
        self.setToolTip(detail)
        self.setVisible(bool(reason))

    def _notice(self, device):
        if device.isNull() or not any(output.id() == device.id() for output in QMediaDevices.audioOutputs()):
            return ("no_device", "사용 가능한 오디오 출력 장치가 없습니다. 이어폰·스피커 연결과 시스템 출력 설정을 확인하세요.",
                    "No audio output device is available. Check your headphones, speakers and system output settings.", "")
        detail = device.description()
        if self._player is not None and self._audio_output is None:
            return ("no_player_output", "재생기에 오디오 출력이 연결되어 있지 않습니다. 미리보기를 다시 열어주세요.",
                    "The player has no audio output connected. Reopen the preview.", detail)
        system = system_audio_state(device)
        if system is not None and not system.available:
            return ("no_device", "오디오 출력 장치가 연결 해제되었거나 사용할 수 없습니다. 시스템 출력 설정을 확인하세요.",
                    "The audio output device is disconnected or unavailable. Check your system output settings.", detail)
        if self._audio_output is not None and self._audio_output.isMuted():
            return ("muted", "미리보기 오디오가 음소거되어 소리가 나지 않습니다. 음소거를 해제하세요.",
                    "Preview audio is muted. Unmute it to hear playback.", detail)
        volume = self._audio_output.volume() if self._audio_output is not None else (
            self._volume_slider.value() / 100.0 if self._volume_slider is not None else 1.0)
        if volume <= 0.0:
            return ("zero_volume", "미리보기 볼륨이 0이라 소리가 나지 않습니다. 볼륨을 올려주세요.",
                    "Preview volume is 0. Turn up the volume to hear playback.", detail)
        if system is not None:
            if system.muted:
                return ("system_muted", "Windows 오디오 출력이 음소거되어 소리가 나지 않습니다. 시스템 음소거를 해제하세요.",
                        "Windows audio output is muted. Unmute it in your system sound settings.", detail)
            if system.volume <= 0.0:
                return ("system_zero_volume", "Windows 출력 볼륨이 0이라 소리가 나지 않습니다. 시스템 볼륨을 올려주세요.",
                        "Windows output volume is 0. Turn up your system volume.", detail)
            if system.mixer_silent:
                return ("mixer_silent", "Windows 볼륨 믹서에서 이 앱의 소리가 꺼져 있습니다. 앱 음소거와 볼륨을 확인하세요.",
                        "This app is silenced in the Windows volume mixer. Check its mute and volume settings.", detail)
        if self._player is not None:
            player = self._player
            if player.error() != QMediaPlayer.Error.NoError or player.mediaStatus() == QMediaPlayer.MediaStatus.InvalidMedia:
                error = player.errorString()
                return ("playback_error", "오디오를 재생할 수 없습니다. 파일과 오디오 장치를 확인하세요.",
                        "Audio cannot be played. Check the file and audio device.", error or detail)
            if (player.mediaStatus() in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia,
                                        QMediaPlayer.MediaStatus.EndOfMedia)
                    and not player.source().isEmpty() and not player.hasAudio()):
                return ("no_audio", "현재 파일에 재생할 오디오가 없습니다. 오디오가 포함된 파일을 선택하세요.",
                        "The current file has no audio. Choose a file containing an audio track.", detail)
        identity = bytes(device.id()), device.description()
        if identity != self._bluetooth_identity:
            self._bluetooth = is_bluetooth_output(device)
            self._bluetooth_identity = identity
        if self._bluetooth:
            return ("bluetooth", "블루투스 오디오 출력: 소리가 화면보다 늦게 들릴 수 있습니다. 정확한 싱크 작업에는 유선 이어폰·헤드폰 또는 스피커를 사용하세요.",
                    "Bluetooth audio output: sound may lag behind the screen. Use wired headphones or speakers for precise timing.", detail)
        return "", "", "", ""
