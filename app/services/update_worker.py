"""Background workers for update discovery and verified Setup download."""

from __future__ import annotations

from pathlib import Path
import threading

from PySide6.QtCore import QThread, Signal

from app.services.update_service import (
    GitHubUpdateService,
    ReleaseInfo,
    UpdateCancelled,
    UpdateError,
)


class UpdateCheckWorker(QThread):
    release_found = Signal(object)
    failed = Signal(str)

    def __init__(self, service: GitHubUpdateService) -> None:
        super().__init__()
        self.service = service

    def run(self) -> None:
        try:
            release = self.service.fetch_latest_release()
        except UpdateError as error:
            self.failed.emit(str(error))
        except Exception as error:
            self.failed.emit(f"Unexpected update check error: {error}")
        else:
            self.release_found.emit(release)


class UpdateDownloadWorker(QThread):
    progress = Signal(float, str)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        service: GitHubUpdateService,
        release: ReleaseInfo,
        target_directory: Path,
    ) -> None:
        super().__init__()
        self.service = service
        self.release = release
        self.target_directory = target_directory
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            path = self.service.download_setup(
                self.release, self.target_directory, self.progress.emit,
                self._cancel_event,
            )
        except UpdateCancelled:
            self.cancelled.emit()
        except UpdateError as error:
            self.failed.emit(str(error))
        except Exception as error:
            self.failed.emit(f"Unexpected update download error: {error}")
        else:
            self.succeeded.emit(path)
