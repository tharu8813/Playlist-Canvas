"""Read Windows endpoint and application mixer state without changing volume.

Core Audio interfaces and their vtable order are defined in the Windows SDK
(mmdeviceapi.h, endpointvolume.h, audiopolicy.h and audioclient.h).
Unavailable inspection returns None; it is never evidence of silence.
"""

from __future__ import annotations

import ctypes
from contextlib import ExitStack
from dataclasses import dataclass
import os
import sys
from uuid import UUID


@dataclass(frozen=True)
class SystemAudioState:
    available: bool = True
    volume: float = 1.0
    muted: bool = False
    mixer_silent: bool | None = None


class _GUID(ctypes.Structure):
    _fields_ = [("data1", ctypes.c_uint32), ("data2", ctypes.c_uint16),
                ("data3", ctypes.c_uint16), ("data4", ctypes.c_ubyte * 8)]


def _guid(value):
    return _GUID.from_buffer_copy(UUID(value).bytes_le)


_ENUMERATOR_CLASS = "bcde0395-e52f-467c-8e3d-c4579291692e"
_ENUMERATOR = "a95664d2-9614-4f35-a746-de8db63617e6"
_ENDPOINT_VOLUME = "5cdf2c82-841e-4546-9722-0cf74078229a"
_SESSION_MANAGER = "77aa99a0-1bd6-484f-8bc7-2c654c9a9b6f"
_SESSION_CONTROL = "bfb7ff88-7239-4fc9-8fa2-07c950be9c6d"
_SESSION_VOLUME = "87ce5498-68d6-44e5-9215-6da47ef883d8"
_PTR = ctypes.c_void_p
_OUT = ctypes.POINTER(_PTR)


def _check(result):
    if result < 0:
        raise OSError(f"Core Audio returned HRESULT 0x{result & 0xffffffff:08x}")


class _Interface:
    def __init__(self, pointer):
        if not pointer.value:
            raise OSError("Core Audio returned an empty interface")
        self.pointer = pointer

    def invoke(self, index, argtypes=(), *args):
        table = ctypes.cast(self.pointer, ctypes.POINTER(ctypes.POINTER(_PTR))).contents
        function = ctypes.WINFUNCTYPE(ctypes.c_long, _PTR, *argtypes)(table[index])
        return function(self.pointer, *args)

    def call(self, index, argtypes=(), *args):
        _check(self.invoke(index, argtypes, *args))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.invoke(2)  # IUnknown::Release, on the same thread that acquired it.

    def query(self, iid):
        result = _PTR()
        self.call(0, (ctypes.POINTER(_GUID), _OUT), ctypes.byref(_guid(iid)), ctypes.byref(result))
        return _Interface(result)

    def activate(self, iid):
        result = _PTR()
        self.call(3, (ctypes.POINTER(_GUID), ctypes.c_ulong, _PTR, _OUT),
                  ctypes.byref(_guid(iid)), 23, None, ctypes.byref(result))
        return _Interface(result)


def _mixer_silent(endpoint) -> bool | None:
    """Only report silence if every active session of this process is muted/zero.

    Other apps, stopped sessions and silence within the music are irrelevant.
    With multiple players an audible session prevents a blanket silent claim.
    """
    try:
        with ExitStack() as stack:
            manager = stack.enter_context(endpoint.activate(_SESSION_MANAGER))
            pointer = _PTR()
            manager.call(5, (_OUT,), ctypes.byref(pointer))  # GetSessionEnumerator
            sessions = stack.enter_context(_Interface(pointer))
            count = ctypes.c_int()
            sessions.call(3, (ctypes.POINTER(ctypes.c_int),), ctypes.byref(count))
            if count.value > 128:
                return None
            silent = []
            for index in range(count.value):
                with ExitStack() as session_stack:
                    pointer = _PTR()
                    sessions.call(4, (ctypes.c_int, _OUT), index, ctypes.byref(pointer))
                    session = session_stack.enter_context(_Interface(pointer))
                    control = session_stack.enter_context(session.query(_SESSION_CONTROL))
                    process, state = ctypes.c_ulong(), ctypes.c_int()
                    control.call(14, (ctypes.POINTER(ctypes.c_ulong),), ctypes.byref(process))
                    if process.value != os.getpid():
                        continue
                    control.call(3, (ctypes.POINTER(ctypes.c_int),), ctypes.byref(state))
                    if state.value != 1:  # AudioSessionStateActive
                        continue
                    volume = session_stack.enter_context(session.query(_SESSION_VOLUME))
                    level, muted = ctypes.c_float(), ctypes.c_int()
                    volume.call(4, (ctypes.POINTER(ctypes.c_float),), ctypes.byref(level))
                    volume.call(6, (ctypes.POINTER(ctypes.c_int),), ctypes.byref(muted))
                    silent.append(bool(muted.value) or level.value <= 0.0)
            return all(silent) if silent else None
    except OSError:
        return None


def _read_windows_state(endpoint_id: str) -> SystemAudioState:
    ole = ctypes.WinDLL("ole32")
    ole.CoInitializeEx.argtypes = [_PTR, ctypes.c_ulong]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    ole.CoCreateInstance.argtypes = [ctypes.POINTER(_GUID), _PTR, ctypes.c_ulong,
                                    ctypes.POINTER(_GUID), _OUT]
    ole.CoCreateInstance.restype = ctypes.c_long
    initialized = ole.CoInitializeEx(None, 0)  # Prefer MTA for session inspection.
    if initialized != -2147417850:  # RPC_E_CHANGED_MODE: Qt already owns an STA.
        _check(initialized)
    try:
        with ExitStack() as stack:
            pointer = _PTR()
            _check(ole.CoCreateInstance(ctypes.byref(_guid(_ENUMERATOR_CLASS)), None, 23,
                                       ctypes.byref(_guid(_ENUMERATOR)), ctypes.byref(pointer)))
            enumerator = stack.enter_context(_Interface(pointer))
            pointer = _PTR()
            enumerator.call(5, (ctypes.c_wchar_p, _OUT), endpoint_id, ctypes.byref(pointer))
            endpoint = stack.enter_context(_Interface(pointer))
            state = ctypes.c_ulong()
            endpoint.call(6, (ctypes.POINTER(ctypes.c_ulong),), ctypes.byref(state))
            if state.value != 1:  # DEVICE_STATE_ACTIVE
                return SystemAudioState(available=False)
            volume = stack.enter_context(endpoint.activate(_ENDPOINT_VOLUME))
            level, muted = ctypes.c_float(), ctypes.c_int()
            volume.call(9, (ctypes.POINTER(ctypes.c_float),), ctypes.byref(level))
            volume.call(15, (ctypes.POINTER(ctypes.c_int),), ctypes.byref(muted))
            mixer = None if muted.value or level.value <= 0.0 else _mixer_silent(endpoint)
            return SystemAudioState(volume=level.value, muted=bool(muted.value), mixer_silent=mixer)
    finally:
        if initialized >= 0:
            ole.CoUninitialize()


def system_audio_state(device) -> SystemAudioState | None:
    if sys.platform != "win32" or device.isNull():
        return None
    try:
        return _read_windows_state(bytes(device.id()).decode("utf-8"))
    except (OSError, AttributeError, UnicodeError):
        return None
