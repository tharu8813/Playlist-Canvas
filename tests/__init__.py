"""Shared test-process isolation.

Qt's test mode redirects ``QStandardPaths`` data locations to a disposable
temporary root. Keep this at package import time so it runs before any test
class creates ``QSettings`` or an application service.
"""

from __future__ import annotations

import os
from tempfile import mkdtemp

from PySide6.QtCore import QStandardPaths


_TEST_LOCAL_APP_DATA = mkdtemp(prefix="playlist-canvas-tests-")
os.environ.setdefault("LOCALAPPDATA", _TEST_LOCAL_APP_DATA)
QStandardPaths.setTestModeEnabled(True)
