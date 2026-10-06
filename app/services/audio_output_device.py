"""Identify Bluetooth playback without guessing from headphone brand names."""

from __future__ import annotations

import re
import sys


def _windows_device_ids(endpoint_id: str) -> tuple[str, ...] | None:
    """Walk the endpoint's PnP parents, using Windows' read-only device API.

    Qt's WASAPI id is an MMDevice endpoint id. Its PnP node is under
    SWD\\MMDEVAPI; the physical transport appears on an ancestor instead.
    None means that the transport could not be inspected.
    """
    import ctypes
    from ctypes import wintypes

    try:
        config = ctypes.WinDLL("cfgmgr32")
        config.CM_Locate_DevNodeW.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.ULONG]
        config.CM_Get_Device_IDW.argtypes = [wintypes.DWORD, wintypes.LPWSTR, wintypes.ULONG, wintypes.ULONG]
        config.CM_Get_Parent.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, wintypes.ULONG]
        for name in ("CM_Locate_DevNodeW", "CM_Get_Device_IDW", "CM_Get_Parent"):
            getattr(config, name).restype = wintypes.ULONG
        node = wintypes.DWORD()
        instance = ctypes.create_unicode_buffer("SWD\\MMDEVAPI\\" + endpoint_id)
        if config.CM_Locate_DevNodeW(ctypes.byref(node), instance, 0):
            return None
        identifiers = []
        visited = set()
        for _ in range(32):
            if node.value in visited:
                return None
            visited.add(node.value)
            identifier = ctypes.create_unicode_buffer(512)
            if config.CM_Get_Device_IDW(node, identifier, len(identifier), 0):
                return None
            identifiers.append(identifier.value)
            if _BLUETOOTH_BUS.search(identifier.value):
                return tuple(identifiers)
            # HTREE is the PnP root. Stop here so a failed parent lookup
            # elsewhere is treated as unknown, rather than a wired device.
            if identifier.value.upper().startswith("HTREE\\"):
                return tuple(identifiers)
            parent = wintypes.DWORD()
            if config.CM_Get_Parent(ctypes.byref(parent), node, 0):
                return None
            node = parent
        return None
    except (OSError, AttributeError):
        return None


_BLUETOOTH_BUS = re.compile(r"^(?:BTH|BTHENUM|BTHHFENUM|BTHLE|BTHLEDEVICE)\\", re.IGNORECASE)
_BLUETOOTH_HINT = re.compile(r"bluetooth|블루투스|bluez[_:.]|(?:bthenum|bthhfenum|bthledevice)\\", re.IGNORECASE)


def is_bluetooth_output(device) -> bool:
    """Prefer transport evidence; explicit Qt hints are a fallback on other backends."""
    if device.isNull():
        return False
    identifier = bytes(device.id()).decode("utf-8", errors="replace")
    if sys.platform == "win32" and identifier:
        ancestry = _windows_device_ids(identifier)
        if ancestry is not None:
            return any(_BLUETOOTH_BUS.search(instance) for instance in ancestry)
    return bool(_BLUETOOTH_HINT.search(identifier + " " + device.description()))
