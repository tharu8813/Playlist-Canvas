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
    QBrush, QColor, QFont, QFontMetrics, QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPen,
    QWheelEvent,
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

HANDLE_COLOR = QColor("#FBBF24")
SNAP_COLOR = QColor("#FDE68A")
_BAND_LABELS = {"high": ("고음", "Highs"), "mid": ("중음", "Mids"), "low": ("저음", "Lows"),
                "level": ("음량", "Level"), "sweep": ("하이패스", "Highpass")}
_PART_TIPS = {
    "start": ("믹스 시작 · 끌어서 위치와 길이 조정 (←/→ 한 박자, Shift: 자유)",
              "Mix start · drag to move it and change the length (←/→ one beat, Shift: free)"),
    "end": ("믹스 끝 · 끌어서 겹침 길이 조정", "Mix end · drag to change the overlap length"),
    "move": ("겹침 구간 · 끌어서 길이는 그대로 옮기기", "Overlap · drag to move it, keeping its length"),
    "incoming": ("B 곡 · 끌어서 들어오는 곡의 시작 지점 조정", "Track B · drag to change where it starts"),
}
PARTS = ("start", "end", "move", "incoming")


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
    """(part, timeline seconds from where the drag began, free: Shift held)."""
    drag_finished = Signal()
    bands_changed = Signal(object)
    nudge_requested = Signal(str, int, bool)
    """(part, direction -1/+1, fine: Shift held)."""

    RULER = 34.0
    TRACK = 96.0
    BAND = 48.0
    GAP = 6.0
    LABEL = 156.0
    RIGHT = 12.0
    GRAB = 9.0
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
        self._hover: str | None = None
        self._press: tuple[str, QPointF] | None = None
        self._drag: tuple[str, float] | None = None
        self._band_drag: tuple[str, str, str, float, BandWindows] | None = None
        self._bands: BandWindows | None = None
        """The band windows drawn, held locally while a band bar is dragged."""
        self._pan: tuple[float, tuple[float, float]] | None = None
        self._view: tuple[float, float] = (0.0, 1.0)
        self._frozen: tuple[float, float] | None = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumWidth(480)
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
        self._update_height()
        self.update()

    def lanes(self):
        """The band lanes shown in advanced mode (``mix_lanes`` of the transition)."""
        if not self.advanced or self.junction is None or self.junction.transition is None:
            return []
        return mix_lanes(self.junction.transition)

    def editable_bands(self) -> bool:
        transition = self.junction.transition if self.junction is not None else None
        return transition is not None and transition_dsp_style(transition) in BAND_ENVELOPES

    def _update_height(self) -> None:
        lanes = len(self.lanes()) if self.advanced else 0
        if self.advanced and lanes == 0:
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
        """What is under (x, y): ("start"|"end"|"move"|"incoming",) or ("band", band, side, kind)."""
        junction = self.junction
        if junction is None or x < self.LABEL:
            return None
        if self.editable_bands():
            for lane_index, lane in enumerate(self.lanes(), start=2):
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
        top, bottom = self.RULER - 14, self._tracks_bottom()
        if not top <= y <= bottom:
            return None
        start, end = self.x_of(junction.start), self.x_of(junction.end)
        if junction.end > junction.start:
            if abs(x - start) <= self.GRAB and (abs(x - start) <= abs(x - end)):
                return ("start",)
            if abs(x - end) <= self.GRAB:
                return ("end",)
        elif abs(x - start) <= self.GRAB and y < self._lane_top(1):
            return ("move",)
        incoming_top = self._lane_top(1)
        if incoming_top <= y <= bottom:
            clip = junction.incoming
            if self.x_of(clip.timeline_start) - 2 <= x <= self.x_of(clip.timeline_end) + 2:
                return ("incoming",)
        if start <= x <= end and y < incoming_top:
            return ("move",)
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
        if hit is not None and hit[0] == "band":
            self._band_drag = (hit[1], hit[2], hit[3], position.x(), self._bands)
            self._frozen = self._view
            return
        if hit is not None:
            self.selected_part = hit[0]
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
            self.setCursor(Qt.CursorShape.SizeHorCursor if part in ("start", "end")
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

    def _hover_at(self, position: QPointF, global_position) -> None:
        hit = self.hit(position.x(), position.y())
        hover = None if hit is None else ":".join(hit)
        if hover != self._hover:
            self._hover = hover
            if hit is None:
                self.unsetCursor()
            elif hit[0] in ("start", "end") or (hit[0] == "band" and hit[3] != "move"):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            else:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.update()
        if hit is not None and hit[0] in _PART_TIPS:
            QToolTip.showText(global_position, _PART_TIPS[hit[0]][0 if self.korean else 1], self)
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
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right) and not event.modifiers() & (
                Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ControlModifier):
            direction = -1 if key == Qt.Key.Key_Left else 1
            if self.selected_part is not None:
                self.nudge_requested.emit(self.selected_part, direction, fine)
            else:
                self.pan(0.1 * direction)
        elif key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            order = [None, *PARTS]
            index = order.index(self.selected_part) if self.selected_part in order else 0
            self.selected_part = order[(index + (1 if key == Qt.Key.Key_Down else -1)) % len(order)]
            self.update()
        elif key == Qt.Key.Key_Escape and self.selected_part is not None:
            self.selected_part = None
            self.update()
        else:
            super().keyPressEvent(event)

    def _dragged_bands(self, x: float, free: bool) -> BandWindows:
        band, side, kind, press_x, base = self._band_drag
        junction = self.junction
        length = max(1e-6, junction.end - junction.start)
        delta = (self.seconds_at(x) - self.seconds_at(press_x)) / length
        out_window, in_window = base[EQ_BANDS.index(band)]
        own = out_window if side == "out" else in_window
        if not free:  # the moving edge clicks onto a beat, or the window's start, middle or end
            anchor = own[1] if kind == "end" else own[0]
            reach = 8.0 * self.seconds_per_pixel() / length
            points = [0.0, 0.5, 1.0]
            incoming = self.analyses[1]
            if incoming is not None and incoming.bpm:
                beat = 60.0 / incoming.bpm / length
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

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        painter.fillRect(self.rect(), palette.window())
        small = QFont(self.font())
        small.setPointSizeF(max(7.5, small.pointSizeF() - 0.5))
        painter.setFont(small)
        junction = self.junction
        if junction is None:
            painter.setPen(palette.placeholderText().color())
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
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
        painter.save()
        painter.setClipRect(QRectF(left, 0, right - left, self.height()))
        if junction.end > junction.start:  # the overlap, tinted over every lane
            band = QColor(palette.highlight().color())
            band.setAlpha(22)
            painter.fillRect(QRectF(self.x_of(junction.start), self.RULER,
                                    self.x_of(junction.end) - self.x_of(junction.start), bottom - self.RULER), band)
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
        if self.drag_hint and self.dragging:
            metrics = painter.fontMetrics()
            width = metrics.horizontalAdvance(self.drag_hint) + 18
            box = QRectF(max(left, right - width), 2, width, 22)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(12, 14, 16, 225))
            painter.drawRoundedRect(box, 5, 5)
            painter.setPen(SNAP_COLOR)
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, self.drag_hint)
        if self.hasFocus():
            painter.setPen(QPen(palette.highlight().color(), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1))

    def _paint_ruler(self, painter: QPainter, left: float, right: float) -> None:
        palette = self.palette()
        muted = palette.placeholderText().color()
        painter.fillRect(QRectF(left, 0, right - left, self.RULER), palette.base())
        start, end = self.view()
        if self.audition is not None:
            a, b = self.audition
            x0, x1 = max(left, self.x_of(a)), min(right, self.x_of(b))
            if x1 > x0:
                color = QColor(palette.highlight().color())
                color.setAlpha(170 if self.loop else 80)
                painter.fillRect(QRectF(x0, self.RULER - 5, x1 - x0, 4), color)
        painter.setPen(muted)
        painter.drawText(QRectF(8, 0, self.LABEL - 16, self.RULER), Qt.AlignmentFlag.AlignVCenter,
                         "믹스 시간" if self.korean else "Mix time")
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
            r0, r1 = clip.timeline_at(clip.tempo_ramp.source_start), clip.timeline_at(clip.tempo_ramp.source_end)
            hatch = QBrush(color.lighter(120), Qt.BrushStyle.BDiagPattern)
            painter.fillRect(QRectF(self.x_of(r0), top + self.track_height - 14, self.x_of(r1) - self.x_of(r0), 10), hatch)
            bpm = analysis.bpm * clip.tempo_ramp.end_rate if analysis is not None and analysis.bpm else None
            painter.setPen(color.lighter(130))
            label_x = min(max(self.x_of(r0), left) + 4, max(left + 4, self.x_of(r1) - 200))
            painter.drawText(QRectF(label_x, top + self.track_height - 30, 220, 14), Qt.AlignmentFlag.AlignLeft,
                             ("템포 램프" if self.korean else "Tempo ramp") + (f" → {bpm:.1f} BPM" if bpm else ""))
        painter.setPen(QPen(color, 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(active, 3, 3)
        self._paint_markers(painter, lane, clip, color, top)
        painter.restore()

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
        painter.setPen(palette.placeholderText().color())
        role = ("나가는 곡" if lane == 0 else "들어오는 곡") if self.korean else ("Outgoing" if lane == 0 else "Incoming")
        facts = [role]
        if analysis is not None and analysis.bpm:
            facts.append(f"{analysis.bpm:.1f} BPM")
        if analysis is not None and analysis.key:
            facts.append(analysis.key)
        painter.drawText(QRectF(12, top + 38, self.LABEL - 22, 18), Qt.AlignmentFlag.AlignVCenter, " · ".join(facts))
        if analysis is None:
            painter.drawText(QRectF(12, top + 56, self.LABEL - 22, 18), Qt.AlignmentFlag.AlignVCenter,
                             "박자 분석 없음" if self.korean else "No beat analysis")

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
        solid, ghost = [], []
        for column in np.nonzero(valid)[0]:
            x = float(edges_x[column]) + 0.5
            y0 = middle - float(maxs[column]) * amplitude
            y1 = middle - float(mins[column]) * amplitude
            (solid if active_left <= x <= active_right else ghost).append(QLineF(x, y0, x, max(y1, y0 + 1)))
        faint = QColor(self.palette().placeholderText().color())
        faint.setAlpha(70)
        painter.setPen(QPen(faint, 1))
        painter.drawLines(ghost)
        wave = QColor(color)
        wave.setAlpha(215)
        painter.setPen(QPen(wave, 1))
        painter.drawLines(solid)

    def _paint_markers(self, painter: QPainter, lane: int, clip: AudioRenderClip, color: QColor, top: float) -> None:
        junction = self.junction
        side = "out" if lane == 0 else "in"
        for seconds, marker_side, korean, english in junction.markers:
            if marker_side != side:
                continue
            x = self.x_of(seconds)
            painter.setPen(QPen(color.lighter(140), 1, Qt.PenStyle.DotLine))
            painter.drawLine(QPointF(x, top + 16), QPointF(x, top + self.track_height))
            diamond = QPainterPath(QPointF(x, top + 3))
            for point in ((x + 5, top + 8), (x, top + 13), (x - 5, top + 8)):
                diamond.lineTo(*point)
            diamond.closeSubpath()
            painter.fillPath(diamond, color.lighter(140))
            painter.setPen(color.lighter(150))
            painter.drawText(QRectF(x + 7, top + 1, 150, 14), Qt.AlignmentFlag.AlignLeft,
                             korean if self.korean else english)
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

    def _paint_handles(self, painter: QPainter, bottom: float) -> None:
        junction = self.junction
        tracks_bottom = self._tracks_bottom()
        outline = QColor(HANDLE_COLOR)
        outline.setAlpha(210)
        if junction.end > junction.start:
            x0, x1 = self.x_of(junction.start), self.x_of(junction.end)
            painter.setPen(QPen(outline, 1.3, Qt.PenStyle.DashLine if self.draft else Qt.PenStyle.SolidLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(x0, self.RULER + 2, x1 - x0, tracks_bottom - self.RULER - 2))
            parts = (("start", x0), ("end", x1))
        else:
            parts = (("move", self.x_of(junction.start)),)
        for part, x in parts:
            state = self._part_state(part)
            color = HANDLE_COLOR.lighter(125) if state in ("hover", "drag", "selected") else HANDLE_COLOR
            width = 4.0 if state == "idle" else 6.0
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRect(QRectF(x - width / 2, self.RULER, width, tracks_bottom - self.RULER))
            grip = QRectF(x - 7, self.RULER - 15, 14, 15)
            painter.drawRoundedRect(grip, 3, 3)
            painter.setPen(QPen(QColor("#111314"), 1))
            for offset in (-2.5, 0.0, 2.5):
                painter.drawLine(QPointF(x + offset, grip.top() + 4), QPointF(x + offset, grip.bottom() - 4))
            if state == "selected" and self.hasFocus():
                painter.setPen(QPen(self.palette().text().color(), 1.2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(grip.adjusted(-3, -3, 3, 3), 4, 4)
        for part, lane in (("move", 0), ("incoming", 1)):
            state = self._part_state(part)
            if state == "idle" or (part == "move" and junction.end <= junction.start):
                continue
            clip = junction.incoming
            x0, x1 = ((self.x_of(junction.start), self.x_of(junction.end)) if part == "move"
                      else (self.x_of(clip.timeline_start), self.x_of(clip.timeline_end)))
            painter.setPen(QPen(HANDLE_COLOR.lighter(120), 2.0 if state == "drag" else 1.4))
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
