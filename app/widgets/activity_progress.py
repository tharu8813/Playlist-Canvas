"""Compact multi-operation progress indicator for the main status bar.

The status bar shows one line: a single operation's own title and progress,
or, with several at once, "Working..." and their combined progress. Hovering
opens a live details popup (clicking pins it) with every operation's title,
percentage, bar, detail line and optional sub-steps -- the native tooltip
could only show a frozen block of text.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget


@dataclass(slots=True)
class ActivityStep:
    """One sub-step of an operation (e.g. "Beats & vocals 3 / 12")."""

    label: str
    progress: float | None = None
    detail: str = ""


@dataclass(slots=True)
class ActivityState:
    key: str
    label: str
    progress: float | None = None
    detail: str = ""
    steps: tuple[ActivityStep, ...] = field(default_factory=tuple)


def _normalized(progress: float | None) -> float | None:
    if progress is None:
        return None
    value = float(progress)
    if value > 1.0:
        value /= 100.0
    return max(0.0, min(1.0, value))


def _steps(steps: Sequence[ActivityStep | tuple]) -> tuple[ActivityStep, ...]:
    """Steps as ActivityStep, from ActivityStep or ``(label, progress[, detail])`` tuples."""
    result = []
    for step in steps:
        if not isinstance(step, ActivityStep):
            step = ActivityStep(*step)
        result.append(ActivityStep(step.label, _normalized(step.progress), step.detail))
    return tuple(result)


def _percent_text(progress: float | None, korean: bool) -> str:
    if progress is None:
        return "진행 중" if korean else "In progress"
    return f"{int(progress * 100 + 1e-9)}%"  # floored like QProgressBar's %p, so both agree


def _set_bar(bar: QProgressBar, progress: float | None) -> None:
    if progress is None:
        bar.setRange(0, 0)  # busy
    else:
        bar.setRange(0, 1000)
        bar.setValue(round(progress * 1000))


class ActivityProgressWidget(QWidget):
    """The status-bar line plus its live details popup."""

    activity_changed = Signal()

    def __init__(self, korean: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._korean = korean
        # Insertion order is start order: rows never jump around while they update.
        self._activities: dict[str, ActivityState] = {}
        self._pinned = False
        self.label = QLabel()
        self.label.setObjectName("activityProgressLabel")
        self.label.setMaximumWidth(170)
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("activityProgressBar")
        self.progress_bar.setFixedWidth(150)
        self.progress_bar.setFixedHeight(16)
        self.progress_bar.setTextVisible(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 2, 0)
        layout.setSpacing(6)
        layout.addWidget(self.label)
        layout.addWidget(self.progress_bar)
        self.setMinimumWidth(220)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.popup = ActivityDetailsPopup(self)
        self.setVisible(False)

    @property
    def active_keys(self) -> tuple[str, ...]:
        return tuple(self._activities)

    def set_korean(self, korean: bool) -> None:
        self._korean = bool(korean)
        self._refresh()

    def begin(
        self, key: str, label: str, progress: float | None = None,
        detail: str = "", steps: Sequence[ActivityStep | tuple] = (),
    ) -> None:
        self._activities[key] = ActivityState(key, label, _normalized(progress), detail, _steps(steps))
        self._refresh()

    def update(
        self, key: str, progress: float | None = None,
        detail: str | None = None, label: str | None = None,
        steps: Sequence[ActivityStep | tuple] | None = None,
    ) -> None:
        """``progress=None`` means "not measurable right now" (a busy bar), as before."""
        state = self._activities.get(key)
        if state is None:
            self.begin(key, label or key, progress, detail or "", steps or ())
            return
        state.progress = _normalized(progress)
        if detail is not None:
            state.detail = detail
        if label is not None:
            state.label = label
        if steps is not None:
            state.steps = _steps(steps)
        self._refresh()

    def finish(self, key: str) -> None:
        if self._activities.pop(key, None) is not None:
            self._refresh()

    def clear(self) -> None:
        self._activities.clear()
        self._refresh()

    def overall_progress(self) -> float | None:
        """Mean progress of the measurable operations; None while none is measurable."""
        values = [state.progress for state in self._activities.values() if state.progress is not None]
        return sum(values) / len(values) if values else None

    def title(self) -> str:
        if len(self._activities) == 1:
            return next(iter(self._activities.values())).label
        return "작업 중..." if self._korean else "Working..."

    def details_text(self) -> str:
        """Everything the popup shows, as plain text (accessibility, logs, tests)."""
        lines = []
        for state in self._activities.values():
            lines.append(f"{state.label} — {_percent_text(state.progress, self._korean)}")
            if state.detail:
                lines.append(f"  {state.detail}")
            for step in state.steps:
                lines.append(f"  · {step.label} — {_percent_text(step.progress, self._korean)}"
                             + (f" ({step.detail})" if step.detail else ""))
        return "\n".join(lines)

    # -- popup -----------------------------------------------------------------

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._show_popup()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not self._pinned:
            self.popup.hide()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._pinned = not self._pinned
            if self._pinned:
                self._show_popup()
            elif not self.underMouse():
                self.popup.hide()
        super().mousePressEvent(event)

    def _show_popup(self) -> None:
        if not self._activities:
            return
        self.popup.sync(list(self._activities.values()), self._korean)
        self.popup.show()
        self._place_popup()

    def _place_popup(self) -> None:
        """Right-aligned just above the status-bar line, kept on screen."""
        self.popup.adjustSize()
        anchor = self.mapToGlobal(QPoint(self.width(), 0))
        x, y = anchor.x() - self.popup.width(), anchor.y() - self.popup.height() - 6
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            x = max(area.left(), min(x, area.right() - self.popup.width()))
            y = max(area.top(), y)
        self.popup.move(x, y)

    # -- refresh ---------------------------------------------------------------

    def _refresh(self) -> None:
        if not self._activities:
            self._pinned = False
            self.popup.hide()
            self.setVisible(False)
            self.setAccessibleName("")
            self.setAccessibleDescription("")
            self.activity_changed.emit()
            return
        count = len(self._activities)
        overall = self.overall_progress()
        self.label.setText(self.title())
        _set_bar(self.progress_bar, overall)
        if overall is None:
            self.progress_bar.setFormat("")
        elif count > 1:
            self.progress_bar.setFormat(f"%p% · {count}개" if self._korean else f"%p% · {count}")
        else:
            self.progress_bar.setFormat("%p%")
        hint = ("클릭하면 세부 현황을 고정합니다." if self._korean
                else "Click to keep the details open.")
        self.setAccessibleName(f"{self.title()} {_percent_text(overall, self._korean)}")
        self.setAccessibleDescription(f"{self.details_text()}\n{hint}")
        if self.popup.isVisible():
            self.popup.sync(list(self._activities.values()), self._korean)
            self._place_popup()
        self.setVisible(True)
        self.activity_changed.emit()


class ActivityDetailsPopup(QFrame):
    """Every running operation with its own bar, detail and sub-steps, updated live."""

    def __init__(self, owner: QWidget) -> None:
        super().__init__(owner, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("activityPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedWidth(340)
        self.heading = QLabel()
        self.heading.setObjectName("activityPopupHeading")
        self._rows_layout = QVBoxLayout()
        self._rows_layout.setSpacing(10)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(self.heading)
        layout.addLayout(self._rows_layout)
        self.rows: dict[str, _ActivityRow] = {}

    def sync(self, states: list[ActivityState], korean: bool) -> None:
        self.heading.setText(
            (f"진행 중인 작업 {len(states)}개" if korean else f"{len(states)} active operation(s)")
        )
        keys = [state.key for state in states]
        for key in [key for key in self.rows if key not in keys]:
            row = self.rows.pop(key)
            self._rows_layout.removeWidget(row)
            row.deleteLater()
        for index, state in enumerate(states):
            row = self.rows.get(state.key)
            if row is None:
                row = self.rows[state.key] = _ActivityRow()
                self._rows_layout.insertWidget(index, row)
            row.set_state(state, korean)


class _ActivityRow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.title = QLabel()
        self.title.setObjectName("activityPopupTitle")
        self.percent = QLabel()
        self.percent.setObjectName("activityPopupPercent")
        self.percent.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.bar = QProgressBar()
        self.bar.setObjectName("activityPopupBar")
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.detail = QLabel()
        self.detail.setObjectName("activityPopupDetail")
        self.detail.setWordWrap(True)
        self.steps = QGridLayout()
        self.steps.setContentsMargins(10, 2, 0, 0)
        self.steps.setHorizontalSpacing(8)
        self.steps.setVerticalSpacing(3)
        self.steps.setColumnStretch(0, 1)
        self._step_widgets: list[tuple[QLabel, QProgressBar, QLabel]] = []
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(self.title, 1)
        header.addWidget(self.percent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addLayout(header)
        layout.addWidget(self.bar)
        layout.addWidget(self.detail)
        layout.addLayout(self.steps)

    def set_state(self, state: ActivityState, korean: bool) -> None:
        self.title.setText(state.label)
        self.percent.setText(_percent_text(state.progress, korean))
        _set_bar(self.bar, state.progress)
        self.detail.setText(state.detail)
        self.detail.setVisible(bool(state.detail))
        while len(self._step_widgets) > len(state.steps):
            for widget in self._step_widgets.pop():
                self.steps.removeWidget(widget)
                widget.deleteLater()
        while len(self._step_widgets) < len(state.steps):
            row = len(self._step_widgets)
            name, bar, percent = QLabel(), QProgressBar(), QLabel()
            name.setObjectName("activityPopupStep")
            bar.setObjectName("activityPopupBar")
            bar.setTextVisible(False)
            bar.setFixedSize(90, 4)
            percent.setObjectName("activityPopupPercent")
            percent.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            percent.setMinimumWidth(40)
            self.steps.addWidget(name, row, 0)
            self.steps.addWidget(bar, row, 1)
            self.steps.addWidget(percent, row, 2)
            self._step_widgets.append((name, bar, percent))
        for step, (name, bar, percent) in zip(state.steps, self._step_widgets):
            name.setText(f"{step.label} · {step.detail}" if step.detail else step.label)
            _set_bar(bar, step.progress)
            percent.setText(_percent_text(step.progress, korean))
