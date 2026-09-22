"""Loading window shown the moment the app starts, before the heavy UI imports.

Deliberately Qt-only (no app.ui / app.services imports): it must be cheap
enough to paint before MainWindow and everything it pulls in are imported.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSettings, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen

_WIDTH, _HEIGHT = 460, 250


def launch_is_korean() -> bool:
    """Same saved language the Translator reads later (Korean by default)."""
    return str(QSettings().value("language", "ko") or "ko").lower().startswith("ko")


class LaunchSplash(QSplashScreen):
    """Product card with a status line; ``finish(window)`` closes it once the window is up."""

    def __init__(self, product_name: str, version: str, icon: QIcon | None = None) -> None:
        super().__init__(self._card(product_name, version, icon))
        self.korean = launch_is_korean()

    @staticmethod
    def _card(product_name: str, version: str, icon: QIcon | None) -> QPixmap:
        ratio = QApplication.primaryScreen().devicePixelRatio() if QApplication.primaryScreen() else 1.0
        pixmap = QPixmap(round(_WIDTH * ratio), round(_HEIGHT * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(QColor("#17181c"))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor("#2b2d34"))
        painter.drawRect(QRectF(0.5, 0.5, _WIDTH - 1, _HEIGHT - 1))
        if icon is not None and not icon.isNull():
            painter.drawPixmap(32, 40, icon.pixmap(56, 56))
        title = QFont()
        title.setPointSize(19)
        title.setBold(True)
        painter.setFont(title)
        painter.setPen(QColor("#f2f3f5"))
        painter.drawText(QRectF(104, 40, _WIDTH - 136, 34), Qt.AlignmentFlag.AlignVCenter, product_name)
        caption = QFont()
        caption.setPointSize(10)
        painter.setFont(caption)
        painter.setPen(QColor("#8b8f99"))
        painter.drawText(QRectF(104, 74, _WIDTH - 136, 22), Qt.AlignmentFlag.AlignVCenter, f"v{version}")
        painter.end()
        return pixmap

    def set_status(self, korean: str, english: str) -> None:
        """Show the current real startup step (repaints immediately)."""
        self.showMessage(korean if self.korean else english, Qt.AlignmentFlag.AlignLeft, QColor("#b8bcc6"))
        QApplication.processEvents()

    def drawContents(self, painter: QPainter) -> None:  # noqa: N802 - Qt override
        """The status line, aligned with the title column instead of the card edge."""
        painter.setPen(QColor("#2f6fed"))
        painter.drawLine(32, _HEIGHT - 64, _WIDTH - 32, _HEIGHT - 64)
        painter.setPen(QColor("#b8bcc6"))
        painter.drawText(QRectF(32, _HEIGHT - 56, _WIDTH - 64, 28), Qt.AlignmentFlag.AlignVCenter, self.message())
