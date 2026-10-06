"""Transport detection and live notices for timing-sensitive playback."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal, QUrl
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication, QSlider, QWidget

from app.services.audio_output_device import _windows_device_ids, is_bluetooth_output
from app.utils.i18n import Translator
from app.widgets.audio_sync_warning import AudioSyncWarning
from app.services.system_audio_state import SystemAudioState


def device(identifier="endpoint", description="Headphones", null=False):
    return SimpleNamespace(id=lambda: identifier.encode(), description=lambda: description, isNull=lambda: null)


class Output(QObject):
    deviceChanged = Signal()
    volumeChanged = Signal(float)
    mutedChanged = Signal(bool)

    def __init__(self, current):
        super().__init__()
        self.current = current
        self.level = 1.0
        self.muted = False

    def device(self):
        return self.current

    def volume(self):
        return self.level

    def isMuted(self):
        return self.muted


class Player(QObject):
    errorChanged = Signal()
    sourceChanged = Signal(QUrl)
    mediaStatusChanged = Signal(object)
    hasAudioChanged = Signal(bool)
    audioOutputChanged = Signal()

    def __init__(self, output):
        super().__init__()
        self.output = output
        self.failure = QMediaPlayer.Error.NoError
        self.status = QMediaPlayer.MediaStatus.NoMedia
        self.url = QUrl()
        self.audio = False

    def audioOutput(self):
        return self.output

    def error(self):
        return self.failure

    def errorString(self):
        return "Cannot open file" if self.failure != QMediaPlayer.Error.NoError else ""

    def mediaStatus(self):
        return self.status

    def source(self):
        return self.url

    def hasAudio(self):
        return self.audio


class BluetoothOutputTests(unittest.TestCase):
    def test_native_walk_reaches_transport_and_stops_on_bluetooth_evidence(self):
        identifiers = {1: "SWD\\MMDEVAPI\\endpoint", 2: "BTHENUM\\headphones"}

        def locate(node, instance, flags):
            self.assertEqual(instance.value, "SWD\\MMDEVAPI\\endpoint")
            node._obj.value = 1
            return 0

        def read(node, buffer, length, flags):
            buffer.value = identifiers[node.value]
            return 0

        def parent(node, current, flags):
            node._obj.value = 2
            return 0

        config = SimpleNamespace(CM_Locate_DevNodeW=Mock(side_effect=locate),
                                 CM_Get_Device_IDW=Mock(side_effect=read),
                                 CM_Get_Parent=Mock(side_effect=parent))
        with patch("ctypes.WinDLL", return_value=config, create=True):
            self.assertEqual(_windows_device_ids("endpoint"), tuple(identifiers.values()))
        self.assertEqual(config.CM_Get_Parent.call_count, 1)

    def test_native_api_failure_is_unknown_and_never_blocks_editor(self):
        with patch("ctypes.WinDLL", side_effect=OSError("unavailable"), create=True):
            self.assertIsNone(_windows_device_ids("endpoint"))
        config = SimpleNamespace(CM_Locate_DevNodeW=Mock(return_value=13),
                                 CM_Get_Device_IDW=Mock(), CM_Get_Parent=Mock())
        with patch("ctypes.WinDLL", return_value=config, create=True):
            self.assertIsNone(_windows_device_ids("endpoint"))
        config.CM_Get_Device_IDW.assert_not_called()

    def test_windows_transport_detects_renamed_devices_and_both_profiles(self):
        for bus in ("BTHENUM", "BTHHFENUM", "BTHLEDEVICE", "BTHLE"):
            with self.subTest(bus=bus), patch("app.services.audio_output_device.sys.platform", "win32"), \
                    patch("app.services.audio_output_device._windows_device_ids", return_value=(
                        "SWD\\MMDEVAPI\\endpoint", bus.lower() + "\\headphones", "HTREE\\ROOT\\0")):
                self.assertTrue(is_bluetooth_output(device(description="My headphones")))

    def test_wired_transport_overrides_misleading_custom_name(self):
        with patch("app.services.audio_output_device.sys.platform", "win32"), \
                patch("app.services.audio_output_device._windows_device_ids", return_value=(
                    "SWD\\MMDEVAPI\\endpoint", "USB\\speaker", "HTREE\\ROOT\\0")):
            self.assertFalse(is_bluetooth_output(device(description="Bluetooth headphones")))

    def test_unknown_transport_uses_only_explicit_hints(self):
        with patch("app.services.audio_output_device.sys.platform", "win32"), \
                patch("app.services.audio_output_device._windows_device_ids", return_value=None):
            self.assertTrue(is_bluetooth_output(device(description="블루투스 헤드폰")))
            for name in ("AirPods", "Sony WH-1000XM5", "USB Headphones", "Wireless headset"):
                self.assertFalse(is_bluetooth_output(device(description=name)))

    def test_linux_bluez_identifier_and_missing_device(self):
        with patch("app.services.audio_output_device.sys.platform", "linux"):
            self.assertTrue(is_bluetooth_output(device("bluez_output.00_11.a2dp-sink")))
            self.assertFalse(is_bluetooth_output(device(null=True, description="Bluetooth")))


class AudioSyncWarningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.bluetooth = device(description="Bluetooth Headphones")
        self.wired = device(description="USB Speakers")
        detector = patch("app.widgets.audio_sync_warning.is_bluetooth_output",
                         side_effect=lambda current: current is self.bluetooth)
        self.detect = detector.start()
        self.addCleanup(detector.stop)
        default = patch("app.widgets.audio_sync_warning.QMediaDevices.defaultAudioOutput", return_value=self.wired)
        self.default = default.start()
        self.addCleanup(default.stop)
        outputs = patch("app.widgets.audio_sync_warning.QMediaDevices.audioOutputs",
                        return_value=[self.wired, self.bluetooth])
        self.outputs = outputs.start()
        self.addCleanup(outputs.stop)
        system = patch("app.widgets.audio_sync_warning.system_audio_state", return_value=None)
        self.system = system.start()
        self.addCleanup(system.stop)

    def warning(self, **kwargs):
        warning = AudioSyncWarning(**kwargs)
        self.addCleanup(warning.deleteLater)
        return warning

    def test_default_output_changes_before_lazy_player_creation(self):
        warning = self.warning()
        self.assertTrue(warning.isHidden())
        self.default.return_value = self.bluetooth
        warning._devices.audioOutputsChanged.emit()
        self.assertFalse(warning.isHidden())
        self.assertIn("블루투스", warning.text())
        self.default.return_value = self.wired
        warning._devices.audioOutputsChanged.emit()
        self.assertTrue(warning.isHidden())

    def test_selected_output_is_used_instead_of_system_default(self):
        output = Output(self.bluetooth)
        warning = self.warning(audio_output=output)
        self.assertFalse(warning.isHidden())
        self.assertEqual(warning.toolTip(), "Bluetooth Headphones")
        output.current = self.wired
        output.deviceChanged.emit()
        self.assertTrue(warning.isHidden())

    def test_null_output_uses_default_and_missing_default_warns(self):
        self.default.return_value = self.bluetooth
        warning = self.warning(audio_output=Output(device(null=True)))
        self.assertFalse(warning.isHidden())
        self.default.return_value = device(null=True)
        warning._devices.audioOutputsChanged.emit()
        self.assertFalse(warning.isHidden())
        self.assertEqual(warning.property("reason"), "no_device")

    def test_disconnected_selected_device_warns_until_reconnected(self):
        selected = device("disconnected", "Headphones")
        warning = self.warning(audio_output=Output(selected))
        self.assertEqual(warning.property("reason"), "no_device")
        self.outputs.return_value.append(selected)
        warning._devices.audioOutputsChanged.emit()
        self.assertTrue(warning.isHidden())

    def test_mute_and_zero_volume_update_immediately_and_clear_on_recovery(self):
        output = Output(self.wired)
        warning = self.warning(audio_output=output)
        output.level = 0.0
        output.volumeChanged.emit(0.0)
        self.assertEqual(warning.property("reason"), "zero_volume")
        output.muted = True
        output.mutedChanged.emit(True)
        self.assertEqual(warning.property("reason"), "muted")
        output.muted = False
        output.mutedChanged.emit(False)
        self.assertEqual(warning.property("reason"), "zero_volume")
        output.level = 0.7
        output.volumeChanged.emit(0.7)
        self.assertTrue(warning.isHidden())

    def test_lazy_editor_volume_warns_even_before_player_exists(self):
        slider = QSlider()
        slider.setRange(0, 100)
        warning = self.warning(volume_slider=slider)
        self.assertEqual(warning.property("reason"), "zero_volume")
        slider.setValue(60)
        self.assertTrue(warning.isHidden())

    def test_system_unavailable_mute_zero_and_app_mixer(self):
        warning = self.warning(audio_output=Output(self.bluetooth))
        for state, reason in ((SystemAudioState(available=False), "no_device"),
                              (SystemAudioState(muted=True), "system_muted"),
                              (SystemAudioState(volume=0.0), "system_zero_volume"),
                              (SystemAudioState(mixer_silent=True), "mixer_silent")):
            with self.subTest(reason=reason):
                self.system.return_value = state
                warning.refresh()
                self.assertEqual(warning.property("reason"), reason)
        self.system.return_value = SystemAudioState()
        warning.refresh()
        self.assertEqual(warning.property("reason"), "bluetooth")

    def test_system_poll_skips_hidden_editor_but_refreshes_healthy_visible_editor(self):
        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        warning = AudioSyncWarning(parent, audio_output=Output(self.wired))
        self.system.reset_mock()
        warning._poll_system()
        self.system.assert_not_called()
        parent.show()
        self.assertTrue(warning.isHidden())
        self.system.return_value = SystemAudioState(muted=True)
        warning._poll_system()
        self.assertEqual(warning.property("reason"), "system_muted")
        self.assertFalse(warning.isHidden())
        self.system.return_value = SystemAudioState()
        warning._poll_system()
        self.assertTrue(warning.isHidden())

    def test_player_error_and_invalid_media_clear_when_source_recovers(self):
        player = Player(Output(self.wired))
        warning = self.warning(media_player=player)
        player.failure = QMediaPlayer.Error.ResourceError
        player.errorChanged.emit()
        self.assertEqual(warning.property("reason"), "playback_error")
        self.assertEqual(warning.toolTip(), "Cannot open file")
        player.failure = QMediaPlayer.Error.NoError
        player.status = QMediaPlayer.MediaStatus.InvalidMedia
        player.mediaStatusChanged.emit(player.status)
        self.assertEqual(warning.property("reason"), "playback_error")
        player.status = QMediaPlayer.MediaStatus.LoadingMedia
        player.url = QUrl.fromLocalFile("working.wav")
        player.sourceChanged.emit(player.url)
        self.assertTrue(warning.isHidden())

    def test_file_without_audio_warns_only_after_loading_finishes(self):
        player = Player(Output(self.wired))
        warning = self.warning(media_player=player)
        player.url = QUrl.fromLocalFile("video.mp4")
        player.status = QMediaPlayer.MediaStatus.LoadingMedia
        player.mediaStatusChanged.emit(player.status)
        self.assertTrue(warning.isHidden())
        player.status = QMediaPlayer.MediaStatus.LoadedMedia
        player.mediaStatusChanged.emit(player.status)
        self.assertEqual(warning.property("reason"), "no_audio")
        player.audio = True
        player.hasAudioChanged.emit(True)
        self.assertTrue(warning.isHidden())
        # A loaded audio track remains valid even if its samples are silent.
        self.assertEqual(warning.property("reason"), "")

    def test_rebinding_player_disconnects_old_player_and_follows_new_output(self):
        first, second = Player(Output(self.bluetooth)), Player(Output(self.wired))
        warning = self.warning(media_player=first)
        warning.bind_player(second)
        self.system.reset_mock()
        first.errorChanged.emit()
        self.system.assert_not_called()
        second.output = Output(self.bluetooth)
        second.audioOutputChanged.emit()
        self.assertEqual(warning.property("reason"), "bluetooth")

    def test_player_without_output_warns_and_recovers_on_output_connection(self):
        player = Player(None)
        warning = self.warning(media_player=player)
        self.assertEqual(warning.property("reason"), "no_player_output")
        player.output = Output(self.wired)
        player.audioOutputChanged.emit()
        self.assertTrue(warning.isHidden())

    def test_lazy_binding_disconnects_old_output(self):
        first, second = Output(self.bluetooth), Output(self.wired)
        warning = self.warning(audio_output=first)
        warning.bind_output(second)
        self.detect.reset_mock()
        first.deviceChanged.emit()
        self.detect.assert_not_called()
        second.current = self.bluetooth
        second.deviceChanged.emit()
        self.assertFalse(warning.isHidden())

    def test_language_changes_update_visible_notice(self):
        translator = Translator()
        translator.set_language("ko")
        warning = self.warning(audio_output=Output(self.bluetooth), translator=translator)
        self.assertIn("유선", warning.text())
        translator.set_language("en")
        self.assertIn("Bluetooth", warning.text())
        self.assertIn("wired", warning.text())
        self.assertFalse(warning.isHidden())


if __name__ == "__main__":
    unittest.main()
