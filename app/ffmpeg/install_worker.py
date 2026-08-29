"""Background worker for a cancellable managed FFmpeg installation."""

from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal

from app.ffmpeg.managed_installer import (
    FFmpegReleaseOption,
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

    def __init__(
        self, installer: ManagedFFmpegInstaller,
        release: FFmpegReleaseOption | None = None, *, force: bool = False,
    ) -> None:
        super().__init__()
        self.installer = installer
        self.release = release
        self.force = force
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """Request cancellation at the next network chunk boundary."""
        self._cancel_event.set()

    def run(self) -> None:
        """Install the latest version and forward terminal state to the GUI thread."""
        try:
            result = (
                self.installer.install_release(
                    self.release, self.progress.emit, self._cancel_event,
                    force=self.force,
                )
                if self.release is not None else
                self.installer.install_latest(self.progress.emit, self._cancel_event)
            )
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


class FFmpegCatalogWorker(QThread):
    """Fetch selectable FFmpeg versions without blocking the Settings dialog."""

    succeeded = Signal(object)
    failed = Signal(str, object)

    def __init__(self, installer: ManagedFFmpegInstaller) -> None:
        super().__init__()
        self.installer = installer
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        # Report the manifest record even if the binary is missing/corrupt so
        # Settings can offer recovery actions instead of hiding the install.
        current = self.installer.recorded_installation()
        try:
            releases = self.installer.available_releases(self._cancel_event)
        except FFmpegInstallCancelled:
            return
        except FFmpegInstallError as error:
            self.failed.emit(str(error), current)
        except Exception as error:
            self.failed.emit(f"Unexpected FFmpeg catalog error: {error}", current)
        else:
            self.succeeded.emit((releases, current))
