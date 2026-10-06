"""Read-only native inspection, unknown states and process mixer isolation."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services.system_audio_state import (
    SystemAudioState, _mixer_silent, _read_windows_state, system_audio_state,
)


class SystemAudioStateTests(unittest.TestCase):
    def setUp(self):
        self.device = SimpleNamespace(isNull=lambda: False, id=lambda: b"endpoint")

    def test_unsupported_platform_and_null_device_never_call_native_api(self):
        with patch("app.services.system_audio_state._read_windows_state") as native:
            with patch("app.services.system_audio_state.sys.platform", "linux"):
                self.assertIsNone(system_audio_state(self.device))
            with patch("app.services.system_audio_state.sys.platform", "win32"):
                self.assertIsNone(system_audio_state(SimpleNamespace(isNull=lambda: True)))
            native.assert_not_called()

    def test_api_failure_remains_unknown_instead_of_claiming_mute(self):
        with patch("app.services.system_audio_state.sys.platform", "win32"), \
                patch("app.services.system_audio_state._read_windows_state", side_effect=OSError("device disappeared")):
            self.assertIsNone(system_audio_state(self.device))

    def test_selected_endpoint_status_is_forwarded(self):
        state = SystemAudioState(volume=0.0, muted=True)
        with patch("app.services.system_audio_state.sys.platform", "win32"), \
                patch("app.services.system_audio_state._read_windows_state", return_value=state) as native:
            self.assertIs(system_audio_state(self.device), state)
            native.assert_called_once_with("endpoint")

    def test_com_apartment_is_balanced_even_if_enumerator_creation_fails(self):
        for initialize_result, uninitializes in ((0, 1), (1, 1), (-2147417850, 0)):
            with self.subTest(initialize_result=initialize_result):
                ole = SimpleNamespace(CoInitializeEx=Mock(return_value=initialize_result),
                                      CoUninitialize=Mock(), CoCreateInstance=Mock(return_value=-1))
                with patch("ctypes.WinDLL", return_value=ole, create=True), self.assertRaises(OSError):
                    _read_windows_state("endpoint")
                self.assertEqual(ole.CoUninitialize.call_count, uninitializes)

    def test_failed_com_initialization_does_not_uninitialize_someone_elses_apartment(self):
        ole = SimpleNamespace(CoInitializeEx=Mock(return_value=-1),
                              CoUninitialize=Mock(), CoCreateInstance=Mock())
        with patch("ctypes.WinDLL", return_value=ole, create=True), self.assertRaises(OSError):
            _read_windows_state("endpoint")
        ole.CoUninitialize.assert_not_called()
        ole.CoCreateInstance.assert_not_called()

    def mixer(self, sessions):
        """Exercise session selection with native API replies, without changing OS volume."""
        objects = {}
        released = []

        class Interface:
            def __init__(self, value=None):
                self.value = value

            def __enter__(self):
                return self

            def __exit__(self, *_):
                released.append(self)

            def activate(self, _iid):
                return manager

            def query(self, _iid):
                return Interface(self.value)

            def call(self, index, _types, *args):
                if self is manager:
                    objects[1] = enumerator
                    args[0]._obj.value = 1
                elif self is enumerator:
                    if index == 3:
                        args[0]._obj.value = len(sessions)
                    else:
                        pointer = args[0] + 2
                        objects[pointer] = Interface(sessions[args[0]])
                        args[1]._obj.value = pointer
                else:
                    pid, active, level, muted = self.value
                    args[0]._obj.value = {14: pid, 3: active, 4: level, 6: muted}[index]

        endpoint, manager, enumerator = Interface(), Interface(), Interface()
        with patch("app.services.system_audio_state._Interface", side_effect=lambda pointer: objects[pointer.value]):
            result = _mixer_silent(endpoint)
        self.assertIn(manager, released)
        self.assertIn(enumerator, released)
        return result

    def test_mixer_only_considers_this_process_active_sessions(self):
        current = os.getpid()
        self.assertIsNone(self.mixer([]))
        self.assertIsNone(self.mixer([(current + 1, 1, 0.0, True), (current, 0, 0.0, True)]))
        self.assertTrue(self.mixer([(current, 1, 0.0, False), (current, 1, 0.5, True)]))
        self.assertFalse(self.mixer([(current, 1, 0.0, True), (current, 1, 0.5, False)]))

    def test_mixer_inspection_failure_is_unknown(self):
        endpoint = Mock()
        endpoint.activate.side_effect = OSError("unsupported interface")
        self.assertIsNone(_mixer_silent(endpoint))


if __name__ == "__main__":
    unittest.main()
