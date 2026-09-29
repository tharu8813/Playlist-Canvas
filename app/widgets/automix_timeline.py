"""The AutoMix editor's two-track timeline: real waveforms, grids, handles and band lanes.

Everything drawn comes from the plan the editor is showing (a Junction, drafted
with the planner's own manual path while a drag is in flight) and from each
track's decoded audio: the waveform is the file's real peaks placed through
the clip's own source<->timeline mapping (tempo ramp included), the beat grid
is the analysis, and the band lanes are the renderer's fade windows
(``mix_lanes`` / ``band_windows_of``). The widget edits nothing itself: drags
are reported (``drag_moved`` for the overlap, ``bands_changed`` for band
windows) and the editor decides what they mean.
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QImage, QKeyEvent, QLinearGradient, QMouseEvent, QPainter, QPainterPath,
    QPen,
    QPixmap, QWheelEvent,
)
from PySide6.QtWidgets import QApplication, QToolTip, QWidget

from app.automix.models import TrackAnalysis
from app.automix.overrides import EQ_BANDS, BandWindows
from app.automix.renderer import BAND_ENVELOPES, band_windows_of, transition_dsp_style
from app.controllers.transition_audition_controller import PEAKS_PER_SECOND
from app.timeline.render_plan import AudioRenderClip
from app.widgets.transition_editor import band_hole, move_window, with_band
from app.widgets.transition_inspector import (
    INCOMING_COLOR, OUTGOING_COLOR, PLAYHEAD_COLOR, Junction, _clock, _nice_step, mix_lanes,
)

HANDLE_COLOR = QColor("#C9D0CE")
"""Handles are neutral: orange and blue belong to the songs, the accent to the selection."""
SNAP_COLOR = QColor("#E6E8E7")
LOCK_COLOR = QColor("#A3AAA9")
_BAND_LABELS = {"high": ("고음", "Highs"), "mid": ("중음", "Mids"), "low": ("저음", "Lows"),
                "level": ("음량", "Level"), "sweep": ("하이패스", "Highpass"),
                "echo": ("에코", "Echo"), "speed": ("속도·음높이", "Speed & pitch")}
_KEYS = ("←/→ 한 박자 · Shift+←/→ 0.01초 · Esc 끌기 취소", "←/→ one beat · Shift+←/→ 0.01 s · Esc cancels a drag")
_PART_TIPS = {
    "start": ("믹스 시작 · 끌어서 위치와 길이를 함께 조정", "Mix start · drag to move it and change the length"),
    "end": ("믹스 끝 · 끌어서 겹침 길이 조정", "Mix end · drag to change the overlap length"),
    "move": ("전환 구간 · 끌어서 길이는 그대로 옮기기", "Transition · drag to move it, keeping its length"),
    "outgoing": ("A 곡 · 끌면 A의 큐가 바뀌며 전환이 함께 움직입니다",
                 "Track A · drag to change A's cue; the transition moves with it"),
    "incoming": ("B 곡 · 끌어서 들어오는 곡의 시작 지점 조정", "Track B · drag to change where it starts"),
    "ramp": ("템포 변경 시작 · 끌어서 A가 B의 템포로 바뀌기 시작하는 지점 조정",
             "Tempo change starts · drag to set where A starts easing onto B's tempo"),
}
PARTS = ("move", "start", "end", "outgoing", "incoming", "ramp")
SIMPLE_PARTS = ("move", "start", "end")
"""Simple mode edits where the transition sits and how long it is; the rest is advanced."""
PART_SELECTION = {"move": "transition", "start": "transition", "end": "transition", "outgoing": "outgoing",
                  "incoming": "incoming", "ramp": "tempo"}
PART_FIELDS = {"move": ("outgoing_cue",), "start": ("outgoing_cue", "duration"), "end": ("duration",),
               "outgoing": ("outgoing_cue",), "incoming": ("incoming_cue",), "ramp": ()}
MARKER_TOP = 17.0
"""Marker labels start below the bar numbers along a lane's top edge."""
MARKER_ROW = 15.0
MARKER_ROWS = 3
RAMP_GRIP = 24.0
"""The tempo-change handle's grip: this band at the bottom of track A."""


def stack_labels(spans: list[tuple[float, float]], gap: float = 6.0) -> list[int]:
    """Row per ``(x, width)`` label, sorted by x: each takes the first row it does not run into.

    Past MARKER_ROWS rows a label shares the row that frees up first.
    """
    ends: list[float] = []
    rows = []
    for x, width in spans:
        row = next((index for index, end in enumerate(ends) if x - gap > end), None)
        if row is None:
            if len(ends) < MARKER_ROWS:
                ends.append(-math.inf)
                row = len(ends) - 1
            else:
                row = min(range(len(ends)), key=ends.__getitem__)
        ends[row] = x + width
        rows.append(row)
    return rows


def _premultiplied(color: QColor) -> np.uint32:
    """``color`` as one ARGB32_Premultiplied pixel."""
    alpha = color.alpha()
    return np.uint32((alpha << 24) | (color.red() * alpha // 255 << 16)
                     | (color.green() * alpha // 255 << 8) | color.blue() * alpha // 255)


def _mapping(clip: AudioRenderClip) -> tuple[np.ndarray, np.ndarray]:
    """Piecewise-linear timeline -> source knots for ``clip``, extended past both ends at the edge rates.

    The extension draws the unused audio (the outgoing song after its cut, the
    incoming one before its cue) where it would sit if the clip went on.
    """
    times, sources = [clip.timeline_start], [clip.source_in]
    segments = clip.rate_segments()
    for start, end, rate in segments:
        times.append(times[-1] + (end - start) / rate)
        sources.append(end)
    far = 1e5
    first_rate, last_rate = segments[0][2], segments[-1][2]
    times = [times[0] - far, *times, times[-1] + far]
    sources = [sources[0] - far * first_rate, *sources, sources[-1] + far * last_rate]
    return np.asarray(times), np.asarray(sources)


class AutoMixTimeline(QWidget):
    """See module docstring. Parts that can be dragged: PARTS plus each band's fade bars."""

    seek_requested = Signal(float)
    drag_started = Signal(str)
    drag_moved = Signal(str, float, bool)
    """(part, timeline seconds from where the drag began, invert: Shift held -- snapping flips while held)."""
    drag_finished = Signal()
    drag_cancelled = Signal()
    """Esc during a drag: nothing changes, the view goes back to the committed plan."""
    bands_changed = Signal(object)
    nudge_requested = Signal(str, int, bool)
    """(part, direction -1/+1, fine: Shift held)."""
    selection_changed = Signal(str)
    """What the side panel should show (transition_editor.SELECTIONS)."""

    RULER = 34.0
    TRACK = 96.0
    BAND = 48.0
    GAP = 6.0
    LABEL = 168.0
    RIGHT = 12.0
    GRAB = 10.0
    BAR = 7.0
    MIN_SPAN = 1.5

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.korean = True
        self.junction: Junction | None = None
        self.draft = False
        self.titles: dict[str, str] = {}
        self.analyses: tuple[TrackAnalysis | None, TrackAnalysis | None] = (None, None)
        self.peaks: dict[str, np.ndarray | None] = {}
        self.advanced = False
        self.band_linked = True
        self.playhead: float | None = None
        self.audition: tuple[float, float] | None = None
        self.loop = False
        self.duration = 0.0
        """The whole mix's length: how far the view may pan."""
        self.drag_hint = ""
        self.snap_guide: float | None = None
        self.selected_part: str | None = None
        self.selection = "transition"
        self.snap_unit = "beat"
        """"off", "beat" or "bar" (transition_editor.SNAP_UNITS): what band bars click onto."""
        self.locked: tuple[str, ...] = ()
        self.show_lanes = True
        self.audition_stale = False
        """The audio heard is an older edit than the one drawn."""
        self._hover: str | None = None
        self._press: tuple[str, QPointF] | None = None
        self._drag: tuple[str, float] | None = None
        self._band_drag: tuple[str, str, str, float, BandWindows] | None = None
        self._bands: BandWindows | None = None
        """The band windows drawn, held locally while a band bar is dragged."""
        self._pan: tuple[float, tuple[float, float]] | None = None
        self._view: tuple[float, float] = (0.0, 1.0)
        self._frozen: tuple[float, float] | None = None
        self._static_cache: tuple[tuple, tuple, QPixmap] | None = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumWidth(360)
        self._update_height()

    # -- state -------------------------------------------------------------------

    def set_junction(self, junction: Junction | None, *, refit: bool = False) -> None:
        """Show ``junction``; the view only moves when ``refit`` (another transition) or on first show."""
        first = self.junction is None
        self.junction = junction
        if self._band_drag is None:
            self._bands = (band_windows_of(junction.transition)
                           if junction is not None and junction.transition is not None else None)
        if junction is not None and (refit or first):
            self.fit()
        self._update_height()
        self.update()

    def set_advanced(self, advanced: bool) -> None:
        self.advanced = advanced
        if not advanced and self.selected_part not in (None, *SIMPLE_PARTS):
            self.selected_part = None
        # Simple mode shows the transition selected; the advanced selection is kept for coming back.
        self._update_height()
        self.update()

    def set_show_lanes(self, shown: bool) -> None:
        self.show_lanes = shown
        if not shown and self.selection.startswith("band:"):
            self._select("transition")
        self._update_height()
        self.update()

    def set_selection(self, selection: str) -> None:
        """Select without telling anyone (the editor restoring its own state)."""
        self.selection = selection
        self.update()

    def _select(self, selection: str) -> None:
        if selection != self.selection:
            self.selection = selection
            self.selection_changed.emit(selection)
        self.update()

    def parts(self) -> tuple[str, ...]:
        """The handles this mode offers (also the ↑/↓ keyboard order)."""
        if not self.advanced:
            return SIMPLE_PARTS
        bands = tuple(f"band:{lane.key}" for lane in self.lanes() if self.editable_bands() and lane.key in EQ_BANDS)
        return (*PARTS[:-1], *(("ramp",) if self._ramp_span() is not None else ()), *bands)

    def lanes(self):
        """The band lanes shown in advanced mode (``mix_lanes`` of the transition)."""
        if not self.advanced or not self.show_lanes or self.junction is None or self.junction.transition is None:
            return []
        return mix_lanes(self.junction.transition)

    def editable_bands(self) -> bool:
        transition = self.junction.transition if self.junction is not None else None
        return transition is not None and transition_dsp_style(transition) in BAND_ENVELOPES

    def _update_height(self) -> None:
        shown = self.advanced and self.show_lanes
        lanes = len(self.lanes()) if shown else 0
        if shown and lanes == 0:
            lanes = 1  # the "no overlap" note
        height = self.RULER + 2 * (self.TRACK + self.GAP) + lanes * (self.BAND + self.GAP) + 10
        self.setMinimumHeight(int(height))

    def sizeHint(self) -> QSize:
        return QSize(900, self.minimumHeight())

    @property
    def track_height(self) -> float:
        """Track lanes take the height the window has to spare (up to a limit); band lanes stay fixed."""
        spare = max(0.0, self.height() - self.minimumHeight())
        return self.TRACK + min(spare / 2.0, 110.0)

    @property
    def dragging(self) -> bool:
        return self._drag is not None or self._band_drag is not None

    # -- view ----------------------------------------------------------------------

    def fit(self) -> None:
        junction = self.junction
        if junction is None:
            return
        length = junction.end - junction.start
        pad = max(6.0, length * 0.75)
        start, end = junction.start - pad, junction.end + pad
        ramp = self._ramp_span()
        if ramp is not None:  # where the tempo starts to change is part of the transition
            start = min(start, ramp[0] - max(2.0, (ramp[1] - ramp[0]) * 0.1))
        if self.audition is not None:
            start, end = min(start, self.audition[0] - 1.0), max(end, self.audition[1] + 1.0)
        self._set_view(start, end)

    def view(self) -> tuple[float, float]:
        return self._frozen or self._view

    def _set_view(self, start: float, end: float) -> None:
        span = max(self.MIN_SPAN, end - start)
        limit = max(self.duration, span) + 30.0
        start = min(max(start, -30.0), limit - span)
        self._view = (start, start + span)
        self.update()

    def zoom(self, factor: float, anchor: float | None = None) -> None:
        """``factor`` < 1 zooms in, around ``anchor`` seconds (the view's middle by default)."""
        start, end = self._view
        anchor = (start + end) / 2 if anchor is None else anchor
        span = min(max(self.MIN_SPAN, (end - start) * factor), max(60.0, self.duration + 60.0))
        ratio = (anchor - start) / max(1e-9, end - start)
        self._set_view(anchor - span * ratio, anchor - span * ratio + span)

    def pan(self, fraction: float) -> None:
        start, end = self._view
        shift = (end - start) * fraction
        self._set_view(start + shift, end + shift)

    def _plot(self) -> tuple[float, float]:
        return self.LABEL, self.width() - self.RIGHT

    def seconds_per_pixel(self) -> float:
        start, end = self.view()
        left, right = self._plot()
        return (end - start) / max(1.0, right - left)

    def x_of(self, seconds: float) -> float:
        start, end = self.view()
        left, right = self._plot()
        return left + (right - left) * (seconds - start) / max(1e-9, end - start)

    def seconds_at(self, x: float) -> float:
        start, end = self.view()
        left, right = self._plot()
        return start + (x - left) / max(1.0, right - left) * (end - start)

    # -- geometry --------------------------------------------------------------------

    def _lane_top(self, lane: int) -> float:
        """Top of track lane 0 (outgoing), 1 (incoming), then band lanes 2..."""
        if lane < 2:
            return self.RULER + self.GAP + lane * (self.track_height + self.GAP)
        return self.RULER + self.GAP + 2 * (self.track_height + self.GAP) + (lane - 2) * (self.BAND + self.GAP)

    def _tracks_bottom(self) -> float:
        return self._lane_top(1) + self.track_height

    def _ramp_span(self) -> tuple[float, float] | None:
        """Timeline (start, end) of the outgoing track's tempo change, or None without one."""
        junction = self.junction
        ramp = junction.outgoing.tempo_ramp if junction is not None else None
        if ramp is None:
            return None
        clip = junction.outgoing
        return clip.timeline_at(ramp.source_start), clip.timeline_at(ramp.source_end)

    def _band_bar(self, lane_index: int, band: str, side: str) -> QRectF | None:
        junction = self.junction
        if self._bands is None or junction is None or junction.end <= junction.start:
            return None
        window = self._bands[EQ_BANDS.index(band)][0 if side == "out" else 1]
        top = self._lane_top(lane_index) + self.BAND - 2 * self.BAR - 4 + (0 if side == "out" else self.BAR + 2)
        length = junction.end - junction.start
        left = self.x_of(junction.start + length * window[0])
        right = self.x_of(junction.start + length * window[1])
        return QRectF(left, top, max(3.0, right - left), self.BAR)

    def hit(self, x: float, y: float) -> tuple[str, ...] | None:
        """What is under (x, y): (part,) for a PARTS handle, ("band", band, side, kind) for a band
        bar, ("lane", band) for the rest of a band lane; None for empty space (a click there seeks)."""
        junction = self.junction
        if junction is None or x < self.LABEL:
            return None
        for lane_index, lane in enumerate(self.lanes(), start=2):
            top = self._lane_top(lane_index)
            if not top <= y <= top + self.BAND:
                continue
            if self.editable_bands():
                for side in ("out", "in"):
                    rect = self._band_bar(lane_index, lane.key, side)
                    if rect is None or not rect.top() - 4 <= y <= rect.bottom() + 4:
                        continue
                    if abs(x - rect.left()) <= self.GRAB:
                        return ("band", lane.key, side, "start")
                    if abs(x - rect.right()) <= self.GRAB:
                        return ("band", lane.key, side, "end")
                    if rect.left() < x < rect.right():
                        return ("band", lane.key, side, "move")
            return ("lane", lane.key) if lane.key in EQ_BANDS else None
        top, bottom = self.RULER - 16, self._tracks_bottom()
        if not top <= y <= bottom:
            return None
        start, end = self.x_of(junction.start), self.x_of(junction.end)
        ramp = self._ramp_span() if self.advanced else None
        lane_bottom = self._lane_top(0) + self.track_height
        if ramp is not None and self._lane_top(0) <= y <= lane_bottom and abs(x - self.x_of(ramp[0])) <= self.GRAB:
            # Beside the mix start handle, its grip at the bottom of track A decides.
            if abs(x - self.x_of(ramp[0])) < abs(x - start) or y >= lane_bottom - RAMP_GRIP:
                return ("ramp",)
        if junction.end > junction.start:
            if abs(x - start) <= self.GRAB and (abs(x - start) <= abs(x - end)):
                return ("start",)
            if abs(x - end) <= self.GRAB:
                return ("end",)
        elif abs(x - start) <= self.GRAB and y < self._lane_top(1):
            return ("move",)
        if y < self.RULER:
            return None  # the ruler seeks
        incoming_top = self._lane_top(1)
        if incoming_top <= y <= bottom and self.advanced:
            clip = junction.incoming
            if self.x_of(clip.timeline_start) - 2 <= x <= self.x_of(clip.timeline_end) + 2:
                return ("incoming",)
        if start <= x <= end and y < incoming_top:
            return ("move",)
        if self.advanced and y < incoming_top and self.x_of(junction.outgoing.timeline_start) <= x <= start:
            return ("outgoing",)
        return None

    # -- mouse -----------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        position = event.position()
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan = (position.x(), self._view)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if event.button() != Qt.MouseButton.LeftButton or self.junction is None:
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        hit = self.hit(position.x(), position.y())
        if hit is not None and hit[0] in ("band", "lane"):
            self.selected_part = f"band:{hit[1]}"
            self._select(f"band:{hit[1]}")
            if hit[0] == "band":
                self._band_drag = (hit[1], hit[2], hit[3], position.x(), self._bands)
                self._frozen = self._view
            return
        if hit is not None:
            self.selected_part = hit[0]
            self._select(PART_SELECTION[hit[0]])
            self._press = (hit[0], position)
            self._frozen = self._view
            self.update()
        elif position.x() >= self.LABEL:
            self.seek_requested.emit(self.seconds_at(position.x()))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        position = event.position()
        free = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if self._pan is not None:
            origin, (start, end) = self._pan
            shift = (origin - position.x()) * (end - start) / max(1.0, self._plot()[1] - self._plot()[0])
            self._set_view(start + shift, end + shift)
            return
        if self._band_drag is not None:
            self._bands = self._dragged_bands(position.x(), free)
            self.update()
            return
        if self._press is not None and self._drag is None:
            part, origin = self._press
            if (position - origin).manhattanLength() < QApplication.startDragDistance():
                return
            self._drag = (part, self.seconds_at(origin.x()))
            self.setCursor(Qt.CursorShape.SizeHorCursor if part in ("start", "end", "ramp")
                           else Qt.CursorShape.ClosedHandCursor)
            self.drag_started.emit(part)
        if self._drag is not None:
            part, origin = self._drag
            self.drag_moved.emit(part, self.seconds_at(position.x()) - origin, free)
            self.update()
            return
        self._hover_at(position, event.globalPosition().toPoint())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self._pan is not None:
            self._pan = None
            self.unsetCursor()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._frozen = None
        if self._band_drag is not None:
            base = self._band_drag[4]
            self._band_drag = None
            if self._bands != base:
                self.bands_changed.emit(self._bands)
            self.update()
            return
        press, self._press = self._press, None
        if self._drag is not None:
            self._drag = None
            self.drag_hint = ""
            self.snap_guide = None
            self.drag_finished.emit()
            self.update()
        elif press is not None:
            self.update()  # a click on a handle only selects it

    def cancel_drag(self) -> bool:
        """Drop a drag in flight (Esc): nothing is committed. False if there was none."""
        if self._drag is None and self._band_drag is None and self._press is None:
            return False
        dragging = self._drag is not None or self._band_drag is not None
        if self._band_drag is not None:
            self._bands = self._band_drag[4]
        self._drag = self._band_drag = self._press = None
        self._frozen = None
        self.drag_hint = ""
        self.snap_guide = None
        self.unsetCursor()
        if dragging:
            self.drag_cancelled.emit()
        self.update()
        return dragging

    def _hover_at(self, position: QPointF, global_position) -> None:
        hit = self.hit(position.x(), position.y())
        hover = None if hit is None else ":".join(hit)
        locked = hit is not None and any(name in self.locked for name in PART_FIELDS.get(hit[0], ()))
        if hover != self._hover:
            self._hover = hover
            if hit is None or hit[0] == "lane":
                self.unsetCursor()
            elif locked:
                self.setCursor(Qt.CursorShape.ForbiddenCursor)
            elif hit[0] in ("start", "end", "ramp") or (hit[0] == "band" and hit[3] != "move"):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            else:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.update()
        keys = _KEYS[0 if self.korean else 1]
        if hit is not None and hit[0] in _PART_TIPS:
            tip = _PART_TIPS[hit[0]][0 if self.korean else 1]
            if locked:
                tip += (" · 고정됨: 속성 패널에서 고정을 풀면 움직일 수 있습니다" if self.korean
                        else " · kept: unlock it in the properties panel to move it")
            QToolTip.showText(global_position, f"{tip}\n{keys}", self)
        elif hit is not None and hit[0] == "lane":
            korean, english = _BAND_LABELS[hit[1]]
            QToolTip.showText(global_position, (f"{korean} 대역 · 클릭해서 선택하면 속성 패널에서 수치로 조절합니다"
                                                if self.korean else
                                                f"{english} band · click to select it and set it in the panel"), self)
        elif hit is not None:
            korean, english = _BAND_LABELS[hit[1]]
            QToolTip.showText(global_position, (
                f"{korean}: {'A가 사라지는' if hit[2] == 'out' else 'B가 올라오는'} 구간 · 끌어서 옮기고 끝을 끌어 길이 조정"
                if self.korean else
                f"{english}: where {'A fades out' if hit[2] == 'out' else 'B fades in'} · drag to move, drag an end to resize"
            ), self)
        elif position.x() >= self.LABEL and self.junction is not None:
            seconds = self.seconds_at(position.x())
            parts = [f"{'믹스' if self.korean else 'Mix'} {_clock(seconds, precise=True)}"]
            for letter, clip in (("A", self.junction.outgoing), ("B", self.junction.incoming)):
                if clip.timeline_start <= seconds <= clip.timeline_end:
                    parts.append(f"{letter} {_clock(clip.source_at(seconds), precise=True)}")
            QToolTip.showText(global_position, " · ".join(parts) + "\n" + (
                "클릭: 이 위치로 이동 · Ctrl+휠: 확대/축소 · 휠: 이동" if self.korean
                else "Click: seek · Ctrl+wheel: zoom · Wheel: scroll"), self)

    def leaveEvent(self, _event) -> None:
        if self._hover is not None:
            self._hover = None
            self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            steps = delta.y() / 120.0
            self.zoom(0.8 ** steps, self.seconds_at(event.position().x()))
        else:
            amount = delta.x() or delta.y()
            self.pan(-amount / 120.0 * 0.1)
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        fine = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if key == Qt.Key.Key_Escape and self.cancel_drag():
            return
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right) and not event.modifiers() & (
                Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ControlModifier):
            direction = -1 if key == Qt.Key.Key_Left else 1
            if self.selected_part is not None:
                self.nudge_requested.emit(self.selected_part, direction, fine)
            else:
                self.pan(0.1 * direction)
        elif key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            order = [None, *self.parts()]
            index = order.index(self.selected_part) if self.selected_part in order else 0
            self.selected_part = order[(index + (1 if key == Qt.Key.Key_Down else -1)) % len(order)]
            part = self.selected_part
            self._select("transition" if part is None else part if part.startswith("band:") else PART_SELECTION[part])
        elif key == Qt.Key.Key_Escape and self.selected_part is not None:
            self.selected_part = None
            self.update()
        else:
            super().keyPressEvent(event)

    def _dragged_bands(self, x: float, invert: bool) -> BandWindows:
        """The band windows under a bar drag to ``x``; snapping follows ``snap_unit``, flipped while Shift is held."""
        band, side, kind, press_x, base = self._band_drag
        junction = self.junction
        length = max(1e-6, junction.end - junction.start)
        delta = (self.seconds_at(x) - self.seconds_at(press_x)) / length
        out_window, in_window = base[EQ_BANDS.index(band)]
        own = out_window if side == "out" else in_window
        if (self.snap_unit != "off") != invert:  # the edge clicks onto a beat/bar, or the window's start, middle, end
            anchor = own[1] if kind == "end" else own[0]
            reach = 8.0 * self.seconds_per_pixel() / length
            points = [0.0, 0.5, 1.0]
            incoming = self.analyses[1]
            if incoming is not None and incoming.bpm:
                beat = 60.0 / incoming.bpm / length
                if self.snap_unit == "bar":
                    beat *= incoming.meter_numerator or 4
                points.append(round((anchor + delta) / beat) * beat)
            nearest = min(points, key=lambda point: abs(point - anchor - delta))
            if abs(nearest - anchor - delta) <= reach:
                delta = nearest - anchor
        if self.band_linked:
            out_window, in_window = move_window(out_window, kind, delta), move_window(in_window, kind, delta)
        elif side == "out":
            out_window = move_window(out_window, kind, delta)
        else:
            in_window = move_window(in_window, kind, delta)
        return with_band(base, band, out_window, in_window)

    # -- painting ----------------------------------------------------------------------

    def _static_state(self) -> tuple[tuple, tuple]:
        """What the static layer shows: (objects compared by identity, values compared by equality)."""
        junction = self.junction
        ids = (junction.outgoing.track_id, junction.incoming.track_id) if junction is not None else ()
        band_hot = (self._band_drag[:2] if self._band_drag is not None
                    else self._hover if self._hover is not None and self._hover.startswith("band") else None)
        objects = (junction, *self.analyses, *(self.peaks.get(track_id) for track_id in ids))
        values = (self.width(), self.height(), self.devicePixelRatioF(), self.view(), self.draft, self.advanced,
                  self.show_lanes, self.audition_stale,
                  self._bands, band_hot, self.korean, self.audition, self.loop,
                  tuple(track_id in self.peaks for track_id in ids),
                  tuple(self.titles.get(track_id) for track_id in ids),
                  self.palette().cacheKey(), self.font().key())
        return objects, values

    def _static_layer(self) -> QPixmap:
        """Everything but the handles, guides and playhead, drawn again only when it changes.

        The playhead moves many times a second during an audition and hovering
        restyles a handle; neither redraws the waveforms, grids and lanes.
        """
        objects, values = self._static_state()
        cached = self._static_cache
        if (cached is not None and cached[1] == values and len(cached[0]) == len(objects)
                and all(a is b for a, b in zip(cached[0], objects))):
            return cached[2]
        ratio = self.devicePixelRatioF()
        pixmap = QPixmap(max(1, round(self.width() * ratio)), max(1, round(self.height() * ratio)))
        pixmap.setDevicePixelRatio(ratio)
        painter = QPainter(pixmap)
        self._paint_static(painter)
        painter.end()
        self._static_cache = (objects, values, pixmap)
        return pixmap

    def _small_font(self) -> QFont:
        small = QFont(self.font())
        small.setPointSizeF(max(7.5, small.pointSizeF() - 0.5))
        return small

    def _paint_static(self, painter: QPainter) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        painter.fillRect(QRectF(0, 0, self.width(), self.height()), palette.window())
        painter.setFont(self._small_font())
        junction = self.junction
        if junction is None:
            painter.setPen(palette.placeholderText().color())
            painter.drawText(QRectF(0, 0, self.width(), self.height()), Qt.AlignmentFlag.AlignCenter,
                             "편집할 전환이 없습니다 · 재생할 곡을 2개 이상 추가하세요" if self.korean
                             else "No transition to edit · add at least two playable tracks")
            return
        left, right = self._plot()
        lanes = self.lanes()
        bottom = (self._lane_top(2 + len(lanes)) - self.GAP) if lanes else self._tracks_bottom()
        painter.fillRect(QRectF(0, 0, self.LABEL - 4, self.height()), palette.alternateBase())
        self._paint_ruler(painter, left, right)
        for lane, clip, analysis, color, letter in (
            (0, junction.outgoing, self.analyses[0], OUTGOING_COLOR, "A"),
            (1, junction.incoming, self.analyses[1], INCOMING_COLOR, "B"),
        ):
            self._paint_track(painter, lane, clip, analysis, color, letter, left, right)
        if self.advanced:
            self._paint_bands(painter, lanes, left, right)
        if junction.end > junction.start:  # the overlap, tinted over every lane
            painter.save()
            painter.setClipRect(QRectF(left, 0, right - left, self.height()))
            band = QColor(palette.highlight().color())
            band.setAlpha(22)
            painter.fillRect(QRectF(self.x_of(junction.start), self.RULER,
                                    self.x_of(junction.end) - self.x_of(junction.start), bottom - self.RULER), band)
            painter.restore()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._static_layer())
        junction = self.junction
        if junction is None:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(self._small_font())
        palette = self.palette()
        left, right = self._plot()
        lanes = self.lanes()
        bottom = (self._lane_top(2 + len(lanes)) - self.GAP) if lanes else self._tracks_bottom()
        painter.save()
        painter.setClipRect(QRectF(left, 0, right - left, self.height()))
        self._paint_selection(painter, lanes)
        self._paint_handles(painter, bottom)
        if self.snap_guide is not None:
            x = self.x_of(self.snap_guide)
            painter.setPen(QPen(SNAP_COLOR, 1.2, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(x, self.RULER), QPointF(x, bottom))
        if self.playhead is not None:
            x = self.x_of(self.playhead)
            painter.setPen(QPen(PLAYHEAD_COLOR, 1.6))
            painter.drawLine(QPointF(x, self.RULER - 10), QPointF(x, bottom))
            head = QPainterPath(QPointF(x - 6, self.RULER - 16))
            head.lineTo(x + 6, self.RULER - 16)
            head.lineTo(x, self.RULER - 8)
            head.closeSubpath()
            painter.fillPath(head, PLAYHEAD_COLOR)
        painter.restore()
        self._paint_selected_label(painter, lanes)
        self._paint_selection_name(painter)
        if self.drag_hint and (self.dragging or self._press is not None):
            self._paint_hint(painter, left, right)
        if self.hasFocus():
            painter.setPen(QPen(palette.highlight().color(), 1, Qt.PenStyle.DotLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1))

    def _selection_rect(self, lanes) -> QRectF | None:
        """The selected target's outline, in widget coordinates."""
        junction = self.junction
        selection = self.selection if self.advanced else "transition"
        if selection == "transition":
            x0, x1 = self.x_of(junction.start), self.x_of(junction.end)
            return QRectF(x0 - 3, self.RULER + 1, max(6.0, x1 - x0 + 6), self._tracks_bottom() - self.RULER)
        if selection in ("outgoing", "incoming"):
            clip, lane = (junction.outgoing, 0) if selection == "outgoing" else (junction.incoming, 1)
            x0, x1 = self.x_of(clip.timeline_start), self.x_of(clip.timeline_end)
            return QRectF(x0, self._lane_top(lane) - 1, x1 - x0, self.track_height + 2)
        if selection == "tempo":
            ramp = self._ramp_span()
            if ramp is None:
                return None
            x0, x1 = self.x_of(ramp[0]), self.x_of(ramp[1])
            return QRectF(x0 - 3, self._lane_top(0) - 1, max(6.0, x1 - x0 + 6), self.track_height + 2)
        for lane_index, lane in enumerate(lanes, start=2):
            if selection == f"band:{lane.key}":
                return QRectF(self.LABEL, self._lane_top(lane_index) - 1,
                              self._plot()[1] - self.LABEL, self.BAND + 2)
        return None

    def _paint_selection(self, painter: QPainter, lanes) -> None:
        """The selected target tinted and outlined in the accent (the outline alone hid under the song borders)."""
        rect = self._selection_rect(lanes)
        if rect is None:
            return
        accent = QColor(self.palette().highlight().color())
        fill = QColor(accent)
        fill.setAlpha(36)
        painter.setBrush(fill)
        painter.setPen(QPen(accent, 2.5, Qt.PenStyle.DashLine if self.draft else Qt.PenStyle.SolidLine))
        painter.drawRoundedRect(rect, 3, 3)

    def _paint_selected_label(self, painter: QPainter, lanes) -> None:
        """The selected row's label marked with an accent bar, so the row reads as selected even off-screen."""
        if (self.selection if self.advanced else "transition") == "transition":
            return  # spans both tracks: the tinted overlap says it
        rect = self._selection_rect(lanes)
        if rect is None:
            return
        accent = QColor(self.palette().highlight().color())
        row = QRectF(0, rect.top() + 1, self.LABEL - 4, rect.height() - 2)
        fill = QColor(accent)
        fill.setAlpha(40)
        painter.fillRect(row, fill)
        painter.fillRect(QRectF(0, row.top(), 4, row.height()), accent)

    def _paint_selection_name(self, painter: QPainter) -> None:
        """What is selected, named, in the corner above the track labels (never over the audio)."""
        from app.widgets.transition_editor import selection_name

        palette = self.palette()
        box = QRectF(0, 0, self.LABEL - 4, self.RULER)
        painter.fillRect(box, palette.alternateBase())
        selection = self.selection if self.advanced else "transition"
        painter.setPen(palette.placeholderText().color())
        painter.drawText(box.adjusted(10, 2, -4, -box.height() / 2), Qt.AlignmentFlag.AlignBottom,
                         "선택" if self.korean else "Selected")
        bold = QFont(painter.font())
        bold.setBold(True)
        painter.setFont(bold)
        painter.setPen(palette.highlight().color())
        name = QFontMetrics(bold).elidedText(selection_name(selection, self.korean), Qt.TextElideMode.ElideRight,
                                             int(box.width() - 14))
        painter.drawText(box.adjusted(10, box.height() / 2, -4, -2), Qt.AlignmentFlag.AlignTop, name)
        painter.setFont(self._small_font())

    def _paint_hint(self, painter: QPainter, left: float, right: float) -> None:
        """The value under a drag (and how far it moved), next to the handle being dragged."""
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(self.drag_hint) + 18
        anchor = self.x_of(self.snap_guide) if self.snap_guide is not None else self._drag_x()
        x = min(max(left, anchor + 10), right - width) if anchor is not None else right - width
        box = QRectF(max(left, x), 2, width, 22)
        painter.setPen(QPen(self.palette().highlight().color(), 1))
        painter.setBrush(QColor(12, 14, 16, 235))
        painter.drawRoundedRect(box, 5, 5)
        painter.setPen(SNAP_COLOR)
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, self.drag_hint)

    def _drag_x(self) -> float | None:
        junction = self.junction
        part = self._drag[0] if self._drag is not None else None
        if part in ("move", "start", "outgoing"):
            return self.x_of(junction.start)
        if part == "end":
            return self.x_of(junction.end)
        if part == "incoming":
            return self.x_of(junction.incoming.timeline_start)
        if part == "ramp" and self._ramp_span() is not None:
            return self.x_of(self._ramp_span()[0])
        return None

    def _paint_ruler(self, painter: QPainter, left: float, right: float) -> None:
        palette = self.palette()
        muted = palette.placeholderText().color()
        painter.fillRect(QRectF(left, 0, right - left, self.RULER), palette.base())
        start, end = self.view()
        if self.audition is not None:  # the window heard: solid when current, dashed while an older edit plays
            a, b = self.audition
            x0, x1 = max(left, self.x_of(a)), min(right, self.x_of(b))
            if x1 > x0:
                color = QColor(palette.highlight().color() if not self.audition_stale else QColor("#F5C66B"))
                color.setAlpha(170 if self.loop else 90)
                if self.audition_stale:
                    painter.setPen(QPen(color, 3, Qt.PenStyle.DashLine))
                    painter.drawLine(QPointF(x0, self.RULER - 3), QPointF(x1, self.RULER - 3))
                else:
                    painter.fillRect(QRectF(x0, self.RULER - 5, x1 - x0, 4), color)
        step = _nice_step(end - start, right - left)
        tick = math.ceil(start / step) * step
        painter.save()
        painter.setClipRect(QRectF(left, 0, right - left, self.RULER))
        while tick <= end:
            x = self.x_of(tick)
            painter.setPen(QPen(muted, 1))
            painter.drawLine(QPointF(x, self.RULER - 8), QPointF(x, self.RULER))
            for minor in range(1, 5):
                mx = self.x_of(tick + step * minor / 5)
                painter.drawLine(QPointF(mx, self.RULER - 4), QPointF(mx, self.RULER))
            painter.setPen(palette.text().color())
            painter.drawText(QRectF(x + 3, 3, 90, 16), Qt.AlignmentFlag.AlignLeft,
                             _clock(tick, precise=step < 1.0))
            tick += step
        painter.restore()

    def _paint_track(self, painter: QPainter, lane: int, clip: AudioRenderClip, analysis: TrackAnalysis | None,
                     color: QColor, letter: str, left: float, right: float) -> None:
        palette = self.palette()
        top = self._lane_top(lane)
        rect = QRectF(left, top, right - left, self.track_height)
        painter.fillRect(rect, palette.base())
        self._paint_track_label(painter, lane, clip, analysis, color, letter)
        painter.save()
        painter.setClipRect(rect)
        x0, x1 = self.x_of(clip.timeline_start), self.x_of(clip.timeline_end)
        active = QRectF(max(left, x0), top + 1, max(0.0, min(right, x1) - max(left, x0)), self.track_height - 2)
        tint = QColor(color)
        tint.setAlpha(22)
        painter.fillRect(active, tint)
        self._paint_grid(painter, clip, analysis, color, top)
        self._paint_waveform(painter, clip, color, top, left, right)
        if lane == 0 and clip.tempo_ramp is not None:
            self._paint_ramp(painter, clip, analysis, color, top, left)
        painter.setPen(QPen(color, 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(active, 3, 3)
        self._paint_markers(painter, lane, clip, color, top)
        painter.restore()

    def _paint_ramp(self, painter: QPainter, clip: AudioRenderClip, analysis: TrackAnalysis | None,
                    color: QColor, top: float, left: float) -> None:
        """Track A's tempo change: a band that brightens toward the new tempo, and what it changes to."""
        ramp = clip.tempo_ramp
        r0, r1 = clip.timeline_at(ramp.source_start), clip.timeline_at(ramp.source_end)
        x0, x1 = self.x_of(r0), self.x_of(r1)
        lane_bottom = top + self.track_height
        if x1 - x0 >= 1.0:
            glow = QLinearGradient(x0, 0, x1, 0)
            tint = QColor(color.lighter(125))
            tint.setAlpha(8)
            glow.setColorAt(0.0, tint)
            tint.setAlpha(46)
            glow.setColorAt(1.0, tint)
            painter.fillRect(QRectF(x0, top + 1, x1 - x0, self.track_height - 2), QBrush(glow))
            hatch = QBrush(color.lighter(120), Qt.BrushStyle.BDiagPattern)
            painter.fillRect(QRectF(x0, lane_bottom - 12, x1 - x0, 10), hatch)
        base = analysis.bpm * clip.playback_rate if analysis is not None and analysis.bpm else None
        target = analysis.bpm * ramp.end_rate if analysis is not None and analysis.bpm else None
        if abs(ramp.end_rate - clip.playback_rate) < 1e-9:
            text = "키 맞춤" if self.korean else "Key glide"
        elif target is None:
            text = "템포 변경" if self.korean else "Tempo change"
        else:
            text = f"{base:.1f} → {target:.1f} BPM"
        if x1 - x0 < 1.0:
            text = ("즉시 · " if self.korean else "at once · ") + text
        bars = ""
        if analysis is not None and analysis.bpm and x1 - x0 >= 1.0:
            bar = (analysis.meter_numerator or 4) * 60.0 / analysis.bpm
            bars = f" · {(ramp.source_end - ramp.source_start) / bar:.1f}" + ("마디" if self.korean else " bars")
        label = ("템포 변경 " if self.korean else "Tempo ") + text + bars
        box = QRectF(max(x0, left) + 6, lane_bottom - RAMP_GRIP - 18, painter.fontMetrics().horizontalAdvance(label) + 10, 16)
        painter.fillRect(box, QColor(17, 19, 20, 200))  # legible over the waveform
        painter.setPen(color.lighter(135))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, label)

    def _paint_track_label(self, painter: QPainter, lane: int, clip: AudioRenderClip,
                           analysis: TrackAnalysis | None, color: QColor, letter: str) -> None:
        palette = self.palette()
        top = self._lane_top(lane)
        badge = QRectF(10, top + 10, 22, 22)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(badge, 5, 5)
        bold = QFont(painter.font())
        bold.setBold(True)
        painter.setFont(bold)
        painter.setPen(QColor("#111314"))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, letter)
        painter.setPen(palette.text().color())
        width = int(self.LABEL - 50)
        title = QFontMetrics(bold).elidedText(self.titles.get(clip.track_id, clip.track_id),
                                              Qt.TextElideMode.ElideRight, width)
        painter.drawText(QRectF(38, top + 10, width, 22), Qt.AlignmentFlag.AlignVCenter, title)
        painter.setFont(QFont(self.font()))
        painter.setPen(color.lighter(115))
        role = ("나가는 곡" if lane == 0 else "들어오는 곡") if self.korean else ("Outgoing" if lane == 0 else "Incoming")
        painter.drawText(QRectF(12, top + 36, self.LABEL - 22, 18), Qt.AlignmentFlag.AlignVCenter, role)
        painter.setPen(palette.placeholderText().color())
        facts = []
        if analysis is not None and analysis.bpm:
            facts.append(f"{analysis.bpm:.1f} BPM")
        if analysis is not None and analysis.key:
            facts.append(analysis.key)
        if analysis is None:
            facts.append("박자 분석 없음" if self.korean else "No beat analysis")
        elif analysis.beat_alignment_quality() != "reliable":
            facts.append("박자 불확실" if self.korean else "beats uncertain")
        text = QFontMetrics(painter.font()).elidedText(" · ".join(facts), Qt.TextElideMode.ElideRight,
                                                       int(self.LABEL - 22))
        painter.drawText(QRectF(12, top + 54, self.LABEL - 22, 18), Qt.AlignmentFlag.AlignVCenter, text)

    def _paint_grid(self, painter: QPainter, clip: AudioRenderClip, analysis: TrackAnalysis | None,
                    color: QColor, top: float) -> None:
        if analysis is None:
            return
        start, end = self.view()
        times, sources = _mapping(clip)
        s0, s1 = np.interp([start, end], times, sources)
        beat_px = (60.0 / analysis.bpm / self.seconds_per_pixel()) if analysis.bpm else 0.0
        if beat_px >= 7.0 and analysis.beats:
            beats = np.asarray(analysis.beats)
            shown = beats[(beats >= s0) & (beats <= s1)]
            line = QColor(color)
            line.setAlpha(34)
            painter.setPen(QPen(line, 1))
            xs = [self.x_of(t) for t in np.interp(shown, sources, times)]
            painter.drawLines([QLineF(x, top, x, top + self.track_height) for x in xs])
        if analysis.downbeats:
            downbeats = np.asarray(analysis.downbeats)
            indices = np.nonzero((downbeats >= s0) & (downbeats <= s1))[0]
            bar_px = beat_px * (analysis.meter_numerator or 4)
            stride = 1 if bar_px >= 10 or not bar_px else max(1, math.ceil(10 / bar_px))
            line = QColor(color)
            line.setAlpha(85)
            painter.setPen(QPen(line, 1))
            label_every = 1 if bar_px >= 44 else 4 if bar_px >= 11 else 0
            for index in indices[::stride]:
                x = self.x_of(float(np.interp(downbeats[index], sources, times)))
                painter.setPen(QPen(line, 1))
                painter.drawLine(QPointF(x, top), QPointF(x, top + self.track_height))
                if label_every and index % label_every == 0:
                    painter.setPen(color.lighter(125))
                    painter.drawText(QRectF(x + 3, top + 2, 40, 13), Qt.AlignmentFlag.AlignLeft, str(index + 1))

    def _paint_waveform(self, painter: QPainter, clip: AudioRenderClip, color: QColor,
                        top: float, left: float, right: float) -> None:
        peaks = self.peaks.get(clip.track_id)
        middle = top + self.track_height / 2 + 6
        amplitude = self.track_height * 0.36
        if peaks is None:
            painter.setPen(QPen(self.palette().mid().color(), 1, Qt.PenStyle.DotLine))
            painter.drawLine(QPointF(left, middle), QPointF(right, middle))
            painter.setPen(self.palette().placeholderText().color())
            loading = clip.track_id not in self.peaks
            painter.drawText(QRectF(self.x_of(clip.timeline_start) + 8, middle - 18, 260, 14), Qt.AlignmentFlag.AlignLeft,
                             ("파형 불러오는 중…" if loading else "파형을 읽지 못했습니다") if self.korean
                             else ("Loading waveform…" if loading else "Could not read the waveform"))
            return
        columns = max(1, int(right - left))
        edges_x = left + np.arange(columns + 1, dtype=np.float64)
        start, end = self.view()
        edges_t = start + (edges_x - left) / max(1.0, right - left) * (end - start)
        times, sources = _mapping(clip)
        edges_s = np.interp(edges_t, times, sources)
        count = len(peaks)
        indices = np.floor(edges_s * PEAKS_PER_SECOND).astype(np.int64)
        valid = (indices[:-1] >= 0) & (indices[:-1] < count)
        if not valid.any():
            return
        clipped = np.clip(indices, 0, count - 1)
        starts = clipped[:-1]
        stops = np.maximum(clipped[1:], starts + 1)
        # min/max of each pixel column's slice; reduceat needs one sorted start list.
        limit = min(count, int(stops.max()))
        mins = np.minimum.reduceat(peaks[:limit, 0], np.minimum(starts, limit - 1))
        maxs = np.maximum.reduceat(peaks[:limit, 1], np.minimum(starts, limit - 1))
        active_left, active_right = self.x_of(clip.timeline_start), self.x_of(clip.timeline_end)
        faint = QColor(self.palette().placeholderText().color())
        faint.setAlpha(70)
        wave = QColor(color)
        wave.setAlpha(215)
        # One pixel column per peak bar, filled in numpy: building a QLineF per column
        # cost several ms a lane, and a maximized window has thousands of columns.
        band_top = math.floor(top)
        height = max(1, math.ceil(top + self.track_height) - band_top)
        y0 = middle - maxs * amplitude - band_top
        y1 = np.maximum(middle - mins * amplitude - band_top, y0 + 1.0)
        rows = np.arange(height, dtype=np.float64)[:, None] + 0.5
        covered = (rows >= np.floor(y0)) & (rows <= np.ceil(y1)) & valid
        centers = edges_x[:-1] + 0.5
        solid = (centers >= active_left) & (centers <= active_right)
        pixels = np.where(solid, _premultiplied(wave), _premultiplied(faint)).astype(np.uint32)
        image_data = np.ascontiguousarray(np.where(covered, pixels, np.uint32(0)))
        image = QImage(image_data.data, columns, height, columns * 4, QImage.Format.Format_ARGB32_Premultiplied)
        painter.drawImage(QPointF(left, band_top), image)

    def _paint_markers(self, painter: QPainter, lane: int, clip: AudioRenderClip, color: QColor, top: float) -> None:
        junction = self.junction
        side = "out" if lane == 0 else "in"
        metrics = painter.fontMetrics()
        labels = sorted((self.x_of(seconds), korean if self.korean else english)
                        for seconds, marker_side, korean, english in junction.markers if marker_side == side)
        widths = [metrics.horizontalAdvance(text) for _x, text in labels]
        rows = stack_labels([(x, 7 + width) for (x, _text), width in zip(labels, widths)])
        for (x, text), width, row in zip(labels, widths, rows):
            y = top + MARKER_TOP + row * MARKER_ROW  # below the bar numbers
            painter.setPen(QPen(color.lighter(140), 1, Qt.PenStyle.DotLine))
            painter.drawLine(QPointF(x, y + 12), QPointF(x, top + self.track_height))
            diamond = QPainterPath(QPointF(x, y + 1))
            for point in ((x + 5, y + 6), (x, y + 11), (x - 5, y + 6)):
                diamond.lineTo(*point)
            diamond.closeSubpath()
            painter.fillPath(diamond, color.lighter(140))
            painter.setPen(color.lighter(150))
            painter.drawText(QRectF(x + 7, y - 1, width + 4, 14), Qt.AlignmentFlag.AlignLeft, text)
        # The cue flag: where A's mix starts / where B starts playing.
        x = self.x_of(junction.start if lane == 0 else clip.timeline_start)
        label = ("A 큐" if self.korean else "A cue") if lane == 0 else ("B 시작" if self.korean else "B start")
        source = clip.source_at(junction.start) if lane == 0 else clip.source_in
        flag = QRectF(x + 1, top + self.track_height - 18, 0, 16)
        text = f"{label} {_clock(source, precise=True)}"
        flag.setWidth(painter.fontMetrics().horizontalAdvance(text) + 10)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 200))
        painter.drawRect(flag)
        painter.setPen(QColor("#111314"))
        painter.drawText(flag, Qt.AlignmentFlag.AlignCenter, text)

    def _handle_color(self, state: str, locked: bool) -> QColor:
        """Idle handles are neutral, hovered ones brighter, selected or dragged ones the accent; kept ones dim."""
        if locked:
            return LOCK_COLOR
        if state in ("drag", "selected"):
            return QColor(self.palette().highlight().color())
        return QColor("#FFFFFF") if state == "hover" else HANDLE_COLOR

    def _paint_lock(self, painter: QPainter, x: float, y: float) -> None:
        """A small padlock (a kept value), its body centred on (x, y)."""
        painter.save()
        painter.setPen(QPen(QColor("#111314"), 1.0))
        painter.setBrush(LOCK_COLOR)
        painter.drawRoundedRect(QRectF(x - 4.5, y - 2, 9, 7), 1.5, 1.5)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(LOCK_COLOR, 1.6))
        painter.drawArc(QRectF(x - 3, y - 7, 6, 8), 0, 180 * 16)
        painter.restore()

    def _paint_handles(self, painter: QPainter, bottom: float) -> None:
        junction = self.junction
        tracks_bottom = self._tracks_bottom()
        if junction.end > junction.start:
            x0, x1 = self.x_of(junction.start), self.x_of(junction.end)
            outline = QColor(HANDLE_COLOR)
            outline.setAlpha(120)
            painter.setPen(QPen(outline, 1.0, Qt.PenStyle.DashLine if self.draft else Qt.PenStyle.SolidLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(x0, self.RULER + 2, x1 - x0, tracks_bottom - self.RULER - 2))
            parts = (("start", x0), ("end", x1))
        else:
            parts = (("move", self.x_of(junction.start)),)
        for part, x in parts:
            state = self._part_state(part)
            locked = any(name in self.locked for name in PART_FIELDS[part])
            color = self._handle_color(state, locked)
            width = 3.0 if state == "idle" else 5.0
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRect(QRectF(x - width / 2, self.RULER, width, tracks_bottom - self.RULER))
            grip = QRectF(x - 8, self.RULER - 16, 16, 16)
            painter.drawRoundedRect(grip, 3, 3)
            if locked:
                self._paint_lock(painter, x, grip.center().y() + 1)
            else:
                painter.setPen(QPen(QColor("#111314"), 1))
                for offset in (-2.5, 0.0, 2.5):
                    painter.drawLine(QPointF(x + offset, grip.top() + 4), QPointF(x + offset, grip.bottom() - 4))
            if state == "selected" and self.hasFocus():
                painter.setPen(QPen(self.palette().text().color(), 1.2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(grip.adjusted(-3, -3, 3, 3), 4, 4)
        if "incoming_cue" in self.locked:
            self._paint_lock(painter, self.x_of(junction.incoming.timeline_start) + 10, self._lane_top(1) + 12)
        ramp = self._ramp_span()
        if ramp is not None:  # where A starts easing onto the new tempo: a dashed line (with a grip in advanced)
            x = self.x_of(ramp[0])
            lane_bottom = self._lane_top(0) + self.track_height
            state = self._part_state("ramp")
            color = (OUTGOING_COLOR.lighter(125) if state == "idle"
                     else self._handle_color(state, False))
            painter.setPen(QPen(color, 2.0 if state != "idle" else 1.3, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(x, self._lane_top(0) + 1), QPointF(x, lane_bottom - 1))
            if not self.advanced:
                ramp = None
        if ramp is not None:
            grip = QRectF(x - 8, lane_bottom - RAMP_GRIP + 2, 16, RAMP_GRIP - 6)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(grip, 3, 3)
            painter.setPen(QPen(QColor("#111314"), 1.3))
            middle = grip.center().y()
            painter.drawLine(QPointF(x - 4, middle), QPointF(x + 4, middle))  # a double arrow: drag sideways
            for side in (-1, 1):
                painter.drawLine(QPointF(x + 4 * side, middle), QPointF(x + 1.5 * side, middle - 2.5))
                painter.drawLine(QPointF(x + 4 * side, middle), QPointF(x + 1.5 * side, middle + 2.5))
            if state == "selected" and self.hasFocus():
                painter.setPen(QPen(self.palette().text().color(), 1.2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(grip.adjusted(-3, -3, 3, 3), 4, 4)
        for part, lane in (("move", 0), ("outgoing", 0), ("incoming", 1)):  # what a drag here would move
            state = self._part_state(part)
            if state not in ("hover", "drag") or (part == "move" and junction.end <= junction.start):
                continue
            clip = junction.incoming if part == "incoming" else junction.outgoing
            x0, x1 = ((self.x_of(junction.start), self.x_of(junction.end)) if part == "move"
                      else (self.x_of(clip.timeline_start), self.x_of(clip.timeline_end)))
            locked = any(name in self.locked for name in PART_FIELDS[part])
            painter.setPen(QPen(self._handle_color(state, locked), 2.0 if state == "drag" else 1.2,
                                Qt.PenStyle.DotLine if state == "hover" else Qt.PenStyle.SolidLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(x0, self._lane_top(lane) + 1, x1 - x0, self.track_height - 2))

    def _part_state(self, part: str) -> str:
        if self._drag is not None and self._drag[0] == part:
            return "drag"
        if self._hover == part:
            return "hover"
        if self.selected_part == part:
            return "selected"
        return "idle"

    def _paint_bands(self, painter: QPainter, lanes, left: float, right: float) -> None:
        palette = self.palette()
        junction = self.junction
        muted = palette.placeholderText().color()
        if not lanes:
            top = self._lane_top(2)
            painter.fillRect(QRectF(left, top, right - left, self.BAND), palette.base())
            painter.setPen(muted)
            painter.drawText(QRectF(left, top, right - left, self.BAND), Qt.AlignmentFlag.AlignCenter,
                             "겹침이 없어 대역 편집이 없습니다 · 컷 전환" if self.korean
                             else "No overlap, so no band lanes · a cut")
            return
        length = max(1e-6, junction.end - junction.start)
        editable = self.editable_bands()
        for lane_index, lane in enumerate(lanes, start=2):
            top = self._lane_top(lane_index)
            rect = QRectF(left, top, right - left, self.BAND)
            painter.fillRect(rect, palette.alternateBase())  # set apart from the track lanes above
            korean, english = _BAND_LABELS.get(lane.key, (lane.korean, lane.english))
            painter.setPen(palette.text().color())
            painter.drawText(QRectF(12, top, self.LABEL - 22, self.BAND / 2), Qt.AlignmentFlag.AlignVCenter,
                             korean if self.korean else english)
            painter.setPen(muted)
            detail = lane.korean.split("\n")[-1] if "\n" in lane.korean else ""
            painter.drawText(QRectF(12, top + self.BAND / 2, self.LABEL - 22, self.BAND / 2),
                             Qt.AlignmentFlag.AlignVCenter, detail)
            curve = QRectF(left, top + 4, right - left, self.BAND - (2 * self.BAR + 14 if editable else 8))
            painter.save()
            painter.setClipRect(rect)
            window = (self._bands[EQ_BANDS.index(lane.key)] if editable and self._bands is not None
                      and lane.key in EQ_BANDS else None)
            if window is not None and band_hole(*window) > 0.005:
                hole = QColor("#EF4444")
                hole.setAlpha(46)
                painter.fillRect(QRectF(self.x_of(junction.start + length * window[0][1]), curve.top(),
                                        self.x_of(junction.start + length * window[1][0])
                                        - self.x_of(junction.start + length * window[0][1]), curve.height()), hole)
            for gain_of, color, entering in ((self._gain(lane, "out"), OUTGOING_COLOR, False),
                                             (self._gain(lane, "in"), INCOMING_COLOR, True)):
                path = QPainterPath()
                steps = 96
                for step in range(steps + 1):
                    seconds = self.seconds_at(left + (right - left) * step / steps)
                    progress = (seconds - junction.start) / length
                    value = gain_of(min(1.0, max(0.0, progress)))
                    point = QPointF(self.x_of(seconds), curve.bottom() - value * curve.height())
                    path.moveTo(point) if step == 0 else path.lineTo(point)
                painter.setPen(QPen(color, 1.8, Qt.PenStyle.DashLine if entering else Qt.PenStyle.SolidLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(path)
            if editable and lane.key in EQ_BANDS:
                for side, color in (("out", OUTGOING_COLOR), ("in", INCOMING_COLOR)):
                    bar = self._band_bar(lane_index, lane.key, side)
                    hot = (self._band_drag is not None and self._band_drag[:2] == (lane.key, side)) or (
                        self._hover is not None and self._hover.startswith(f"band:{lane.key}:{side}"))
                    fill = QColor(color)
                    fill.setAlpha(235 if hot else 150)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(fill)
                    painter.drawRoundedRect(bar, 3, 3)
                    painter.setBrush(color.lighter(135))
                    for edge in (bar.left(), bar.right()):
                        painter.drawRoundedRect(QRectF(edge - 2.5, bar.top() - 2, 5, bar.height() + 4), 2, 2)
            painter.restore()

    def _gain(self, lane, side: str):
        """A lane's gain curve; for a band lane under a drag, from the dragged windows."""
        if self._band_drag is not None and self._bands is not None and lane.key in EQ_BANDS:
            from app.widgets.transition_inspector import _fade

            window = self._bands[EQ_BANDS.index(lane.key)][0 if side == "out" else 1]
            return _fade(window, side == "in")
        return lane.outgoing if side == "out" else lane.incoming
