"""Shared test-process isolation.

Qt's test mode redirects ``QStandardPaths`` data locations to a disposable
temporary root. Keep this at package import time so it runs before any test
class creates ``QSettings`` or an application service.
"""

from __future__ import annotations

import atexit
import os
from tempfile import mkdtemp

from PySide6.QtCore import QCoreApplication, QEvent, QStandardPaths
from PySide6.QtWidgets import QApplication


_TEST_LOCAL_APP_DATA = mkdtemp(prefix="playlist-canvas-tests-")
os.environ.setdefault("LOCALAPPDATA", _TEST_LOCAL_APP_DATA)
QStandardPaths.setTestModeEnabled(True)


@atexit.register  # registered after PySide's own exit hook, so it runs first
def _delete_leftover_windows() -> None:
    """Delete the dialogs/widgets tests left open while Qt and Python are still whole.

    Left alone, PySide destroys them during interpreter shutdown, which could
    end the run with a fail-fast (0xC0000409) after every test passed.
    """
    if QCoreApplication.instance() is None:
        return
    for widget in QApplication.topLevelWidgets():
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
