"""Platform-safe subprocess options for GUI application helper processes."""

from __future__ import annotations

import subprocess
import sys


def hidden_process_kwargs() -> dict[str, object]:
    """Return flags that prevent a child console window on packaged Windows builds."""
    if sys.platform != "win32":
        return {}
    startup_info = subprocess.STARTUPINFO()
    startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup_info.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startup_info,
    }
