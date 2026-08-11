"""Application logging and last-resort GUI crash reporting."""

from __future__ import annotations

import logging
import os
import sys
import threading
import traceback as traceback_module
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType
from typing import Type

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot, qInstallMessageHandler, QtMsgType
from PySide6.QtWidgets import QApplication, QWidget

from app.dialogs.crash_report_dialog import CrashReportDialog


LOGGER = logging.getLogger(__name__)
LOG_FILE_NAME = "playlist-canvas.log"
RUN_MARKER_NAME = "running.marker"
_CRASH_BRIDGE: "_CrashBridge | None" = None


class _CrashBridge(QObject):
    """Deliver fatal Python errors to the GUI thread, including from worker threads."""

    report_requested = Signal(str, str, str)

    def __init__(self, application: QApplication) -> None:
        super().__init__(application)
        self._showing_report = False
        self.report_requested.connect(self._show_report, Qt.ConnectionType.QueuedConnection)

    @Slot(str, str, str)
    def _show_report(self, exception_type: str, message: str, traceback_text: str) -> None:
        if self._showing_report:
            return
        self._showing_report = True
        try:
            application = QApplication.instance()
            parent = application.activeWindow() if application is not None else None
            _show_unexpected_error(parent, exception_type, message, traceback_text)
        except Exception:
            LOGGER.critical("Unable to show the crash report dialog", exc_info=True)
        finally:
            self._showing_report = False


def log_directory() -> Path:
    """Return the per-user folder reserved for recoverable diagnostic logs."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "PlaylistCanvas" / "logs"


def _run_marker_path() -> Path:
    return log_directory().parent / RUN_MARKER_NAME


def configure_logging() -> Path | None:
    """Configure UTF-8 rotating file logging once and return its location when available."""
    root = logging.getLogger()
    if getattr(root, "_playlist_canvas_configured", False):
        return getattr(root, "_playlist_canvas_log_path", None)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(threadName)s | %(message)s"
    )
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root.setLevel(logging.INFO)
    root.addHandler(stream_handler)
    log_path: Path | None = None
    try:
        directory = log_directory()
        directory.mkdir(parents=True, exist_ok=True)
        log_path = directory / LOG_FILE_NAME
        file_handler = RotatingFileHandler(
            log_path, maxBytes=2 * 1024 * 1024, backupCount=4, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        root.warning("Could not create the application log file.", exc_info=True)
    logging.captureWarnings(True)
    root._playlist_canvas_configured = True  # type: ignore[attr-defined]
    root._playlist_canvas_log_path = log_path  # type: ignore[attr-defined]
    return log_path


def install_exception_hook() -> None:
    """Send uncaught main-thread and standard-thread exceptions to a detailed crash dialog."""
    global _CRASH_BRIDGE
    sys.excepthook = _exception_hook
    threading.excepthook = _thread_exception_hook
    application = QApplication.instance()
    if application is not None and _CRASH_BRIDGE is None:
        _CRASH_BRIDGE = _CrashBridge(application)
        _install_abnormal_shutdown_marker(application)
    qInstallMessageHandler(_qt_message_handler)


def _install_abnormal_shutdown_marker(application: QApplication) -> None:
    """Report a prior native/forced termination on the next successful startup."""
    try:
        marker = _run_marker_path()
        previous = marker.read_text(encoding="utf-8") if marker.is_file() else ""
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            f"started_at={datetime.now(timezone.utc).isoformat()}\n",
            encoding="utf-8",
        )
    except OSError:
        LOGGER.warning("Could not maintain the abnormal-shutdown marker", exc_info=True)
        return

    def clear_marker() -> None:
        try:
            marker.unlink(missing_ok=True)
        except OSError:
            LOGGER.warning("Could not remove the run marker", exc_info=True)

    application.aboutToQuit.connect(clear_marker)
    if previous and _CRASH_BRIDGE is not None:
        QTimer.singleShot(
            0,
            lambda: _CRASH_BRIDGE.report_requested.emit(
                "Previous abnormal shutdown",
                "The previous app session did not close normally.",
                "A previous application session ended without a normal shutdown.\n\n" + previous,
            ),
        )


def _exception_hook(exc_type: Type[BaseException], exception: BaseException,
                    traceback: TracebackType | None) -> None:
    """Record an unexpected exception before scheduling a safe GUI report."""
    LOGGER.critical("Unhandled application exception", exc_info=(exc_type, exception, traceback))
    application = QApplication.instance()
    if application is None or _CRASH_BRIDGE is None:
        sys.__excepthook__(exc_type, exception, traceback)
        return
    traceback_text = "".join(traceback_module.format_exception(exc_type, exception, traceback))
    _CRASH_BRIDGE.report_requested.emit(exc_type.__name__, str(exception), traceback_text)


def _thread_exception_hook(arguments: threading.ExceptHookArgs) -> None:
    """Route uncaught standard-library thread errors through the same crash UI."""
    _exception_hook(arguments.exc_type, arguments.exc_value, arguments.exc_traceback)


def report_unexpected_error(context: str, exception: BaseException) -> None:
    """Log a caught-but-unexpected failure and show the same crash report UI.

    Use this for defensive ``except Exception`` paths where the application can
    remain open but the operation itself has reached an invalid state.
    """
    exc_type, _value, traceback = sys.exc_info()
    exception_type = exc_type.__name__ if exc_type is not None else type(exception).__name__
    LOGGER.critical("Unexpected error in %s", context, exc_info=(exc_type, exception, traceback))
    traceback_text = f"Context: {context}\n\n" + "".join(
        traceback_module.format_exception(exc_type or type(exception), exception, traceback)
    )
    if _CRASH_BRIDGE is not None:
        _CRASH_BRIDGE.report_requested.emit(exception_type, f"{context}: {exception}", traceback_text)


def _qt_message_handler(message_type: QtMsgType, context: object, message: str) -> None:
    """Persist Qt diagnostics; fatal Qt/native termination cannot reliably show UI."""
    levels = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }
    LOGGER.log(levels.get(message_type, logging.ERROR), "Qt: %s", message)
    if message_type is QtMsgType.QtCriticalMsg:
        trace = "Qt critical message\n\n" + message
        if _CRASH_BRIDGE is not None:
            _CRASH_BRIDGE.report_requested.emit("QtCriticalMessage", message, trace)


def _show_unexpected_error(parent: QWidget | None, exception_type: str,
                           exception_message: str, traceback_text: str) -> None:
    """Display an exception summary, stack trace, and log path after Qt returns idle."""
    translator = getattr(parent, "translator", None)
    korean = getattr(getattr(translator, "language", None), "value", "ko") == "ko"
    log_path = getattr(logging.getLogger(), "_playlist_canvas_log_path", None)
    dialog = CrashReportDialog(
        exception_type=exception_type,
        exception_message=exception_message,
        traceback_text=traceback_text,
        log_path=str(log_path) if log_path else None,
        korean=korean,
        parent=parent,
    )
    dialog.exec()
