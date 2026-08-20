"""Compact multi-operation progress indicator for the main status bar."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QWidget


@dataclass(slots=True)
class ActivityState:
    key: str
    label: str
    progress: float | None = None
    detail: str = ""
    sequence: int = 0


class ActivityProgressWidget(QWidget):
    """Show the most recently updated task and list every task on hover."""

    activity_changed = Signal()

    def __init__(self, korean: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._korean = korean
        self._activities: dict[str, ActivityState] = {}
        self._sequence = 0
        self.label = QLabel()
        self.label.setObjectName("activityProgressLabel")
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
        self.setVisible(False)

    @property
    def active_keys(self) -> tuple[str, ...]:
        return tuple(self._activities)

    def set_korean(self, korean: bool) -> None:
        self._korean = bool(korean)
        self._refresh()

    def begin(
        self, key: str, label: str, progress: float | None = None,
        detail: str = "",
    ) -> None:
        self._sequence += 1
        self._activities[key] = ActivityState(
            key, label, self._normalized(progress), detail, self._sequence,
        )
        self._refresh()

    def update(
        self, key: str, progress: float | None = None,
        detail: str | None = None, label: str | None = None,
    ) -> None:
        state = self._activities.get(key)
        if state is None:
            self.begin(key, label or key, progress, detail or "")
            return
        self._sequence += 1
        state.sequence = self._sequence
        state.progress = self._normalized(progress)
        if detail is not None:
            state.detail = detail
        if label is not None:
            state.label = label
        self._refresh()

    def finish(self, key: str) -> None:
        if self._activities.pop(key, None) is not None:
            self._refresh()

    def clear(self) -> None:
        self._activities.clear()
        self._refresh()

    @staticmethod
    def _normalized(progress: float | None) -> float | None:
        if progress is None:
            return None
        value = float(progress)
        if value > 1.0:
            value /= 100.0
        return max(0.0, min(1.0, value))

    def _refresh(self) -> None:
        if not self._activities:
            self.setVisible(False)
            self.setToolTip("")
            self.activity_changed.emit()
            return
        current = max(self._activities.values(), key=lambda state: state.sequence)
        self.label.setText(current.label)
        self.label.setMaximumWidth(150)
        if current.progress is None:
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setFormat("")
        else:
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(round(current.progress * 1000))
            suffix = f" · +{len(self._activities) - 1}" if len(self._activities) > 1 else ""
            self.progress_bar.setFormat(f"%p%{suffix}")
        heading = "진행 중인 작업" if self._korean else "Active operations"
        lines = [heading]
        for state in sorted(
            self._activities.values(), key=lambda item: item.sequence, reverse=True,
        ):
            if state.progress is None:
                percent = "진행률 계산 중" if self._korean else "Calculating progress"
            else:
                percent = f"{round(state.progress * 100)}%"
            lines.append(f"• {state.label} — {percent}")
            if state.detail:
                lines.append(f"  {state.detail}")
        tooltip = "\n".join(lines)
        self.setToolTip(tooltip)
        self.label.setToolTip(tooltip)
        self.progress_bar.setToolTip(tooltip)
        self.setVisible(True)
        self.activity_changed.emit()
