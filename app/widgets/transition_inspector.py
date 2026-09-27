"""Preview's "Transition details" window: every junction of the playing plan, drawn.

Opened from AutoMixDetailsPanel. Read-only like the panel: it draws the
CompiledRenderPlan Preview is playing -- where each overlap sits, how the
renderer blends it (per band, with the same envelopes build_filter_graph
renders), where the Canvas hands over, and the planner's cue/vocal/structure
facts -- and never analyzes, plans or renders audio itself.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QGuiApplication, QKeySequence, QMouseEvent, QPainter, QPainterPath,
    QPen, QShortcut,
)
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget, QMenu,
    QPlainTextEdit, QPushButton, QScrollArea, QSlider, QSplitter, QStyle, QStyledItemDelegate,
    QStyleOptionSlider, QStyleOptionViewItem, QToolButton, QToolTip, QVBoxLayout, QWidget,
)

from app.automix.diagnostics import rows_to_json
from app.automix.renderer import (
    BAND_ENVELOPES, DROP_IN_ATTACK_SECONDS, HIGH_CROSSOVER_HZ, LOW_CROSSOVER_HZ,
    SWEEP_SHAPES, _CURVE_BY_TRANSITION_TYPE, _band_envelope, transition_dsp_style,
)
from app.timeline.render_plan import (
    AudioRenderClip, AudioRenderTransition, CompiledRenderPlan, TransitionDsp, visual_segments,
)

OUTGOING_COLOR = QColor("#F59E0B")
INCOMING_COLOR = QColor("#38BDF8")
PLAYHEAD_COLOR = QColor("#EF4444")

_STYLE_DESCRIPTIONS = {
    "bass_swap": ("저음을 겹침 가운데에서 짧게 맞바꿔 두 베이스가 동시에 울리지 않게 합니다. 중·고음은 부드럽게 교차합니다.",
                  "Swaps the lows quickly mid-overlap so two bass lines never play together; mids and highs cross smoothly."),
    "vocal_safe_eq": ("저음에 이어 중음(보컬 대역)도 짧게 맞바꿔 두 보컬이 겹치는 시간을 최소화합니다.",
                      "Swaps the lows, then the mids (the vocal band), so two voices barely overlap."),
    "filter_blend": ("들어오는 곡은 고음부터, 나가는 곡은 고음을 가장 늦게까지 남기는 3대역 블렌드입니다.",
                     "A 3-band blend: the incoming track arrives highs-first and the outgoing one keeps its highs longest."),
    "short_fade": ("대역 분리 없이 전체 음량을 짧게 교차합니다.", "A short full-band equal-power crossfade."),
    "filter_sweep": ("나가는 곡의 저음을 하이패스 필터로 서서히 걷어내고, 들어오는 곡은 고음부터 채워 넣는 DJ식 스윕입니다.",
                     "A DJ-style sweep: a highpass lifts the outgoing lows away while the incoming track fills in from the highs."),
    "drop_in": ("나가는 곡의 여운이 이미 잦아드는 중이라, 들어오는 곡을 처음부터 제 음량으로 시작합니다.",
                "The outgoing tail is already fading, so the incoming track starts at full level over it."),
    "legacy": ("두 곡의 음량을 전체 대역에서 교차하는 기본 크로스페이드입니다.",
               "A plain crossfade of the full signal."),
    "sequential": ("섞지 않고 앞 곡이 끝나자마자 다음 곡을 이어 재생합니다.",
                   "Not mixed: the next track starts right after the previous one ends."),
    "gap": ("두 곡 사이에 무음 간격이 있습니다.", "There is a silent gap between the two tracks."),
}


# -- pure model -----------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class MixLane:
    """One drawn band of a transition: each side's gain (0..1) at overlap progress 0..1."""

    key: str
    korean: str
    english: str
    outgoing: Callable[[float], float]
    incoming: Callable[[float], float]


def _fade(window: tuple[float, float], entering: bool, curve: str = "qsin") -> Callable[[float], float]:
    """The renderer's afade/acrossfade shape over ``window`` of the overlap."""
    start, end = window

    def gain(progress: float) -> float:
        if end <= start:
            amount = 1.0 if progress >= end else 0.0
        else:
            amount = min(1.0, max(0.0, (progress - start) / (end - start)))
        if curve == "qsin":
            return math.sin(amount * math.pi / 2) if entering else math.cos(amount * math.pi / 2)
        return amount if entering else 1.0 - amount

    return gain


def mix_lanes(transition: AudioRenderTransition) -> list[MixLane]:
    """How the renderer blends ``transition``, as drawable per-band gain curves."""
    style = transition_dsp_style(transition)
    whole = (0.0, 1.0)
    if style in BAND_ENVELOPES:
        names = {
            "low": ("저음", "Lows"),
            "mid": ("중음", "Mids"),
            "high": ("고음", "Highs"),
        }
        ranges = {
            "low": f"< {LOW_CROSSOVER_HZ} Hz",
            "mid": f"{LOW_CROSSOVER_HZ}–{HIGH_CROSSOVER_HZ} Hz",
            "high": f"> {HIGH_CROSSOVER_HZ} Hz",
        }
        lanes = []
        for band in ("high", "mid", "low"):
            out_window, in_window = _band_envelope(style, band, transition.vocal_handoff)
            korean, english = names[band]
            lanes.append(MixLane(
                band, f"{korean}\n{ranges[band]}", f"{english}\n{ranges[band]}",
                _fade(out_window, False), _fade(in_window, True),
            ))
        return lanes
    if style is TransitionDsp.FILTER_SWEEP:
        (out_sweep, out_level), (in_sweep, in_level) = SWEEP_SHAPES["out"], SWEEP_SHAPES["in"]
        return [
            MixLane("level", "음량", "Level", _fade(out_level, False), _fade(in_level, True)),
            # The cutoff moves exponentially in Hz, i.e. linearly on a log axis.
            MixLane("sweep", "남은 저음\n(하이패스)", "Lows left\n(highpass)",
                    _fade(out_sweep, False, "tri"), _fade(in_sweep, True, "tri")),
        ]
    if style is TransitionDsp.DROP_IN:
        attack = min(1.0, DROP_IN_ATTACK_SECONDS / max(transition.duration, 1e-6))
        return [MixLane("level", "음량", "Level", _fade(whole, False), _fade((0.0, attack), True))]
    curve = "qsin" if style is TransitionDsp.SHORT_FADE else _CURVE_BY_TRANSITION_TYPE.get(transition.type, "tri")
    return [MixLane("level", "음량", "Level", _fade(whole, False, curve), _fade(whole, True, curve))]


@dataclass(frozen=True, slots=True)
class Junction:
    """One junction of the plan in timeline seconds, ready to draw."""

    index: int
    outgoing: AudioRenderClip
    incoming: AudioRenderClip
    transition: AudioRenderTransition | None
    start: float
    end: float
    handover: float
    markers: tuple[tuple[float, str, str, str], ...]
    """(timeline seconds, side "out"/"in"/"", Korean label, English label)."""
    ramp: tuple[float, float] | None


def plan_junctions(plan: CompiledRenderPlan) -> list[Junction]:
    """Every junction between consecutive clips, with its markers on the timeline."""
    clips = plan.audio.clips
    by_pair = {(t.clip_a, t.clip_b): t for t in plan.audio.transitions}
    segments = visual_segments(plan)
    junctions = []
    for index, (outgoing, incoming) in enumerate(zip(clips, clips[1:])):
        transition = by_pair.get((outgoing.clip_id, incoming.clip_id))
        handover = segments[index].end if index < len(segments) else None
        junctions.append(make_junction(index + 1, outgoing, incoming, transition, handover))
    return junctions


def make_junction(
    number: int, outgoing: AudioRenderClip, incoming: AudioRenderClip,
    transition: AudioRenderTransition | None, handover: float | None = None,
) -> Junction:
    """One drawable junction; ``handover`` defaults to the overlap's middle (visual_segments' rule)."""
    mixed = transition is not None and transition.duration > 0.0
    start = transition.timeline_start if mixed else incoming.timeline_start
    end = start + transition.duration if mixed else start
    if handover is None:
        handover = (start + end) / 2.0
    details = dict(transition.details) if transition is not None else {}

    def place(clip: AudioRenderClip, source_seconds: object) -> float | None:
        if not isinstance(source_seconds, (int, float)) or isinstance(source_seconds, bool):
            return None
        if not clip.source_in - 1e-6 <= float(source_seconds) <= clip.source_out + 1e-6:
            return None
        return clip.timeline_at(float(source_seconds))

    markers = []
    for clip, key, side, korean, english in (
        (outgoing, "outgoing_vocal_outro_start", "out", "마지막 보컬 끝", "Last vocal ends"),
        (outgoing, "outgoing_structure_anchor", "out", "아웃트로", "Outro"),
        (incoming, "incoming_vocal_intro_end", "in", "첫 보컬 시작", "First vocal"),
        (incoming, "incoming_structure_anchor", "in", "인트로 끝", "Intro ends"),
    ):
        seconds = place(clip, details.get(key))
        if seconds is not None:
            markers.append((seconds, side, korean, english))
    ramp = None
    if outgoing.tempo_ramp is not None:
        ramp = (outgoing.timeline_at(outgoing.tempo_ramp.source_start),
                outgoing.timeline_at(outgoing.tempo_ramp.source_end))
    return Junction(
        number, outgoing, incoming, transition if mixed else None,
        start, end, handover, tuple(markers), ramp,
    )


def diagram_range(junction: Junction) -> tuple[float, float]:
    """The seconds a transition diagram shows: the overlap plus context on both sides."""
    length = junction.end - junction.start
    pad = max(3.0, length * 0.35) if length > 0.0 else 8.0
    return junction.start - pad, junction.end + pad


def _clock(seconds: float, precise: bool = False) -> str:
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    minutes = int(seconds // 60)
    rest = seconds - minutes * 60
    return f"{sign}{minutes}:{rest:04.1f}" if precise else f"{sign}{minutes}:{int(rest):02d}"


def _nice_step(span: float, pixels: float) -> float:
    target = span / max(1.0, pixels / 90.0)
    for step in (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600):
        if step >= target:
            return float(step)
    return 1200.0


# -- drawing ----------------------------------------------------------------------

class MixOverviewStrip(QWidget):
    """The whole playlist: clips on two alternating lanes, overlaps, the playhead."""

    selected = Signal(int)
    seek_requested = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.plan: CompiledRenderPlan | None = None
        self.junctions: list[Junction] = []
        self.titles: dict[str, str] = {}
        self.current = -1
        self.playhead = 0.0
        self.korean = True
        self.setMinimumHeight(74)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self) -> QSize:
        return QSize(640, 78)

    def _x(self, seconds: float) -> float:
        duration = max(1e-6, self.plan.duration_seconds if self.plan else 1.0)
        return 8.0 + (self.width() - 16.0) * seconds / duration

    def _seconds(self, x: float) -> float:
        duration = self.plan.duration_seconds if self.plan else 0.0
        return max(0.0, min(duration, (x - 8.0) / max(1.0, self.width() - 16.0) * duration))

    def _junction_at(self, x: float) -> int:
        if not self.junctions:
            return -1
        return min(range(len(self.junctions)),
                   key=lambda i: abs(self._x((self.junctions[i].start + self.junctions[i].end) / 2) - x))

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        painter.fillRect(self.rect(), palette.base())
        if self.plan is None or not self.plan.audio.clips:
            return
        lane_height, top = 20.0, 10.0
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.5))
        painter.setFont(font)
        for index, clip in enumerate(self.plan.audio.clips):
            lane_top = top + (index % 2) * (lane_height + 6)
            rect = QRectF(self._x(clip.timeline_start), lane_top,
                          max(2.0, self._x(clip.timeline_end) - self._x(clip.timeline_start)), lane_height)
            color = QColor(palette.mid().color())
            color.setAlpha(150)
            painter.setPen(QPen(palette.dark().color(), 1))
            painter.setBrush(color)
            painter.drawRoundedRect(rect, 4, 4)
            painter.setPen(palette.text().color())
            label = f"{index + 1:02d} {self.titles.get(clip.track_id, '')}"
            painter.drawText(rect.adjusted(5, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter,
                             painter.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight,
                                                              max(0, int(rect.width() - 9))))
        highlight = palette.highlight().color()
        for index, junction in enumerate(self.junctions):
            left, right = self._x(junction.start), self._x(junction.end)
            rect = QRectF(left, top - 4, max(3.0, right - left), lane_height * 2 + 14)
            fill = QColor(highlight)
            fill.setAlpha(110 if index == self.current else 55)
            painter.setBrush(fill)
            painter.setPen(QPen(highlight, 2.0 if index == self.current else 0.0))
            painter.drawRoundedRect(rect, 3, 3)
        x = self._x(self.playhead)
        painter.setPen(QPen(PLAYHEAD_COLOR, 2))
        painter.drawLine(QPointF(x, 2), QPointF(x, self.height() - 12))
        painter.setPen(palette.placeholderText().color())
        painter.drawText(QRectF(8, self.height() - 14, 80, 14), Qt.AlignmentFlag.AlignLeft, "0:00")
        painter.drawText(QRectF(self.width() - 88, self.height() - 14, 80, 14), Qt.AlignmentFlag.AlignRight,
                         _clock(self.plan.duration_seconds))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        index = self._junction_at(event.position().x())
        if index >= 0:
            self.selected.emit(index)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self.seek_requested.emit(self._seconds(event.position().x()))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        index = self._junction_at(event.position().x())
        if index < 0:
            return
        junction = self.junctions[index]
        hint = ("클릭: 전환 선택 · 더블클릭: 이 위치로 이동" if self.korean
                else "Click: select transition · Double-click: seek here")
        QToolTip.showText(event.globalPosition().toPoint(),
                          f"{junction.index:02d} → {junction.index + 1:02d} · {_clock(junction.start)}\n{hint}", self)


class TransitionDiagram(QWidget):
    """One junction up close: both clips, each band's gain curves, markers, the playhead.

    With ``editable`` (a manual transition) the overlap and the incoming clip
    can be dragged: ``drag_moved(kind, delta_seconds, free)`` reports how far
    from where the drag began (``kind`` is "move", "start", "end" or
    "incoming"; ``free``: Shift held, no snapping) and the window re-plans.
    """

    seek_requested = Signal(float)
    drag_started = Signal(str)
    drag_moved = Signal(str, float, bool)
    drag_finished = Signal()
    EDGE_GRAB = 7.0

    LABEL_WIDTH = 104.0
    MARKER_AREA = 44.0
    CLIP_LANE = 24.0
    ENVELOPE_LANE = 58.0
    AXIS = 24.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.junction: Junction | None = None
        self.lanes: list[MixLane] = []
        self.titles: dict[str, str] = {}
        self.playhead = 0.0
        self.korean = True
        self.editable = False
        self.draft = False
        """Drawn from an edit that is not mixed yet (drawn dashed)."""
        self.beat_marks: tuple[tuple[float, ...], tuple[float, ...]] = ((), ())
        """Timeline seconds of each clip's bars (outgoing, incoming), drawn as ticks while editing."""
        self._drag: tuple[str, float] | None = None
        self._frozen_range: tuple[float, float] | None = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_height()

    def set_junction(self, junction: Junction | None) -> None:
        self.junction = junction
        self.lanes = mix_lanes(junction.transition) if junction and junction.transition else []
        self._update_height()
        self.update()

    def _update_height(self) -> None:
        lanes = max(1, len(self.lanes))
        self.setMinimumHeight(int(self.MARKER_AREA + 2 * (self.CLIP_LANE + 6)
                                  + lanes * (self.ENVELOPE_LANE + 8) + self.AXIS + 8))

    def sizeHint(self) -> QSize:
        return QSize(720, self.minimumHeight())

    def _range(self) -> tuple[float, float]:
        if self._frozen_range is not None:  # the scale must not move under a drag
            return self._frozen_range
        return diagram_range(self.junction) if self.junction else (0.0, 1.0)

    def _x(self, seconds: float) -> float:
        start, end = self._range()
        left = self.LABEL_WIDTH
        return left + (self.width() - left - 14.0) * (seconds - start) / max(1e-6, end - start)

    def _seconds(self, x: float) -> float:
        start, end = self._range()
        left = self.LABEL_WIDTH
        return start + (x - left) / max(1.0, self.width() - left - 14.0) * (end - start)

    def _label(self, korean: str, english: str) -> str:
        return korean if self.korean else english

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        painter.fillRect(self.rect(), palette.base())
        junction = self.junction
        if junction is None:
            return
        text_color = palette.text().color()
        muted = palette.placeholderText().color()
        small = QFont(self.font())
        small.setPointSizeF(max(7.0, small.pointSizeF() - 1.0))
        painter.setFont(small)
        start, end = self._range()
        plot_left, plot_right = self.LABEL_WIDTH, self.width() - 14.0
        top = self.MARKER_AREA
        bottom = self.height() - self.AXIS

        def clamp_x(seconds: float) -> float:
            return min(plot_right, max(plot_left, self._x(seconds)))

        # Overlap band behind everything.
        if junction.end > junction.start:
            band = QColor(palette.highlight().color())
            band.setAlpha(34)
            painter.fillRect(QRectF(clamp_x(junction.start), top - 4,
                                    clamp_x(junction.end) - clamp_x(junction.start), bottom - top + 4), band)

        # Clip lanes.
        y = top
        for clip, color, korean, english in (
            (junction.outgoing, OUTGOING_COLOR, "나가는 곡", "Outgoing"),
            (junction.incoming, INCOMING_COLOR, "들어오는 곡", "Incoming"),
        ):
            painter.setPen(muted)
            painter.drawText(QRectF(6, y, self.LABEL_WIDTH - 12, self.CLIP_LANE),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                             self._label(korean, english))
            left, right = clamp_x(clip.timeline_start), clamp_x(clip.timeline_end)
            if right > left:
                fill = QColor(color)
                fill.setAlpha(90)
                rect = QRectF(left, y + 2, right - left, self.CLIP_LANE - 4)
                painter.setPen(QPen(color, 1.2))
                painter.setBrush(fill)
                painter.drawRoundedRect(rect, 4, 4)
                painter.setPen(text_color)
                title = self.titles.get(clip.track_id, clip.track_id)
                painter.drawText(rect.adjusted(6, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter,
                                 painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight,
                                                                  max(0, int(rect.width() - 10))))
            if clip is junction.outgoing and junction.ramp is not None:
                ramp_left, ramp_right = clamp_x(junction.ramp[0]), clamp_x(junction.ramp[1])
                if ramp_right > ramp_left:
                    hatch = QBrush(color.darker(140), Qt.BrushStyle.BDiagPattern)
                    painter.fillRect(QRectF(ramp_left, y + 2, ramp_right - ramp_left, self.CLIP_LANE - 4), hatch)
            y += self.CLIP_LANE + 6
        if self.editable:
            self._draw_edit_overlay(painter, top, clamp_x)

        # Envelope lanes.
        for lane in self.lanes:
            lane_rect = QRectF(plot_left, y, plot_right - plot_left, self.ENVELOPE_LANE)
            painter.setPen(muted)
            painter.drawText(QRectF(6, y, self.LABEL_WIDTH - 12, self.ENVELOPE_LANE),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                             self._label(lane.korean, lane.english))
            grid = QColor(palette.mid().color())
            grid.setAlpha(120)
            painter.setPen(QPen(grid, 1, Qt.PenStyle.DotLine))
            painter.drawLine(QPointF(plot_left, y), QPointF(plot_right, y))
            painter.drawLine(QPointF(plot_left, lane_rect.bottom()), QPointF(plot_right, lane_rect.bottom()))
            for gain, color in ((lane.outgoing, OUTGOING_COLOR), (lane.incoming, INCOMING_COLOR)):
                self._draw_curve(painter, lane_rect, gain, color, gain is lane.outgoing)
            y += self.ENVELOPE_LANE + 8
        if not self.lanes:
            painter.setPen(muted)
            painter.drawText(QRectF(plot_left, y, plot_right - plot_left, self.ENVELOPE_LANE),
                             Qt.AlignmentFlag.AlignCenter,
                             self._label("겹치는 구간이 없습니다 · 곡이 차례로 이어집니다",
                                         "No overlap · the tracks play one after another"))

        # Markers: overlap edges, the Canvas handover, planner facts, the playhead.
        markers: list[tuple[float, QColor, str, Qt.PenStyle]] = []
        if junction.end > junction.start:
            markers += [
                (junction.start, muted, self._label("믹스 시작", "Mix starts"), Qt.PenStyle.SolidLine),
                (junction.end, muted, self._label("믹스 끝", "Mix ends"), Qt.PenStyle.SolidLine),
            ]
        markers.append((junction.handover, palette.highlight().color(),
                         self._label("▣ 화면 전환", "▣ Canvas switch"), Qt.PenStyle.DashLine))
        for seconds, side, korean, english in junction.markers:
            color = OUTGOING_COLOR if side == "out" else INCOMING_COLOR
            markers.append((seconds, color, ("◆ " if side == "out" else "◇ ") + self._label(korean, english),
                            Qt.PenStyle.DotLine))
        rows_end: list[float] = [-1e9, -1e9]
        for seconds, color, label, style in sorted(markers, key=lambda m: m[0]):
            if not start <= seconds <= end:
                continue
            x = self._x(seconds)
            painter.setPen(QPen(color, 1.4, style))
            painter.drawLine(QPointF(x, top - 6), QPointF(x, bottom))
            width = painter.fontMetrics().horizontalAdvance(label) + 6
            row = 0 if x - width / 2 > rows_end[0] else 1
            label_left = min(max(plot_left, x - width / 2), plot_right - width)
            rows_end[row] = label_left + width
            painter.setPen(color if color != muted else text_color)
            painter.drawText(QRectF(label_left, 4 + row * 17, width, 16), Qt.AlignmentFlag.AlignCenter, label)
        if start <= self.playhead <= end:
            x = self._x(self.playhead)
            painter.setPen(QPen(PLAYHEAD_COLOR, 2))
            painter.drawLine(QPointF(x, top - 6), QPointF(x, bottom))
            triangle = QPainterPath(QPointF(x - 5, top - 12))
            triangle.lineTo(x + 5, top - 12)
            triangle.lineTo(x, top - 5)
            triangle.closeSubpath()
            painter.fillPath(triangle, PLAYHEAD_COLOR)

        # Time axis.
        painter.setPen(muted)
        painter.drawLine(QPointF(plot_left, bottom + 2), QPointF(plot_right, bottom + 2))
        step = _nice_step(end - start, plot_right - plot_left)
        tick = math.ceil(start / step) * step
        while tick <= end:
            x = self._x(tick)
            painter.drawLine(QPointF(x, bottom + 2), QPointF(x, bottom + 6))
            painter.drawText(QRectF(x - 40, bottom + 6, 80, 16), Qt.AlignmentFlag.AlignHCenter,
                             _clock(tick, precise=step < 1.0))
            tick += step

    def _draw_edit_overlay(self, painter: QPainter, top: float, clamp_x: Callable[[float], float]) -> None:
        """Bar ticks on both clips, and grab handles on the overlap's edges."""
        junction = self.junction
        start, end = self._range()
        for lane, (marks, color) in enumerate(zip(self.beat_marks, (OUTGOING_COLOR, INCOMING_COLOR))):
            y = top + lane * (self.CLIP_LANE + 6)
            tick = QColor(color)
            tick.setAlpha(150)
            painter.setPen(QPen(tick, 1))
            for seconds in marks:
                if start <= seconds <= end:
                    x = self._x(seconds)
                    painter.drawLine(QPointF(x, y + self.CLIP_LANE - 8), QPointF(x, y + self.CLIP_LANE - 2))
        accent = QColor("#FBBF24")
        lanes_bottom = top + 2 * self.CLIP_LANE + 6
        if junction.end > junction.start:
            outline = QColor(accent)
            outline.setAlpha(200)
            painter.setPen(QPen(outline, 1.4, Qt.PenStyle.DashLine if self.draft else Qt.PenStyle.SolidLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(clamp_x(junction.start), top - 3,
                                    clamp_x(junction.end) - clamp_x(junction.start), lanes_bottom - top + 6))
            for seconds in (junction.start, junction.end):
                x = clamp_x(seconds)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(accent)
                painter.drawRoundedRect(QRectF(x - 4, top + self.CLIP_LANE - 9, 8, 24), 3, 3)
        else:
            x = clamp_x(junction.start)
            painter.setPen(QPen(accent, 2))
            painter.drawLine(QPointF(x, top - 3), QPointF(x, lanes_bottom + 3))

    def _edit_hit(self, x: float, y: float) -> str | None:
        """Which part of a manual junction is under (x, y), if any."""
        junction = self.junction
        if not self.editable or junction is None or x < self.LABEL_WIDTH:
            return None
        top = self.MARKER_AREA
        incoming_top = top + self.CLIP_LANE + 6
        bottom = self.height() - self.AXIS
        if not top - 4 <= y <= bottom:
            return None
        left, right = self._x(junction.start), self._x(junction.end)
        if junction.end > junction.start:
            if abs(x - left) <= self.EDGE_GRAB:
                return "start"
            if abs(x - right) <= self.EDGE_GRAB:
                return "end"
        elif abs(x - left) <= self.EDGE_GRAB and y < incoming_top:
            return "move"
        if incoming_top <= y <= incoming_top + self.CLIP_LANE:
            clip = junction.incoming
            if self._x(clip.timeline_start) - 2 <= x <= self._x(clip.timeline_end) + 2:
                return "incoming"
        if left <= x <= right:
            return "move"
        return None

    def _draw_curve(self, painter: QPainter, rect: QRectF, gain: Callable[[float], float],
                    color: QColor, outgoing: bool) -> None:
        junction = self.junction
        start, end = self._range()
        length = max(1e-6, junction.end - junction.start)
        clip = junction.outgoing if outgoing else junction.incoming
        steps = 160
        path = QPainterPath()
        points = []
        for step in range(steps + 1):
            seconds = start + (end - start) * step / steps
            if not clip.timeline_start - 1e-6 <= seconds <= clip.timeline_end + 1e-6:
                value = 0.0
            elif junction.end <= junction.start:
                value = 1.0
            else:
                value = gain(min(1.0, max(0.0, (seconds - junction.start) / length)))
            points.append(QPointF(self._x(seconds), rect.bottom() - value * (rect.height() - 4)))
        path.moveTo(QPointF(points[0].x(), rect.bottom()))
        for point in points:
            path.lineTo(point)
        path.lineTo(QPointF(points[-1].x(), rect.bottom()))
        fill = QColor(color)
        fill.setAlpha(60)
        painter.fillPath(path, fill)
        line = QPainterPath(points[0])
        for point in points[1:]:
            line.lineTo(point)
        painter.setPen(QPen(color, 2, Qt.PenStyle.SolidLine if outgoing else Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(line)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        position = event.position()
        kind = self._edit_hit(position.x(), position.y())
        if kind is not None and event.button() == Qt.MouseButton.LeftButton:
            self._frozen_range = self._range()
            self._drag = (kind, self._seconds(position.x()))
            self.setCursor(Qt.CursorShape.SizeHorCursor if kind in ("start", "end")
                           else Qt.CursorShape.ClosedHandCursor)
            self.drag_started.emit(kind)
            return
        if self.junction is not None and position.x() >= self.LABEL_WIDTH:
            self.seek_requested.emit(max(0.0, self._seconds(position.x())))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag is not None:
            self._drag = None
            self._frozen_range = None
            self._update_cursor(event.position().x(), event.position().y())
            self.drag_finished.emit()
            self.update()

    def _update_cursor(self, x: float, y: float) -> None:
        kind = self._edit_hit(x, y)
        self.setCursor(Qt.CursorShape.SizeHorCursor if kind in ("start", "end")
                       else Qt.CursorShape.OpenHandCursor if kind is not None
                       else Qt.CursorShape.PointingHandCursor)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        position = event.position()
        if self._drag is not None:
            kind, origin = self._drag
            free = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.drag_moved.emit(kind, self._seconds(position.x()) - origin, free)
            return
        if self.editable:
            self._update_cursor(position.x(), position.y())
            kind = self._edit_hit(position.x(), position.y())
            if kind is not None:
                QToolTip.showText(event.globalPosition().toPoint(), {
                    "start": ("끌어서 믹스 시작 위치 바꾸기", "Drag to change where the mix starts"),
                    "end": ("끌어서 겹침 길이 바꾸기", "Drag to change the overlap length"),
                    "move": ("끌어서 전환 위치 옮기기", "Drag to move the transition"),
                    "incoming": ("끌어서 들어오는 곡의 시작 지점 바꾸기", "Drag to change where the incoming song starts"),
                }[kind][0 if self.korean else 1], self)
                return
        if self.junction is None or position.x() < self.LABEL_WIDTH:
            return
        seconds = self._seconds(position.x())
        offset = seconds - self.junction.handover
        QToolTip.showText(
            event.globalPosition().toPoint(),
            f"{_clock(seconds, precise=True)} ({'화면 전환' if self.korean else 'Canvas switch'} "
            f"{offset:+.1f}s)\n" + ("클릭하면 이 위치로 이동" if self.korean else "Click to seek here"),
            self,
        )


# -- window -------------------------------------------------------------------------

class _TransitionItemDelegate(QStyledItemDelegate):
    """Transition list rows as small cards: number and time, how it mixes, which songs.

    The item's text stays the plain one-line description (accessibility, search);
    ``ROLE`` holds ``{"title", "style", "songs", "playing"}`` for drawing.
    """

    ROLE = Qt.ItemDataRole.UserRole

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        return QSize(max(0, option.rect.width()), 70)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        data = index.data(self.ROLE) or {}
        palette = option.palette
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        card = QRectF(option.rect).adjusted(2, 3, -4, -3)
        highlight = palette.highlight().color()
        fill = QColor(highlight) if selected else QColor(palette.base().color())
        if selected:
            fill.setAlpha(46)
        elif hovered:
            fill = QColor(palette.alternateBase().color())
        painter.setBrush(fill)
        painter.setPen(QPen(highlight if selected else palette.mid().color(), 1.6 if selected else 1.0))
        painter.drawRoundedRect(card, 7, 7)
        if selected:
            painter.fillRect(QRectF(card.left() + 1, card.top() + 8, 3, card.height() - 16), highlight)
        text, muted = palette.text().color(), palette.placeholderText().color()
        left, width = card.left() + 12, card.width() - 24
        bold = QFont(option.font)
        bold.setBold(True)
        painter.setFont(bold)
        painter.setPen(text)
        painter.drawText(QRectF(left, card.top() + 6, width, 20), Qt.AlignmentFlag.AlignVCenter,
                         str(data.get("title", "")))
        if data.get("playing"):
            painter.setPen(PLAYHEAD_COLOR)
            painter.drawText(QRectF(left, card.top() + 6, width, 20),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, str(data["playing"]))
        painter.setFont(option.font)
        painter.setPen(text)
        metrics = painter.fontMetrics()
        painter.drawText(QRectF(left, card.top() + 26, width, 18), Qt.AlignmentFlag.AlignVCenter,
                         metrics.elidedText(str(data.get("style", "")), Qt.TextElideMode.ElideRight, int(width)))
        small = QFont(option.font)
        small.setPointSizeF(max(7.0, small.pointSizeF() - 1.0))
        painter.setFont(small)
        painter.setPen(muted)
        painter.drawText(QRectF(left, card.top() + 44, width, 18), Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(str(data.get("songs", "")),
                                                          Qt.TextElideMode.ElideMiddle, int(width)))
        painter.restore()


class _SeekSlider(QSlider):
    """A playback bar: clicking the groove jumps there instead of paging toward it."""

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            option = QStyleOptionSlider()
            self.initStyleOption(option)
            handle = self.style().subControlRect(
                QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, self)
            if not handle.contains(event.position().toPoint()):
                self.setValue(QStyle.sliderValueFromPosition(
                    self.minimum(), self.maximum(), round(event.position().x() - handle.width() / 2),
                    max(1, self.width() - handle.width())))
        super().mousePressEvent(event)


class TransitionInspectorWindow(QDialog):
    """A larger, drawn view of AutoMixDetailsPanel's transitions, kept in sync with it."""

    seek_requested = Signal(float)
    play_requested = Signal(float)
    playing_toggled = Signal(bool)
    volume_changed = Signal(int)
    override_changed = Signal(str, object)
    """A junction was set by hand (pair key, TransitionOverride) or put back on automatic (key, None)."""

    def __init__(self, panel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.panel = panel
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setModal(False)
        self.resize(1120, 760)
        self.junctions: list[Junction] = []
        self._selected = -1
        self._plan = None
        self._rows = None
        self._language: bool | None = None
        # Manual editing (enable_editing): Preview's overrides, and drafts drawn
        # from an edit until the re-mixed plan arrives.
        self._edit_context = None
        self._overrides: dict[str, object] = {}
        self._drafts: dict[str, Junction] = {}
        self._drag_base = None
        self._drag_override = None

        # Header: what this is and how far the mix is -- nothing else competes here.
        self.title_label = QLabel()
        self.title_label.setObjectName("dialogTitle")
        self.status_label = QLabel()
        self.status_label.setObjectName("previewStatusChip")
        self.export_button = QToolButton()
        self.export_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        export_menu = QMenu(self.export_button)
        self.copy_text_action = export_menu.addAction("")
        self.copy_json_action = export_menu.addAction("")
        self.copy_text_action.triggered.connect(lambda: QGuiApplication.clipboard().setText(panel.as_text()))
        self.copy_json_action.triggered.connect(
            lambda: QGuiApplication.clipboard().setText(rows_to_json(panel.rows)))
        self.export_button.setMenu(export_menu)
        header = QHBoxLayout()
        header.setSpacing(10)
        header.addWidget(self.title_label)
        header.addWidget(self.status_label)
        header.addStretch(1)
        header.addWidget(self.export_button)

        self.overview_title = QLabel()
        self.overview_title.setObjectName("panelTitle")
        self.overview_hint = QLabel()
        self.overview_hint.setObjectName("mutedLabel")
        self.overview = MixOverviewStrip()
        self.overview.selected.connect(self._user_select)
        self.overview.seek_requested.connect(self.seek_requested)
        overview_header = QHBoxLayout()
        overview_header.addWidget(self.overview_title)
        overview_header.addStretch(1)
        overview_header.addWidget(self.overview_hint)

        # Left: the transitions as small cards, with "follow playback" beside them.
        self.list_title = QLabel()
        self.list_title.setObjectName("panelTitle")
        self.follow_check = QCheckBox()
        self.follow_check.setChecked(True)
        self.list = QListWidget()
        self.list.setObjectName("automixTransitionList")
        self.list.setItemDelegate(_TransitionItemDelegate(self.list))
        self.list.setMouseTracking(True)
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.currentRowChanged.connect(self._on_row_changed)
        self.list.itemDoubleClicked.connect(lambda _item: self._seek_to_selected(play=True))
        self._sounding = -1
        list_header = QHBoxLayout()
        list_header.addWidget(self.list_title)
        list_header.addStretch(1)
        list_header.addWidget(self.follow_check)
        list_pane = QWidget()
        list_pane.setMinimumWidth(240)
        list_layout = QVBoxLayout(list_pane)
        list_layout.setContentsMargins(0, 0, 6, 0)
        list_layout.setSpacing(6)
        list_layout.addLayout(list_header)
        list_layout.addWidget(self.list, 1)

        # Right: what happens (plain words, key facts), then how (graph), then numbers.
        self.heading_label = QLabel()
        heading_font = QFont(self.heading_label.font())
        heading_font.setPointSizeF(heading_font.pointSizeF() + 4)
        heading_font.setBold(True)
        self.heading_label.setFont(heading_font)
        self.heading_label.setWordWrap(True)
        from app.widgets.transition_editor import TransitionEditorPanel

        # Auto | Manual, beside the heading: the one switch this window edits.
        self.auto_button = QPushButton()
        self.manual_button = QPushButton()
        self.mode_box = QFrame()
        self.mode_box.setObjectName("transitionModeSwitch")
        mode_layout = QHBoxLayout(self.mode_box)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(0)
        for button in (self.auto_button, self.manual_button):
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setMinimumWidth(72)
            button.setObjectName("transitionModeButton")
            mode_layout.addWidget(button)
        self.auto_button.clicked.connect(lambda: self._set_manual(False))
        self.manual_button.clicked.connect(lambda: self._set_manual(True))
        self.mode_box.hide()
        self.editor = TransitionEditorPanel()
        self.editor.edited.connect(self._apply_edit)
        self.editor.revert_requested.connect(lambda: self._set_manual(False))
        self.editor.hide()
        self.description_label = QLabel()
        self.description_label.setWordWrap(True)
        self.description_label.setTextFormat(Qt.TextFormat.RichText)
        self.fact_labels = [QLabel() for _ in range(4)]
        for label in self.fact_labels:
            label.setObjectName("previewStatusChip")
        self.listen_button = QPushButton()
        self.listen_button.setObjectName("primaryButton")
        self.listen_button.clicked.connect(lambda: self._seek_to_selected(play=True))
        self.jump_button = QPushButton()
        self.jump_button.clicked.connect(lambda: self._seek_to_selected(play=False))
        facts = QHBoxLayout()
        facts.setSpacing(6)
        for label in self.fact_labels:
            facts.addWidget(label)
        facts.addStretch(1)
        facts.addWidget(self.jump_button)
        facts.addWidget(self.listen_button)
        self.diagram = TransitionDiagram()
        self.diagram.seek_requested.connect(self.seek_requested)
        self.diagram.drag_started.connect(self._drag_started)
        self.diagram.drag_moved.connect(self._drag_moved)
        self.diagram.drag_finished.connect(self._drag_finished)
        self.legend_label = QLabel()
        self.legend_label.setObjectName("mutedLabel")
        self.legend_label.setWordWrap(True)
        self.legend_label.setTextFormat(Qt.TextFormat.RichText)
        self.metrics_title = QLabel()
        self.metrics_title.setObjectName("panelTitle")
        self.metrics_grid = QGridLayout()
        self.metrics_grid.setHorizontalSpacing(10)
        self.metrics_grid.setVerticalSpacing(10)
        # Planner internals stay folded away until asked for.
        self.details_button = QToolButton()
        self.details_button.setCheckable(True)
        self.details_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.details_button.setArrowType(Qt.ArrowType.RightArrow)
        self.details_button.toggled.connect(self._set_details_visible)
        self.reasons_title = QLabel()
        self.reasons_title.setObjectName("panelTitle")
        self.reasons_label = QLabel()
        self.reasons_label.setWordWrap(True)
        self.reasons_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.all_values_title = QLabel()
        self.all_values_title.setObjectName("panelTitle")
        self.all_values = QPlainTextEdit()
        self.all_values.setReadOnly(True)
        self.all_values.setMinimumHeight(220)
        self.details_box = QWidget()
        details_layout = QVBoxLayout(self.details_box)
        details_layout.setContentsMargins(18, 0, 0, 0)
        details_layout.setSpacing(6)
        for widget in (self.reasons_title, self.reasons_label, self.all_values_title, self.all_values):
            details_layout.addWidget(widget)
        self.details_box.hide()

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(10, 0, 8, 8)
        detail_layout.setSpacing(10)
        heading_row = QHBoxLayout()
        heading_row.addWidget(self.heading_label, 1)
        heading_row.addWidget(self.mode_box, 0, Qt.AlignmentFlag.AlignTop)
        detail_layout.addLayout(heading_row)
        detail_layout.addWidget(self.description_label)
        detail_layout.addLayout(facts)
        detail_layout.addWidget(self.diagram)
        detail_layout.addWidget(self.legend_label)
        detail_layout.addWidget(self.editor)
        detail_layout.addSpacing(4)
        detail_layout.addWidget(self.metrics_title)
        detail_layout.addLayout(self.metrics_grid)
        detail_layout.addSpacing(4)
        detail_layout.addWidget(self.details_button)
        detail_layout.addWidget(self.details_box)
        detail_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(detail)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(list_pane)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(1, 1)
        splitter.setChildrenCollapsible(False)
        splitter.setSizes([270, 850])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addSpacing(4)
        layout.addLayout(overview_header)
        layout.addWidget(self.overview)
        layout.addSpacing(6)
        layout.addWidget(splitter, 1)
        layout.addWidget(self._build_transport())

        panel.changed.connect(self.refresh)
        panel.playhead_changed.connect(self.set_playhead)
        self._install_shortcuts()
        self.refresh()

    # -- transport ------------------------------------------------------------------

    def _build_transport(self) -> QFrame:
        """Media controls mirroring Preview's: they drive the same playback."""
        self._playing = False
        self._playhead = 0.0
        self._syncing = False
        bar = QFrame()
        bar.setObjectName("previewControlCard")
        self.transport_play_button = QPushButton()
        self.transport_play_button.setObjectName("previewPlayButton")
        self.transport_play_button.setCheckable(True)
        self.transport_play_button.setMinimumWidth(110)
        self.transport_play_button.toggled.connect(self._on_play_toggled)
        self.previous_button = QPushButton()
        self.rewind_button = QPushButton("−5s")
        self.forward_button = QPushButton("+5s")
        self.next_button = QPushButton()
        for button in (self.previous_button, self.rewind_button, self.forward_button, self.next_button):
            button.setObjectName("previewTransportButton")
        self.previous_button.clicked.connect(lambda: self._user_select(self._selected - 1, seek=True))
        self.next_button.clicked.connect(lambda: self._user_select(self._selected + 1, seek=True))
        self.rewind_button.clicked.connect(lambda: self._seek_relative(-5.0))
        self.forward_button.clicked.connect(lambda: self._seek_relative(5.0))
        self.position_slider = _SeekSlider(Qt.Orientation.Horizontal)
        self.position_slider.setObjectName("previewTimeline")
        self.position_slider.valueChanged.connect(self._on_position_changed)
        self.time_label = QLabel("0:00.0 / 0:00")
        self.time_label.setObjectName("previewTimeLabel")
        self.time_label.setMinimumWidth(118)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.loop_check = QCheckBox()
        self.volume_label = QLabel("🔊")
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setFixedWidth(110)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        self.volume_value_label = QLabel("100%")
        self.volume_value_label.setObjectName("previewValueLabel")
        self.volume_value_label.setMinimumWidth(38)

        seek_row = QHBoxLayout()
        seek_row.addWidget(self.position_slider, 1)
        seek_row.addWidget(self.time_label)
        controls = QHBoxLayout()
        controls.setSpacing(6)
        controls.addWidget(self.previous_button)
        controls.addWidget(self.rewind_button)
        controls.addWidget(self.transport_play_button)
        controls.addWidget(self.forward_button)
        controls.addWidget(self.next_button)
        controls.addSpacing(12)
        controls.addWidget(self.loop_check)
        controls.addStretch(1)
        controls.addWidget(self.volume_label)
        controls.addWidget(self.volume_slider)
        controls.addWidget(self.volume_value_label)
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)
        layout.addLayout(seek_row)
        layout.addLayout(controls)
        return bar

    def _install_shortcuts(self) -> None:
        for sequence, callback in (
            ("Space", self.transport_play_button.toggle),
            ("Left", lambda: self._seek_relative(-5.0)),
            ("Right", lambda: self._seek_relative(5.0)),
            ("Shift+Left", self.previous_button.click),
            ("Shift+Right", self.next_button.click),
            ("L", self.loop_check.toggle),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(callback)

    def set_playing(self, playing: bool) -> None:
        """Preview's play state (its own button, a shortcut or this window)."""
        self._playing = bool(playing)
        self.transport_play_button.blockSignals(True)
        self.transport_play_button.setChecked(self._playing)
        self.transport_play_button.blockSignals(False)
        self._set_play_text()

    def set_volume(self, value: int) -> None:
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(int(value))
        self.volume_slider.blockSignals(False)
        self.volume_value_label.setText(f"{int(value)}%")

    def _on_play_toggled(self, playing: bool) -> None:
        self._playing = playing
        self._set_play_text()
        self.playing_toggled.emit(playing)

    def _on_volume_changed(self, value: int) -> None:
        self.volume_value_label.setText(f"{value}%")
        self.volume_changed.emit(value)

    def _on_position_changed(self, value: int) -> None:
        if not self._syncing:
            self.seek_requested.emit(value / 1000.0)

    def _seek_relative(self, seconds: float) -> None:
        duration = self._plan.duration_seconds if self._plan is not None else 0.0
        self.seek_requested.emit(min(max(0.0, self._playhead + seconds), duration))

    def _loop_range(self, junction: Junction) -> tuple[float, float]:
        """The selected transition with a run-up before it and a little after."""
        return max(0.0, junction.start - 4.0), max(junction.end, junction.start) + 2.0

    def _set_play_text(self) -> None:
        korean = self._korean()
        self.transport_play_button.setText(
            ("Ⅱ  일시정지" if korean else "Ⅱ  Pause") if self._playing
            else ("▶  재생" if korean else "▶  Play")
        )

    def _update_position(self, seconds: float) -> None:
        duration = self._plan.duration_seconds if self._plan is not None else 0.0
        if not self.position_slider.isSliderDown():
            self._syncing = True
            self.position_slider.setValue(round(seconds * 1000))
            self._syncing = False
        self.time_label.setText(f"{_clock(seconds, precise=True)} / {_clock(duration)}")

    # -- sync with the panel ----------------------------------------------------------

    def _korean(self) -> bool:
        return self.panel._korean()

    def refresh(self) -> None:
        """Re-read the panel's plan, rows, status and language."""
        korean = self._korean()
        plan, tracks = self.panel.plan, self.panel.tracks
        titles = {track.id: track.title or track.id for track in tracks}
        for widget in (self.overview, self.diagram):
            widget.korean = korean
            widget.titles = titles
        if plan is not self._plan:
            self._plan = plan
            if self._drafts:  # the re-mixed plan replaces what the drafts showed
                self._drafts.clear()
                self._rows = None
                self.editor.set_status("")
            self.junctions = plan_junctions(plan) if plan is not None else []
            self.overview.plan = plan
            self.overview.junctions = self.junctions
            self._syncing = True
            self.position_slider.setRange(0, max(1, round((plan.duration_seconds if plan else 0.0) * 1000)))
            self._syncing = False
            self._update_position(self._playhead)
        rows = self.panel.rows
        self._retranslate(korean)
        if rows is self._rows and korean == self._language:
            return  # only the status line changed (e.g. preparation progress)
        self._rows, self._language = rows, korean
        selected = self._selected
        self.list.blockSignals(True)
        self.list.clear()
        self.list.addItems([self._list_text(row) for row in rows])
        for index, row in enumerate(rows):
            self._set_card(index, row)
        self.list.blockSignals(False)
        self.list_title.setText(f"{'전환' if korean else 'Transitions'} {len(rows)}")
        if rows:
            self._select(min(max(selected, 0), len(rows) - 1), force=True)
        else:
            self._select(-1, force=True)

    def set_playhead(self, seconds: float) -> None:
        self._playhead = seconds
        self.overview.playhead = seconds
        self.diagram.playhead = seconds
        self._update_position(seconds)
        junction = self.junction
        if self.loop_check.isChecked() and self._playing and junction is not None:
            loop_start, loop_end = self._loop_range(junction)
            if seconds >= loop_end:
                self.seek_requested.emit(loop_start)
                return
        sounding = self.panel.current_index
        if sounding != self._sounding:
            previous, self._sounding = self._sounding, sounding
            rows = self.panel.rows
            for index in (previous, sounding):
                if 0 <= index < len(rows) and index < self.list.count():
                    self._set_card(index, rows[index])
        if self.follow_check.isChecked() and self.junctions:
            # The transition playing now, else the next one coming up.
            upcoming = next((index for index, junction in enumerate(self.junctions)
                             if seconds < junction.end), len(self.junctions) - 1)
            if upcoming != self._selected:
                self._select(upcoming)
        if self.isVisible():
            self.overview.update()
            start, end = diagram_range(self.junction) if self.junction else (0.0, 0.0)
            if start - 1 <= seconds <= end + 1:
                self.diagram.update()

    @property
    def junction(self) -> Junction | None:
        return self.junctions[self._selected] if 0 <= self._selected < len(self.junctions) else None

    # -- selection and presentation ---------------------------------------------------

    def _on_row_changed(self, row: int) -> None:
        if row != self._selected:
            self._user_select(row)

    def _user_select(self, index: int, *, seek: bool = False) -> None:
        """A choice made by hand: stop following the playhead so it is not undone next frame.

        ``seek``: the transport's previous/next buttons also move playback to the
        run-up of that transition, like a media player's track skip.
        """
        self.follow_check.setChecked(False)
        self._select(index)
        if seek and self.junction is not None:
            self.seek_requested.emit(self._loop_range(self.junction)[0])

    def _set_details_visible(self, visible: bool) -> None:
        self.details_box.setVisible(visible)
        self.details_button.setArrowType(Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow)

    def _set_card(self, index: int, row: dict[str, object]) -> None:
        """The drawn card of one list row (see _TransitionItemDelegate)."""
        korean = self._korean()
        duration = float(row["duration"])
        length = f"{duration:.1f}s" if duration else ("겹침 없음" if korean else "no overlap")
        self.list.item(index).setData(_TransitionItemDelegate.ROLE, {
            "title": f"{int(row['index']):02d} → {int(row['index']) + 1:02d}    {_clock(float(row['timeline_start']))}",
            "style": ("✎ " if self._is_manual(index) else "") + f"{self.panel._style(row)} · {length}",
            "songs": f"{row['from']} → {row['to']}",
            "playing": ("● 재생 중" if korean else "● Playing") if index == self._sounding else "",
        })

    def _select(self, index: int, *, force: bool = False) -> None:
        rows = self.panel.rows
        if rows:
            index = min(max(index, 0), len(rows) - 1)
        if index == self._selected and not force:
            return
        self._selected = index if rows else -1
        if self.list.currentRow() != self._selected:
            self.list.blockSignals(True)
            self.list.setCurrentRow(self._selected)
            self.list.blockSignals(False)
        self.overview.current = self._selected
        self.overview.update()
        self._show_selected()

    def _seek_to_selected(self, *, play: bool) -> None:
        junction = self.junction
        if junction is None:
            return
        # A few seconds of run-up, so the transition is heard from its approach.
        seconds = max(0.0, junction.start - 4.0) if play else junction.start
        (self.play_requested if play else self.seek_requested).emit(seconds)

    def _list_text(self, row: dict[str, object]) -> str:
        korean = self._korean()
        duration = float(row["duration"])
        length = f"{duration:.1f}s" if duration else ("겹침 없음" if korean else "no overlap")
        return (f"{int(row['index']):02d} → {int(row['index']) + 1:02d} {_clock(float(row['timeline_start']))} · "
                f"{self.panel._style(row)} · {length} · {row['from']} → {row['to']}")

    def _show_selected(self) -> None:
        korean = self._korean()
        rows = self.panel.rows
        junction = self.junction
        manual = self._is_manual(self._selected)
        drawn = self._drafts.get(self._pair(self._selected), junction) if manual else junction
        self.diagram.editable = manual
        self.diagram.draft = drawn is not junction
        self.diagram.set_junction(drawn)
        has_junction = junction is not None and 0 <= self._selected < len(rows)
        self._show_editing(has_junction and rows[self._selected].get("type") != "gap", manual, drawn)
        for widget in (self.listen_button, self.jump_button, self.loop_check, self.details_button, self.diagram):
            widget.setEnabled(has_junction)
        for widget in (self.diagram, self.legend_label, self.details_button, *self.fact_labels,
                       self.listen_button, self.jump_button):
            widget.setVisible(has_junction)
        self.previous_button.setEnabled(self._selected > 0)
        self.next_button.setEnabled(0 <= self._selected < len(rows) - 1)
        self._clear_metrics()
        if not has_junction:
            self.heading_label.setText("곡 사이 전환이 없습니다" if korean else "No transitions between tracks")
            self.description_label.setText(
                "플레이리스트에 곡이 두 곡 이상 있어야 전환이 생깁니다." if korean
                else "Transitions appear once the playlist has two or more songs.")
            for label in (self.reasons_label, self.legend_label):
                label.clear()
            self.all_values.clear()
            self.metrics_title.hide()
            self.details_button.setChecked(False)
            return
        self.all_values.setPlainText(self.panel.detail_text(self._selected, korean))
        row = rows[self._selected]
        self.heading_label.setText(("✎ " if manual else "") + f"{row['from']}  →  {row['to']}")
        duration = float(row["duration"])
        facts = (
            (f"{'시작' if korean else 'Starts'} {_clock(junction.start, precise=True)}",
             "두 곡이 겹치기 시작하는 시각" if korean else "When the two songs start to overlap"),
            ((f"{'믹스' if korean else 'Mix'} {duration:.1f}s" if duration
              else ("겹침 없음" if korean else "No overlap")),
             "두 곡이 함께 들리는 길이" if korean else "How long both songs play together"),
            (f"▣ {'화면 전환' if korean else 'Canvas switch'} {_clock(junction.handover, precise=True)}",
             "영상이 다음 곡으로 넘어가는 시각 (겹침의 가운데)" if korean
             else "When the video moves to the next song (middle of the overlap)"),
            (f"{int(row['index']):02d} / {len(rows):02d}",
             "전체 전환 중 순서" if korean else "Position among all transitions"),
        )
        for label, (text, tip) in zip(self.fact_labels, facts):
            label.setText(text)
            label.setToolTip(tip)
        key = str(row["dsp"] or row["type"])
        description = _STYLE_DESCRIPTIONS.get(key)
        if key == "sequential" and self.panel.automix:
            description = (self.panel._back_to_back_reason(True), self.panel._back_to_back_reason(False))
        style = self.panel._style(row)
        self.description_label.setText(
            f"<b>{style}</b> — {description[0 if korean else 1]}" if description is not None else f"<b>{style}</b>")
        self.legend_label.setText(self._legend(korean))
        self.legend_label.setToolTip(
            "곡선은 대역별로 두 곡의 음량(0–100%)입니다. 실선은 나가는 곡, 점선은 들어오는 곡입니다." if korean
            else "Curves are each song's level (0–100%) per band: solid is outgoing, dashed is incoming.")
        self._fill_metrics(row, korean)
        reasons = [reason for reason in str(row.get("reasons") or "").split("; ") if reason]
        self.reasons_title.setVisible(bool(reasons) or duration > 0.0)
        self.reasons_label.setText(
            # The planner's own line markers (*, ?, +) are kept: they carry meaning.
            "\n".join(reasons) if reasons
            else ("선택 근거 정보가 없는 전환입니다." if korean
                  else "No selection reasons recorded for this transition.") if duration > 0.0 else ""
        )

    def _legend(self, korean: bool) -> str:
        def swatch(color: QColor, text: str, symbol: str = "■") -> str:
            return f"<span style='color:{color.name()}'>{symbol}</span> {text}"

        items = [
            swatch(OUTGOING_COLOR, "나가는 곡" if korean else "Outgoing"),
            swatch(INCOMING_COLOR, "들어오는 곡" if korean else "Incoming"),
            swatch(self.palette().highlight().color(), "화면 전환" if korean else "Canvas switch", "▣"),
            swatch(PLAYHEAD_COLOR, "재생 위치" if korean else "Playhead", "▼"),
        ]
        if self.junction is not None and self.junction.ramp is not None:
            items.append("▨ " + ("템포 맞춤" if korean else "Tempo matching"))
        items.append("· " + ("그래프를 클릭하면 그 위치로 이동" if korean else "Click the graph to seek"))
        return "&nbsp;&nbsp;&nbsp;".join(items)

    def _clear_metrics(self) -> None:
        while self.metrics_grid.count():
            item = self.metrics_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Detach now: deleteLater alone leaves the old card painted until
                # the event loop runs, overlapping the new selection's layout.
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _fill_metrics(self, row: dict[str, object], korean: bool) -> None:
        def number(key: str, digits: int = 1) -> str | None:
            value = row.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            return f"{value:.{digits}f}"

        def yes_no(key: str) -> str | None:
            value = row.get(key)
            if value is None or key not in row:
                return None
            if isinstance(value, bool):
                return ("예" if value else "아니요") if korean else ("yes" if value else "no")
            return str(value)

        cards: list[tuple[str, str, str]] = []
        if number("outgoing_bpm") and number("incoming_bpm"):
            detail = []
            if number("target_bpm"):
                detail.append(f"{'목표' if korean else 'Target'} {number('target_bpm')}")
            if number("tempo_delta_percent"):
                detail.append(f"{'차이' if korean else 'Delta'} {number('tempo_delta_percent')}%")
            if number("tempo_ramp_seconds") and float(row["tempo_ramp_seconds"]) > 0:
                detail.append(f"{'맞춤' if korean else 'Ramp'} {number('tempo_ramp_seconds')}s")
            cards.append(("템포 (BPM)" if korean else "Tempo (BPM)",
                          f"{number('outgoing_bpm')} → {number('incoming_bpm')}", " · ".join(detail)))
        if row.get("outgoing_key") or row.get("incoming_key"):
            clash = yes_no("key_clash")
            cards.append(("키" if korean else "Key",
                          f"{row.get('outgoing_key') or '?'} → {row.get('incoming_key') or '?'}",
                          (f"{'충돌' if korean else 'Clash'}: {clash}" if clash else "")))
        if number("score", 2):
            cards.append(("후보 점수" if korean else "Candidate score", number("score", 2),
                          " · ".join(str(value) for value in (row.get("strategy"),
                                                               f"{row['bars']} {'마디' if korean else 'bars'}"
                                                               if row.get("bars") else None) if value)))
        if number("energy_delta", 2):
            cards.append(("에너지 차이" if korean else "Energy delta", number("energy_delta", 2),
                          "양수: 들어오는 곡이 더 강함" if korean else "Positive: incoming is stronger"))
        vocal = yes_no("vocal_overlap")
        if vocal:
            cards.append(("보컬 겹침" if korean else "Vocal overlap", vocal, ""))
        if number("outgoing_downbeat_confidence", 2) or number("incoming_downbeat_confidence", 2):
            cards.append(("다운비트 신뢰도" if korean else "Downbeat confidence",
                          f"{number('outgoing_downbeat_confidence', 2) or '–'} / "
                          f"{number('incoming_downbeat_confidence', 2) or '–'}",
                          "나가는 곡 / 들어오는 곡" if korean else "outgoing / incoming"))
        if row.get("outgoing_analyzer") or row.get("incoming_analyzer"):
            cards.append(("분석기" if korean else "Analyzer",
                          f"{row.get('outgoing_analyzer') or '–'} / {row.get('incoming_analyzer') or '–'}",
                          "나가는 곡 / 들어오는 곡" if korean else "outgoing / incoming"))
        self.metrics_title.setVisible(bool(cards))
        columns = 3
        for position, (title, value, detail) in enumerate(cards):
            card = QFrame()
            card.setObjectName("card")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 10, 12, 10)
            card_layout.setSpacing(2)
            title_label = QLabel(title)
            title_label.setObjectName("mutedLabel")
            value_label = QLabel(value)
            value_font = QFont(value_label.font())
            value_font.setPointSizeF(value_font.pointSizeF() + 2)
            value_font.setBold(True)
            value_label.setFont(value_font)
            card_layout.addWidget(title_label)
            card_layout.addWidget(value_label)
            if detail:
                detail_label = QLabel(detail)
                detail_label.setObjectName("mutedLabel")
                detail_label.setWordWrap(True)
                card_layout.addWidget(detail_label)
            card.setAccessibleName(f"{title}: {value}. {detail}")
            self.metrics_grid.addWidget(card, position // columns, position % columns)

    def _retranslate(self, korean: bool) -> None:
        self.auto_button.setText("자동" if korean else "Auto")
        self.manual_button.setText("수동" if korean else "Manual")
        self.auto_button.setToolTip("분석으로 전환 위치·길이·스타일을 자동으로 정합니다." if korean
                                    else "Analysis decides where, how long and how this transition mixes.")
        self.manual_button.setToolTip("이 전환을 직접 설정합니다. 지금 들리는 전환에서 출발합니다." if korean
                                      else "Set this transition yourself, starting from what plays now.")
        self.editor.retranslate(korean)
        self.setWindowTitle("전환 상세" if korean else "Transition details")
        self.title_label.setText("전환 상세" if korean else "Transition details")
        self.status_label.setText(self.panel.status_label.text())
        self.status_label.setToolTip(self.panel.status_label.toolTip())
        self.follow_check.setText("자동 선택" if korean else "Auto-select")
        self.follow_check.setToolTip(
            "재생 중이거나 곧 나올 전환을 자동으로 선택합니다. 직접 고르면 꺼집니다." if korean
            else "Selects the transition playing now or coming up next. Picking one yourself turns it off."
        )
        self.export_button.setText("복사 ▾" if korean else "Copy ▾")
        self.export_button.setToolTip("전환 정보를 클립보드로 복사합니다." if korean
                                      else "Copy the transition information to the clipboard.")
        self.copy_text_action.setText("모든 전환을 텍스트로 복사" if korean else "Copy all transitions as text")
        self.copy_json_action.setText("모든 전환을 JSON으로 복사" if korean else "Copy all transitions as JSON")
        self.overview_title.setText("전체 믹스" if korean else "Whole mix")
        self.overview_hint.setText("색칠된 구간이 전환 · 클릭: 선택 · 더블클릭: 이동" if korean
                                   else "Shaded spans are transitions · Click: select · Double-click: seek")
        self.metrics_title.setText("분석 수치" if korean else "Analysis")
        self.details_button.setText("자세한 정보 · 선택 근거와 전체 수치" if korean
                                    else "More details · reasons and all values")
        self.reasons_title.setText("이 방식을 고른 이유 (플래너 기록)" if korean else "Why this mix (planner notes)")
        self.all_values_title.setText("전체 수치" if korean else "All values")
        self.list.setToolTip("클릭: 선택 · 더블클릭: 4초 전부터 재생" if korean
                             else "Click: select · Double-click: play from 4 s before")
        self.listen_button.setText("▶ 4초 전부터 듣기" if korean else "▶ Listen from 4 s before")
        self.listen_button.setToolTip("전환이 시작되기 4초 전부터 재생합니다." if korean
                                      else "Plays from 4 seconds before the transition starts.")
        self.jump_button.setText("시작점으로 이동" if korean else "Go to start")
        self.jump_button.setToolTip("재생 위치를 전환이 시작되는 지점으로 옮깁니다." if korean
                                    else "Moves the playhead to where the transition starts.")
        self.details_button.setToolTip(
            "플래너가 이 방식을 고른 근거와, 큐·속도·보컬·구조 위치 등 기록된 모든 값을 봅니다." if korean
            else "Why the planner chose this mix, and every value it recorded (cues, rates, vocal and structure times).")
        self.previous_button.setText("‹  이전 전환" if korean else "‹  Previous")
        self.next_button.setText("다음 전환  ›" if korean else "Next  ›")
        self.previous_button.setToolTip(
            "이전 전환을 선택하고 4초 전부터 재생 위치를 옮깁니다 (Shift+←)" if korean
            else "Select the previous transition and move to 4 s before it (Shift+←)")
        self.next_button.setToolTip(
            "다음 전환을 선택하고 4초 전부터 재생 위치를 옮깁니다 (Shift+→)" if korean
            else "Select the next transition and move to 4 s before it (Shift+→)")
        self.rewind_button.setToolTip("5초 뒤로 (←)" if korean else "Back 5 s (←)")
        self.forward_button.setToolTip("5초 앞으로 (→)" if korean else "Forward 5 s (→)")
        self.transport_play_button.setToolTip("재생 / 일시정지 (Space)" if korean else "Play / Pause (Space)")
        self._set_play_text()
        self.loop_check.setText("🔁 선택한 전환 반복" if korean else "🔁 Loop selected transition")
        self.loop_check.setToolTip(
            "선택한 전환의 4초 전부터 끝난 뒤 2초까지를 반복 재생합니다 (L)" if korean
            else "Repeats from 4 s before the selected transition to 2 s after it (L)")
        self.position_slider.setToolTip(
            "재생바 · 클릭하거나 끌어서 이동합니다" if korean else "Playback bar · click or drag to seek")
        self.volume_slider.setToolTip("볼륨" if korean else "Volume")

    # -- manual editing ---------------------------------------------------------------

    def enable_editing(self, context_provider: Callable[[], object], overrides: dict[str, object]) -> None:
        """Let this window set junctions by hand.

        ``context_provider`` returns a transition_editor.EditContext (tracks and
        the analysis known so far); ``overrides`` is Preview's pair key ->
        TransitionOverride map. Edits come back through ``override_changed``.
        """
        self._edit_context = context_provider
        self._overrides = dict(overrides)
        self._rows = None
        self.refresh()

    def set_overrides(self, overrides: dict[str, object]) -> None:
        self._overrides = dict(overrides)
        self._rows = None
        self.refresh()

    def select_pair(self, outgoing_track_id: str, incoming_track_id: str) -> bool:
        """Select the junction between two tracks (e.g. from the playlist's chip)."""
        for index, junction in enumerate(self.junctions):
            if (junction.outgoing.track_id, junction.incoming.track_id) == (outgoing_track_id, incoming_track_id):
                self._user_select(index)
                return True
        return False

    def _pair(self, index: int) -> str:
        from app.automix.overrides import pair_key

        if not 0 <= index < len(self.junctions):
            return ""
        junction = self.junctions[index]
        return pair_key(junction.outgoing.track_id, junction.incoming.track_id)

    def _is_manual(self, index: int) -> bool:
        return self._edit_context is not None and self._pair(index) in self._overrides

    def _context(self):
        return self._edit_context() if self._edit_context is not None else None

    def _show_editing(self, editable: bool, manual: bool, drawn: Junction | None) -> None:
        self.mode_box.setVisible(self._edit_context is not None and editable)
        self.auto_button.setChecked(not manual)
        self.manual_button.setChecked(manual)
        self.editor.setVisible(manual and editable)
        if not (manual and editable) or drawn is None:
            self.diagram.beat_marks = ((), ())
            return
        context = self._context()
        analyses = context.analyses if context is not None else {}
        outgoing = analyses.get(drawn.outgoing.track_id)
        incoming = analyses.get(drawn.incoming.track_id)
        tracks = context.tracks if context is not None else {}
        durations = tuple(tracks[clip.track_id].duration_seconds if clip.track_id in tracks else clip.source_out
                          for clip in (drawn.outgoing, drawn.incoming))
        self.editor.set_override(self._overrides.get(self._pair(self._selected)), (outgoing, incoming), durations)

        def marks(clip: AudioRenderClip, analysis) -> tuple[float, ...]:
            from app.widgets.transition_editor import beat_grid

            return tuple(clip.timeline_at(point) for point in beat_grid(analysis)
                         if clip.source_in <= point <= clip.source_out)

        self.diagram.beat_marks = (marks(drawn.outgoing, outgoing), marks(drawn.incoming, incoming))

    def _set_manual(self, manual: bool) -> None:
        junction = self.junction
        if junction is None or self._edit_context is None:
            return
        key = self._pair(self._selected)
        if manual == (key in self._overrides):
            return
        self.follow_check.setChecked(False)  # editing this junction: the playhead must not move the selection
        if manual:
            from app.widgets.transition_editor import override_from_junction

            self._apply_edit(override_from_junction(junction))
        else:
            self._overrides.pop(key, None)
            self._drafts.pop(key, None)
            self.editor.set_status(self._remix_text())
            self._rows = None
            self.override_changed.emit(key, None)
            self.refresh()

    def _remix_text(self) -> str:
        return "변경 사항으로 다시 믹싱하는 중…" if self._korean() else "Re-mixing with your change…"

    def _draft(self, override) -> Junction | None:
        from app.widgets.transition_editor import draft_junction

        context = self._context()
        if context is None or not 0 <= self._selected < len(self.junctions):
            return None
        return draft_junction(self.junctions, self._selected, override, context)

    def _apply_edit(self, override, *, commit: bool = True) -> None:
        """Show ``override`` on the selected junction; ``commit`` stores it and asks Preview to re-mix."""
        key = self._pair(self._selected)
        if not key:
            return
        draft = self._draft(override)
        if draft is not None:
            self._drafts[key] = draft
        if commit:
            self._overrides[key] = override
            self.editor.set_status(self._remix_text())
            self.override_changed.emit(key, override)
            self._rows = None
            self.refresh()
        else:
            if draft is not None:
                self.diagram.draft = True
                self.diagram.set_junction(draft)
            self.editor.set_override(override, self.editor._analyses)

    def _drag_started(self, _kind: str) -> None:
        self.follow_check.setChecked(False)
        key = self._pair(self._selected)
        override = self._overrides.get(key)
        drawn = self.diagram.junction
        self._drag_base = (override, drawn) if override is not None and drawn is not None else None
        self._drag_override = None

    def _drag_moved(self, kind: str, delta: float, free: bool) -> None:
        from app.widgets.transition_editor import MIN_EDIT_SECONDS, beat_grid, snap, snap_length
        from app.automix.overrides import MAX_DURATION_SECONDS

        if self._drag_base is None:
            return
        base, drawn = self._drag_base
        context = self._context()
        analyses = context.analyses if context is not None else {}
        tracks = context.tracks if context is not None else {}
        outgoing_analysis = analyses.get(drawn.outgoing.track_id)
        incoming_analysis = analyses.get(drawn.incoming.track_id)
        snapping = self.editor.snapping and not free
        outgoing_track = tracks.get(drawn.outgoing.track_id)
        head = replace(drawn.outgoing, source_out=max(drawn.outgoing.source_out,
                                                      outgoing_track.duration_seconds if outgoing_track else 0.0))
        changes: dict[str, float] = {}
        if kind in ("move", "start"):
            cue = head.source_at(drawn.start + delta)
            if snapping:
                cue = snap(cue, beat_grid(outgoing_analysis))
            changes["outgoing_cue"] = max(0.0, cue)
            if kind == "start" and drawn.end > drawn.start:
                changes["duration"] = drawn.end - head.timeline_at(cue)
        elif kind == "end":
            length = base.duration + delta
            changes["duration"] = snap_length(length, incoming_analysis) if snapping else length
        elif kind == "incoming":
            cue = max(0.0, base.incoming_cue - delta)
            if snapping:
                cue = snap(cue, (0.0, *beat_grid(incoming_analysis)))
            changes["incoming_cue"] = cue
        if "duration" in changes:
            changes["duration"] = min(MAX_DURATION_SECONDS, max(MIN_EDIT_SECONDS, changes["duration"]))
        try:
            override = replace(base, **changes)
        except ValueError:
            return
        self._drag_override = override
        self._apply_edit(override, commit=False)

    def _drag_finished(self) -> None:
        override, self._drag_override, self._drag_base = self._drag_override, None, None
        if override is not None and override != self._overrides.get(self._pair(self._selected)):
            self._apply_edit(override)
