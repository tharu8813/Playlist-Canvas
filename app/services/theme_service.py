"""Dark-only appearance; legacy theme values remain readable in projects."""
from enum import Enum
from PySide6.QtCore import QObject, QSettings, Signal

class Theme(str, Enum):
    # Kept only so older project/settings callers can still be read. The UI
    # exposes no selector for these values and ThemeService always applies dark.
    LIGHT = 'light'
    DARK = 'dark'
    AUTO = 'auto'

class ThemeService(QObject):
    theme_changed = Signal(str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        QSettings().setValue('theme', Theme.DARK.value)

    @property
    def preference(self) -> Theme:
        return Theme.DARK

    @property
    def effective_theme(self) -> Theme:
        return Theme.DARK

    def set_preference(self, preference: Theme | str) -> None:
        """Normalize older callers to the supported studio appearance."""
        QSettings().setValue('theme', Theme.DARK.value)

    def refresh_auto_theme(self, force: bool = False) -> None:
        """System appearance no longer changes the editing workspace."""
