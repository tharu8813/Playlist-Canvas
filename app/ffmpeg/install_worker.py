"""Background worker for a cancellable managed FFmpeg installation."""

from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal

from app.ffmpeg.managed_installer import (
    FFmpegInstallCancelled,
    FFmpegInstallError,
    ManagedFFmpegInstaller,
)


class FFmpegInstallWorker(QThread):
    """Downloads and verifies FFmpeg without blocking the user interface."""

    progress = Signal(str, float, str)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, installer: ManagedFFmpegInstaller) -> None:
        super().__init__()
        self.installer = installer
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """Request cancellation at the next network chunk boundary."""
        self._cancel_event.set()

    def run(self) -> None:
        """Install the latest version and forward terminal state to the GUI thread."""
        try:
            result = self.installer.install_latest(self.progress.emit, self._cancel_event)
        except FFmpegInstallCancelled:
            self.cancelled.emit()
        except FFmpegInstallError as error:
            self.failed.emit(str(error))
        except Exception as error:
            # A filesystem permission error or an unexpected network/runtime
            # exception must never leave the Settings button disabled forever.
            self.failed.emit(f"Unexpected FFmpeg installation error: {error}")
        else:
            self.succeeded.emit(result)
