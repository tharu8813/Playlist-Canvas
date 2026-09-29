"""Modal "Preparing Preview" popup: says what is loading while Preview is being built."""

from __future__ import annotations

from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QProgressBar, QVBoxLayout, QWidget


class PreviewLoadingDialog(QDialog):
    """A checklist of loading steps; each ``advance`` repaints before the next blocking step.

    The GUI thread is busy between steps, so every change is painted at once
    (user input stays queued: the popup is modal and nothing can be clicked).
    """

    def __init__(self, steps: list[str], korean: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("previewLoadingDialog")
        self.setWindowTitle("미리보기 준비" if korean else "Preparing Preview")
        # A title bar without a close button: it closes itself when loading is done.
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint
        )
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setMinimumWidth(380)
        title = QLabel("미리보기를 준비하고 있습니다" if korean else "Preparing the preview")
        title.setObjectName("panelTitle")
        bar = QProgressBar()
        bar.setRange(0, 0)  # busy: the steps below carry the progress
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(9)
        layout.addWidget(title)
        layout.addWidget(bar)
        self._steps = steps
        self._labels = []
        for _step in steps:
            label = QLabel()
            label.setObjectName("mutedLabel")
            layout.addWidget(label)
            self._labels.append(label)
        self.current = -1
        self._closing_timer: QTimer | None = None

    def start(self) -> None:
        """Show and paint the popup before the caller starts blocking work."""
        self.advance(0)
        self.show()
        self._paint()

    def advance(self, index: int) -> None:
        """Mark the steps before ``index`` done and ``index`` running (past the end: all done)."""
        self.current = index
        for position, (label, text) in enumerate(zip(self._labels, self._steps)):
            mark = "✓" if position < index else ("●" if position == index else "○")
            label.setText(f"{mark}  {text}")
            label.setEnabled(position <= index)
        if self.isVisible():
            self._paint()

    def finish_soon(self) -> None:
        """Close after a short beat, so the last check mark is seen."""
        self.finish(after_ms=150)

    def finish(self, *_signal_args: object, after_ms: int = 0) -> None:
        """All steps done: close (after ``after_ms``). Connect bound methods only, never
        lambdas: a connection to this deleted popup is then dropped by Qt."""
        if self._closing_timer is not None:
            return
        self.advance(len(self._steps))
        self._closing_timer = QTimer(self)
        self._closing_timer.setSingleShot(True)
        self._closing_timer.timeout.connect(self._close)
        self._closing_timer.start(after_ms)

    def _close(self) -> None:
        self.hide()
        self.deleteLater()

    def _paint(self) -> None:
        self.repaint()
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
