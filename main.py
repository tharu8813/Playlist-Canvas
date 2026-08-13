"""Application entry point for Playlist Canvas."""

from __future__ import annotations

import sys
from pathlib import Path
import shutil

from PySide6.QtCore import QSettings, QStandardPaths, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app import LEGACY_PRODUCT_NAME, PRODUCT_NAME, __version__
from app.ui.main_window import MainWindow
from app.utils.logging_setup import configure_logging, install_exception_hook


def _configure_product_identity(application: QApplication) -> None:
    """Apply the new brand and migrate settings/recoveries from the old name."""
    application.setApplicationName(LEGACY_PRODUCT_NAME)
    application.setOrganizationName(LEGACY_PRODUCT_NAME)
    legacy_data = Path(QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppLocalDataLocation
    ))

    application.setApplicationName(PRODUCT_NAME)
    application.setApplicationDisplayName(PRODUCT_NAME)
    application.setOrganizationName(PRODUCT_NAME)
    current_data = Path(QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppLocalDataLocation
    ))

    current = QSettings()
    migration_key = "migration/playlist_canvas_brand"
    if not current.value(migration_key, False, bool):
        legacy = QSettings(LEGACY_PRODUCT_NAME, LEGACY_PRODUCT_NAME)
        for key in legacy.allKeys():
            if not current.contains(key):
                current.setValue(key, legacy.value(key))
        current.setValue(migration_key, True)
        current.sync()

    legacy_recoveries = legacy_data / "recoveries"
    current_recoveries = current_data / "recoveries"
    if legacy_recoveries.is_dir() and legacy_recoveries != current_recoveries:
        try:
            current_recoveries.mkdir(parents=True, exist_ok=True)
            for snapshot in legacy_recoveries.glob("*.recovery.json"):
                target = current_recoveries / snapshot.name
                if not target.exists():
                    shutil.copy2(snapshot, target)
        except OSError:
            # The legacy recovery remains untouched and can be copied manually.
            pass


def main() -> int:
    """Create and run the Qt application."""
    configure_logging()
    application = QApplication(sys.argv)
    _configure_product_identity(application)
    application.setApplicationVersion(__version__)
    resource_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    icon_path = resource_root / "app" / "resources" / "app_icon.ico"
    if icon_path.is_file():
        application.setWindowIcon(QIcon(str(icon_path)))
    install_exception_hook()
    window = MainWindow()
    window.show()
    if "--smoke-test" in sys.argv:
        QTimer.singleShot(0, application.quit)
        return application.exec()
    if not window.show_startup_dialog():
        window.close()
        return 0
    window.schedule_automatic_update_check()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
