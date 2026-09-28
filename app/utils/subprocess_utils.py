"""Platform-safe subprocess options for GUI application helper processes."""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager

_background_lock = threading.Lock()
_background_depth = 0
_THREAD_PRIORITY_NORMAL = 0
_THREAD_PRIORITY_BELOW_NORMAL = -1


def background_work_active() -> bool:
    """Whether Preview's background mixing is running (see ``background_work``)."""
    return _background_depth > 0


def lower_thread_if_background() -> None:
    """ThreadPoolExecutor initializer: yield the CPU while background work is active."""
    if background_work_active():
        _set_current_thread_priority(_THREAD_PRIORITY_BELOW_NORMAL)


def _set_current_thread_priority(priority: int) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetThreadPriority(kernel32.GetCurrentThread(), priority)
    except (AttributeError, OSError):
        pass


@contextmanager
def background_work() -> Iterator[None]:
    """Run Preview-time mixing/analysis without competing with playback.

    While any such scope is open, helper processes start at below-normal
    priority and this thread (plus analysis pools, via
    ``lower_thread_if_background``) runs below normal, so Preview's frame
    rendering and audio get the CPU first. The work and its result are
    unchanged; it only waits when the UI needs the CPU. Export never opens
    this scope (Preview locks the editor, so the two do not overlap).
    """
    global _background_depth
    with _background_lock:
        _background_depth += 1
    _set_current_thread_priority(_THREAD_PRIORITY_BELOW_NORMAL)
    try:
        yield
    finally:
        _set_current_thread_priority(_THREAD_PRIORITY_NORMAL)
        with _background_lock:
            _background_depth -= 1


def hidden_process_kwargs() -> dict[str, object]:
    """Return flags that prevent a child console window on packaged Windows builds."""
    if sys.platform != "win32":
        return {}
    startup_info = subprocess.STARTUPINFO()
    startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup_info.wShowWindow = subprocess.SW_HIDE
    flags = subprocess.CREATE_NO_WINDOW
    if background_work_active():
        flags |= subprocess.BELOW_NORMAL_PRIORITY_CLASS
    return {
        "creationflags": flags,
        "startupinfo": startup_info,
    }
