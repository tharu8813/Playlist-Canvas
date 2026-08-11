"""Persistent Light, Dark, and system-Auto theme selection."""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtGui import QGuiApplication


class Theme(str, Enum):
    """User-selectable application theme preferences."""

    LIGHT = "light"
    DARK = "dark"
    AUTO = "auto"


class ThemeService(QObject):
    """Resolves the selected preference to an effective visual theme."""

    theme_changed = Signal(str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        saved = QSettings().value("theme", Theme.AUTO.value)
        self._preference = Theme(saved) if saved in {item.value for item in Theme} else Theme.AUTO
        style_hints = QGuiApplication.styleHints()
        if hasattr(style_hints, "colorSchemeChanged"):
            style_hints.colorSchemeChanged.connect(lambda _scheme: self.refresh_auto_theme())

    @property
    def preference(self) -> Theme:
        """Return the user's persisted theme choice."""
        return self._preference

    @property
    def effective_theme(self) -> Theme:
        """Return Light or Dark after resolving Auto against the system."""
        if self._preference is not Theme.AUTO:
            return self._preference
        style_hints = QGuiApplication.styleHints()
        scheme = style_hints.colorScheme() if hasattr(style_hints, "colorScheme") else Qt.ColorScheme.Unknown
        if scheme is Qt.ColorScheme.Light:
            return Theme.LIGHT
        if scheme is Qt.ColorScheme.Dark:
            return Theme.DARK
        window_color = QGuiApplication.palette().window().color()
        return Theme.DARK if window_color.lightness() < 128 else Theme.LIGHT

    def set_preference(self, preference: Theme | str) -> None:
        """Persist and publish a new explicit or automatic theme preference.

        Qt may unwrap ``str``-backed enum values stored in combo-box item data.
        Accepting the serialized value here keeps menu and settings-dialog callers
        equally safe.
        """
        try:
            normalized = Theme(preference)
        except ValueError:
            normalized = Theme.AUTO
        if normalized is self._preference:
            self.refresh_auto_theme(force=True)
            return
        self._preference = normalized
        QSettings().setValue("theme", normalized.value)
        self.theme_changed.emit(normalized.value, self.effective_theme.value)

    def refresh_auto_theme(self, force: bool = False) -> None:
        """Republish Auto styling when the operating-system color scheme changes."""
        if self._preference is Theme.AUTO or force:
            self.theme_changed.emit(self._preference.value, self.effective_theme.value)
