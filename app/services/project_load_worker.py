"""Background project loading so .pvsproj extraction never blocks the GUI thread."""

from __future__ import annotations

from pathlib import Path
import threading

from PySide6.QtCore import QThread, Signal

from app.models.project import ProjectDocument
from app.services.project_service import ProjectService


class ProjectLoadWorker(QThread):
    """Read, extract and validate one project file outside the GUI thread.

    The original exception is kept (not stringified) so the caller can re-raise
    it and the crash report/guidance still see the real type and traceback.
    """

    # (extracted bytes, total bytes); object because packages exceed 32-bit ints.
    progress = Signal(object, object)

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)
        self.cancel_event = threading.Event()
        self.document: ProjectDocument | None = None
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            self.document = ProjectService.load(
                self.path, progress=self.progress.emit, cancel_event=self.cancel_event,
            )
        except Exception as error:
            self.error = error
