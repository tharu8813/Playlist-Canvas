"""Shared dark studio palette, typography and application chrome."""

from pathlib import Path
import os
import sys

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from app.utils.font_loader import load_application_font
from app.ui.studio_icons import StudioIconStyle


COLORS = {
    "window": "#191B1D", "panel": "#212426", "field": "#191C1E",
    "button": "#2C3033", "hover": "#373D40", "text": "#E6E8E7",
    "muted": "#A3AAA9", "border": "#393E40", "disabled": "#25292B",
    "alternate": "#272B2D", "shadow": "#111314", "accent": "#79C7B4",
    "accent_hover": "#96D7C7",
}


def studio_stylesheet() -> str:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    sheet = (root / "app/ui/studio.qss").read_text(encoding="utf-8")
    for name, value in sorted(COLORS.items(), key=lambda item: -len(item[0])):
        sheet = sheet.replace(f"@{name}", value)
    icon_root = root / "assets/icons" if getattr(sys, "frozen", False) else root / "app/assets/icons"
    return sheet.replace("@spin_up", (icon_root / "spin_up.svg").as_posix()).replace(
        "@spin_down", (icon_root / "spin_down.svg").as_posix(),
    ).replace("@check", (icon_root / "check.svg").as_posix())


def apply_studio_style(application: QApplication | None) -> None:
    """Install once, including dialogs created before the editor is visible."""
    if application is None or application.property("playlistCanvasStudioStyle"):
        return
    application.setStyle(StudioIconStyle("Fusion"))
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    load_application_font(fonts / "segoeui.ttf")
    load_application_font(fonts / "seguisb.ttf")
    load_application_font(fonts / "seguisym.ttf")
    load_application_font(fonts / "malgun.ttf")
    load_application_font(fonts / "malgunbd.ttf")
    font = QFont()
    font.setFamilies(["Segoe UI", "Malgun Gothic", "Noto Sans CJK KR", "sans-serif"])
    font.setPointSizeF(9.0)
    application.setFont(font)
    palette = QPalette()
    roles = {
        "Window": "window", "WindowText": "text", "Base": "field",
        "AlternateBase": "alternate", "ToolTipBase": "panel", "ToolTipText": "text",
        "Text": "text", "Button": "button", "ButtonText": "text",
        "Highlight": "accent", "HighlightedText": "window", "Link": "accent",
        "PlaceholderText": "muted", "BrightText": "text",
    }
    for role, color in roles.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(COLORS[color]))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(COLORS["muted"]))
    application.setPalette(palette)
    application.setStyleSheet(studio_stylesheet())
    application.setProperty("playlistCanvasEffectiveTheme", "dark")
    application.setProperty("playlistCanvasStudioStyle", True)
