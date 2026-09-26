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
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QGuiApplication, QMouseEvent, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget,
    QPushButton, QScrollArea, QSplitter, QToolTip, QVBoxLayout, QWidget,
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
        mixed = transition is not None and transition.duration > 0.0
        start = transition.timeline_start if mixed else incoming.timeline_start
        end = start + transition.duration if mixed else start
        handover = segments[index].end if index < len(segments) else start
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
        junctions.append(Junction(
            index + 1, outgoing, incoming, transition if mixed else None,
            start, end, handover, tuple(markers), ramp,
        ))
    return junctions


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
    """One junction up close: both clips, each band's gain curves, markers, the playhead."""

    seek_requested = Signal(float)

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
        if self.junction is not None and event.position().x() >= self.LABEL_WIDTH:
            self.seek_requested.emit(max(0.0, self._seconds(event.position().x())))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.junction is None or event.position().x() < self.LABEL_WIDTH:
            return
        seconds = self._seconds(event.position().x())
        offset = seconds - self.junction.handover
        QToolTip.showText(
            event.globalPosition().toPoint(),
            f"{_clock(seconds, precise=True)} ({'화면 전환' if self.korean else 'Canvas switch'} "
            f"{offset:+.1f}s)\n" + ("클릭하면 이 위치로 이동" if self.korean else "Click to seek here"),
            self,
        )


# -- window -------------------------------------------------------------------------

class TransitionInspectorWindow(QDialog):
    """A larger, drawn view of AutoMixDetailsPanel's transitions, kept in sync with it."""

    seek_requested = Signal(float)
    play_requested = Signal(float)

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

        self.title_label = QLabel()
        self.title_label.setObjectName("dialogTitle")
        self.status_label = QLabel()
        self.status_label.setObjectName("previewStatusChip")
        self.follow_check = QCheckBox()
        self.follow_check.setChecked(True)
        self.copy_text_button = QPushButton()
        self.copy_json_button = QPushButton()
        self.copy_text_button.clicked.connect(lambda: QGuiApplication.clipboard().setText(panel.as_text()))
        self.copy_json_button.clicked.connect(lambda: QGuiApplication.clipboard().setText(rows_to_json(panel.rows)))
        header = QHBoxLayout()
        header.addWidget(self.title_label)
        header.addWidget(self.status_label)
        header.addStretch(1)
        header.addWidget(self.follow_check)
        header.addWidget(self.copy_text_button)
        header.addWidget(self.copy_json_button)

        self.overview_label = QLabel()
        self.overview_label.setObjectName("mutedLabel")
        self.overview = MixOverviewStrip()
        self.overview.selected.connect(self._user_select)
        self.overview.seek_requested.connect(self.seek_requested)

        self.list = QListWidget()
        self.list.setObjectName("automixTransitionList")
        self.list.setMinimumWidth(230)
        self.list.setWordWrap(True)
        self.list.setSpacing(3)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.currentRowChanged.connect(self._on_row_changed)
        self.list.itemDoubleClicked.connect(lambda _item: self._seek_to_selected(play=False))

        self.heading_label = QLabel()
        heading_font = QFont(self.heading_label.font())
        heading_font.setPointSizeF(heading_font.pointSizeF() + 3)
        heading_font.setBold(True)
        self.heading_label.setFont(heading_font)
        self.heading_label.setWordWrap(True)
        self.subheading_label = QLabel()
        self.subheading_label.setObjectName("mutedLabel")
        self.subheading_label.setWordWrap(True)
        self.description_label = QLabel()
        self.description_label.setObjectName("infoCallout")
        self.description_label.setWordWrap(True)
        self.diagram = TransitionDiagram()
        self.diagram.seek_requested.connect(self.seek_requested)
        self.legend_label = QLabel()
        self.legend_label.setObjectName("mutedLabel")
        self.legend_label.setWordWrap(True)
        self.legend_label.setTextFormat(Qt.TextFormat.RichText)
        self.metrics_title = QLabel()
        self.metrics_title.setObjectName("panelTitle")
        self.metrics_grid = QGridLayout()
        self.metrics_grid.setHorizontalSpacing(10)
        self.metrics_grid.setVerticalSpacing(10)
        self.reasons_title = QLabel()
        self.reasons_title.setObjectName("panelTitle")
        self.reasons_label = QLabel()
        self.reasons_label.setWordWrap(True)
        self.reasons_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.play_button = QPushButton()
        self.play_button.setObjectName("primaryButton")
        self.play_button.clicked.connect(lambda: self._seek_to_selected(play=True))
        self.jump_button = QPushButton()
        self.jump_button.clicked.connect(lambda: self._seek_to_selected(play=False))
        self.previous_button = QPushButton("◀")
        self.next_button = QPushButton("▶")
        self.previous_button.clicked.connect(lambda: self._user_select(self._selected - 1))
        self.next_button.clicked.connect(lambda: self._user_select(self._selected + 1))
        actions = QHBoxLayout()
        actions.addWidget(self.previous_button)
        actions.addWidget(self.next_button)
        actions.addStretch(1)
        actions.addWidget(self.jump_button)
        actions.addWidget(self.play_button)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(4, 0, 8, 8)
        detail_layout.setSpacing(10)
        detail_layout.addWidget(self.heading_label)
        detail_layout.addWidget(self.subheading_label)
        detail_layout.addLayout(actions)
        detail_layout.addWidget(self.diagram)
        detail_layout.addWidget(self.legend_label)
        detail_layout.addWidget(self.description_label)
        detail_layout.addWidget(self.metrics_title)
        detail_layout.addLayout(self.metrics_grid)
        detail_layout.addWidget(self.reasons_title)
        detail_layout.addWidget(self.reasons_label)
        detail_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(detail)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.list)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 860])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addLayout(header)
        layout.addWidget(self.overview_label)
        layout.addWidget(self.overview)
        layout.addWidget(splitter, 1)

        panel.changed.connect(self.refresh)
        panel.playhead_changed.connect(self.set_playhead)
        self.refresh()

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
            self.junctions = plan_junctions(plan) if plan is not None else []
            self.overview.plan = plan
            self.overview.junctions = self.junctions
        rows = self.panel.rows
        self._retranslate(korean)
        if rows is self._rows and korean == self._language:
            return  # only the status line changed (e.g. preparation progress)
        self._rows, self._language = rows, korean
        selected = self._selected
        self.list.blockSignals(True)
        self.list.clear()
        self.list.addItems([self._list_text(row) for row in rows])
        self.list.blockSignals(False)
        if rows:
            self._select(min(max(selected, 0), len(rows) - 1), force=True)
        else:
            self._select(-1, force=True)

    def set_playhead(self, seconds: float) -> None:
        self.overview.playhead = seconds
        self.diagram.playhead = seconds
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

    def _user_select(self, index: int) -> None:
        """A choice made by hand: stop following the playhead so it is not undone next frame."""
        self.follow_check.setChecked(False)
        self._select(index)

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
        return (f"{int(row['index']):02d} → {int(row['index']) + 1:02d}   {_clock(float(row['timeline_start']))}"
                f"\n{self.panel._style(row)} · {length}\n{row['from']} → {row['to']}")

    def _show_selected(self) -> None:
        korean = self._korean()
        rows = self.panel.rows
        junction = self.junction
        self.diagram.set_junction(junction)
        for button in (self.play_button, self.jump_button, self.previous_button, self.next_button):
            button.setEnabled(junction is not None)
        self.previous_button.setEnabled(self._selected > 0)
        self.next_button.setEnabled(0 <= self._selected < len(rows) - 1)
        self._clear_metrics()
        if junction is None or not 0 <= self._selected < len(rows):
            self.heading_label.setText("이 미리보기에는 곡 사이 전환이 없습니다." if korean
                                       else "This preview has no transitions between tracks.")
            for label in (self.subheading_label, self.description_label, self.reasons_label, self.legend_label):
                label.clear()
            self.metrics_title.hide()
            self.reasons_title.hide()
            self.description_label.hide()
            return
        row = rows[self._selected]
        self.heading_label.setText(f"{row['from']}  →  {row['to']}")
        duration = float(row["duration"])
        parts = [f"{'전환' if korean else 'Transition'} {int(row['index']):02d}/{len(rows):02d}",
                 f"{'시작' if korean else 'Starts'} {_clock(junction.start, precise=True)}",
                 (f"{'길이' if korean else 'Length'} {duration:.1f}s" if duration
                  else ("겹침 없음" if korean else "No overlap")),
                 f"{'화면 전환' if korean else 'Canvas switch'} {_clock(junction.handover, precise=True)}",
                 self.panel._style(row)]
        self.subheading_label.setText("   ·   ".join(parts))
        key = str(row["dsp"] or row["type"])
        description = _STYLE_DESCRIPTIONS.get(key)
        if key == "sequential" and self.panel.automix:
            description = (self.panel._back_to_back_reason(True), self.panel._back_to_back_reason(False))
        self.description_label.setVisible(description is not None)
        if description is not None:
            self.description_label.setText(description[0 if korean else 1])
        self.legend_label.setText(self._legend(korean))
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
            swatch(OUTGOING_COLOR, "나가는 곡 (실선)" if korean else "Outgoing (solid)"),
            swatch(INCOMING_COLOR, "들어오는 곡 (점선)" if korean else "Incoming (dashed)"),
            swatch(self.palette().highlight().color(),
                   "화면 전환: 캔버스가 다음 곡으로 넘어가는 지점" if korean
                   else "Canvas switch: where the video moves to the next track", "▣"),
            swatch(PLAYHEAD_COLOR, "재생 위치" if korean else "Playhead", "▼"),
        ]
        if self.junction is not None and self.junction.ramp is not None:
            items.append(("▨ 빗금: 템포를 맞추는 구간" if korean else "▨ Hatched: tempo matching"))
        hint = ("그래프를 클릭하면 그 위치로 이동합니다. 곡선은 각 대역에서 두 곡의 음량(0–100%)입니다." if korean
                else "Click the graph to seek. Curves show each track's level (0–100%) per band.")
        return "&nbsp;&nbsp;&nbsp;".join(items) + f"<br>{hint}"

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
        self.setWindowTitle("전환 상세" if korean else "Transition details")
        self.title_label.setText("전환 상세" if korean else "Transition details")
        self.status_label.setText(self.panel.status_label.text())
        self.status_label.setToolTip(self.panel.status_label.toolTip())
        self.follow_check.setText("재생 위치 따라가기" if korean else "Follow playhead")
        self.follow_check.setToolTip(
            "재생 중인 전환이나 다음 전환을 자동으로 선택합니다." if korean
            else "Selects the transition playing now, or the next one, automatically."
        )
        self.copy_text_button.setText("텍스트 복사" if korean else "Copy text")
        self.copy_json_button.setText("JSON 복사" if korean else "Copy JSON")
        self.overview_label.setText(
            "전체 믹스 · 강조된 구간이 두 곡이 겹치는 전환입니다. 클릭하면 선택, 더블클릭하면 그 위치로 이동합니다." if korean
            else "Whole mix · highlighted spans are overlaps. Click to select, double-click to seek."
        )
        self.metrics_title.setText("분석 수치" if korean else "Analysis")
        self.reasons_title.setText("이 방식을 고른 이유" if korean else "Why this mix")
        self.play_button.setText("▶ 4초 전부터 듣기" if korean else "▶ Listen from 4 s before")
        self.play_button.setToolTip("전환이 시작되기 4초 전부터 재생합니다." if korean
                                    else "Plays from 4 seconds before the transition starts.")
        self.jump_button.setText("전환 시작으로 이동" if korean else "Go to transition")
        self.previous_button.setToolTip("이전 전환" if korean else "Previous transition")
        self.next_button.setToolTip("다음 전환" if korean else "Next transition")
