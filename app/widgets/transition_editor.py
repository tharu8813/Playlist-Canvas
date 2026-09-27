"""Manual transition editing for the "Transition details" window.

Pure helpers (prefill an override from what is playing, draft a junction from
an override, snap to the beat grid) plus TransitionEditorPanel, the controls
under the diagram. The diagram's drag handles live in TransitionDiagram; the
window ties both to the override store Preview owns. Nothing here renders
audio: an edit is committed as a TransitionOverride and Preview re-mixes.
"""

from __future__ import annotations

import bisect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QSlider, QToolButton, QVBoxLayout, QWidget,
)

from app.automix.models import TrackAnalysis
from app.automix.overrides import (
    EQ_BANDS, MANUAL_STYLES, MAX_DURATION_SECONDS, MIN_EQ_WINDOW, STYLE_AUTO, STYLE_CUT, STYLE_EQ,
    BandWindows, TransitionOverride, Window, pair_key,
)
from app.automix.renderer import BAND_ENVELOPES, HIGH_CROSSOVER_HZ, LOW_CROSSOVER_HZ, default_eq_bands
from app.automix.structure.models import TrackStructureAnalysis
from app.models.playlist import PlaylistTrack
from app.timeline.render_plan import TransitionDsp
from app.widgets.transition_inspector import INCOMING_COLOR, OUTGOING_COLOR, Junction, make_junction

DEFAULT_MANUAL_SECONDS = 8.0
MIN_EDIT_SECONDS = 0.5

STYLE_CHOICES = {
    # style: (Korean, English, Korean tip, English tip)
    STYLE_AUTO: ("자동 추천", "Auto pick", "분석 결과로 이 구간에 맞는 스타일을 고릅니다.",
                 "Analysis picks the style for this window."),
    "bass_swap": ("베이스 스왑", "Bass swap", "저음을 가운데서 맞바꿉니다. 박자가 맞는 곡에 좋습니다.",
                  "Swaps the lows mid-way; best for beat-matched songs."),
    "vocal_safe_eq": ("보컬 보호", "Vocal-safe", "저음에 이어 보컬 대역도 맞바꿔 두 보컬이 겹치지 않게 합니다.",
                      "Swaps the lows, then the vocal band, so voices do not clash."),
    "filter_sweep": ("필터 스윕", "Filter sweep", "나가는 곡의 저음을 필터로 걷어내는 DJ식 전환입니다.",
                     "A DJ-style highpass sweep out of the outgoing song."),
    "filter_blend": ("필터 블렌드", "Filter blend", "들어오는 곡이 고음부터 스며드는 3대역 블렌드입니다.",
                     "A 3-band blend; the incoming song arrives highs-first."),
    "short_fade": ("짧은 페이드", "Short fade", "대역 분리 없이 전체 음량을 교차합니다.",
                   "A full-band equal-power fade."),
    "drop_in": ("드롭인", "Drop in", "들어오는 곡을 처음부터 제 음량으로 시작하고 앞 곡은 아래로 사라집니다.",
                "The incoming song starts at full level; the outgoing one fades under it."),
    "legacy": ("크로스페이드", "Crossfade", "가장 단순한 전체 대역 크로스페이드입니다.",
               "The plainest full-band crossfade."),
    "cut": ("컷", "Cut", "겹치지 않고 큐 지점에서 바로 다음 곡으로 넘어갑니다.",
            "No overlap: jump to the next song at the cue."),
    "eq": ("EQ 직접", "Custom EQ", "저음·중음·고음을 각각 언제 넘길지 아래 그래프에서 직접 정합니다.",
           "Set when the lows, mids and highs each change hands, in the graph below."),
}
assert tuple(STYLE_CHOICES) == MANUAL_STYLES


def style_label(style: str, korean: bool) -> str:
    names = STYLE_CHOICES.get(style)
    return (names[0] if korean else names[1]) if names else style


def junction_pair(junction: Junction) -> str:
    return pair_key(junction.outgoing.track_id, junction.incoming.track_id)


def override_from_junction(junction: Junction) -> TransitionOverride:
    """A manual starting point that sounds like what is playing now."""
    transition = junction.transition
    if transition is not None:
        details = dict(transition.details)
        rate = details.get("outgoing_rate")
        cue = details.get("outgoing_cue")
        return TransitionOverride(
            outgoing_cue=max(0.0, float(cue if isinstance(cue, (int, float)) else
                                        junction.outgoing.source_at(junction.start))),
            incoming_cue=max(0.0, float(junction.incoming.source_in)),
            duration=min(MAX_DURATION_SECONDS, float(transition.duration)),
            style=STYLE_AUTO,
            tempo_match=isinstance(rate, (int, float)) and abs(float(rate) - 1.0) > 1e-9,
        )
    # No overlap now (an automatic cut or back-to-back junction): keep that exact
    # boundary as a manual cut; picking a style then opens an 8 s overlap there.
    return TransitionOverride(
        outgoing_cue=max(0.0, float(junction.outgoing.source_out)),
        incoming_cue=max(0.0, float(junction.incoming.source_in)),
        duration=DEFAULT_MANUAL_SECONDS,
        style=STYLE_CUT,
    )


@dataclass
class EditContext:
    """What drafting a junction needs besides the plan: the tracks and whatever analysis exists."""

    tracks: Mapping[str, PlaylistTrack]
    analyses: Mapping[str, TrackAnalysis] = field(default_factory=dict)
    structures: Mapping[str, TrackStructureAnalysis] = field(default_factory=dict)


def ramp_floor(junctions: Sequence[Junction], index: int) -> float:
    """Outgoing source second where the previous junction's overlap ends (the clip's start if none)."""
    outgoing = junctions[index].outgoing
    if index > 0 and junctions[index - 1].transition is not None:
        return outgoing.source_at(junctions[index - 1].end)
    return outgoing.source_in


def draft_junction(
    junctions: Sequence[Junction], index: int, override: TransitionOverride, context: EditContext,
) -> Junction | None:
    """How junction ``index`` will look with ``override``, planned by the planner's own manual path."""
    from app.automix.planner import plan_manual_junction
    from app.automix.settings import AUTOMIX_SETTINGS

    junction = junctions[index]
    outgoing_track = context.tracks.get(junction.outgoing.track_id)
    incoming_track = context.tracks.get(junction.incoming.track_id)
    if outgoing_track is None or incoming_track is None:
        return None
    try:
        outgoing, incoming, transition = plan_manual_junction(
            junction.outgoing, outgoing_track, incoming_track, override, context.analyses,
            context.structures, AUTOMIX_SETTINGS, ramp_floor(junctions, index),
        )
    except (ValueError, ZeroDivisionError):
        return None
    return make_junction(junction.index, outgoing, incoming, transition)


def beat_grid(analysis: TrackAnalysis | None) -> tuple[float, ...]:
    """Where a cue may snap: downbeats when known, else beats."""
    if analysis is None:
        return ()
    return analysis.downbeats or analysis.beats


def snap(seconds: float, grid: Sequence[float], tolerance: float | None = None) -> float:
    """``seconds`` moved onto the nearest grid point (within ``tolerance``, if given)."""
    if not grid:
        return seconds
    position = bisect.bisect_left(grid, seconds)
    nearest = min(grid[max(0, position - 1):position + 1], key=lambda point: abs(point - seconds))
    if tolerance is not None and abs(nearest - seconds) > tolerance:
        return seconds
    return float(nearest)


def snap_length(seconds: float, analysis: TrackAnalysis | None) -> float:
    """``seconds`` rounded to whole beats of ``analysis``'s tempo (unchanged without one)."""
    if analysis is None or not analysis.bpm:
        return seconds
    beat = 60.0 / analysis.bpm
    return max(beat, round(seconds / beat) * beat)


def bars_text(seconds: float, analysis: TrackAnalysis | None, korean: bool) -> str:
    if analysis is None or not analysis.bpm:
        return ""
    bar = (analysis.meter_numerator or 4) * 60.0 / analysis.bpm
    bars = seconds / bar
    return f"{bars:.1f}마디" if korean else f"{bars:.1f} bars"


# -- band timing (Custom EQ) ------------------------------------------------------

EQ_PRESETS: dict[str, tuple[str, str, BandWindows]] = {
    # key: (Korean, English, bands)
    "bass_swap": ("베이스 스왑", "Bass swap",
                  tuple(BAND_ENVELOPES[TransitionDsp.BASS_SWAP][band] for band in EQ_BANDS)),
    "vocal_safe_eq": ("보컬 보호", "Vocal-safe",
                      tuple(BAND_ENVELOPES[TransitionDsp.VOCAL_SAFE_EQ][band] for band in EQ_BANDS)),
    "filter_blend": ("필터 블렌드", "Filter blend",
                     tuple(BAND_ENVELOPES[TransitionDsp.FILTER_BLEND][band] for band in EQ_BANDS)),
    "even": ("전체 교차", "Even crossfade", (((0.0, 1.0), (0.0, 1.0)),) * len(EQ_BANDS)),
}
_BAND_NAMES = {
    "low": ("저음", "Lows", f"< {LOW_CROSSOVER_HZ} Hz"),
    "mid": ("중음", "Mids", f"{LOW_CROSSOVER_HZ}–{HIGH_CROSSOVER_HZ} Hz"),
    "high": ("고음", "Highs", f"> {HIGH_CROSSOVER_HZ} Hz"),
}
_DRAW_ORDER = ("high", "mid", "low")
"""Top to bottom, like the diagram above it."""


def move_window(window: Window, kind: str, delta: float) -> Window:
    """``window`` with its body ("move") or one edge ("start"/"end") shifted by ``delta``, kept in 0..1."""
    start, end = window
    if kind == "move":
        delta = min(max(delta, -start), 1.0 - end)
        return start + delta, end + delta
    if kind == "start":
        return min(max(0.0, start + delta), end - MIN_EQ_WINDOW), end
    return start, max(min(1.0, end + delta), start + MIN_EQ_WINDOW)


def band_hole(out_window: Window, in_window: Window) -> float:
    """How much of the overlap (0..1) a band spends with neither song at full level."""
    return max(0.0, in_window[0] - out_window[1])


def with_band(bands: BandWindows, band: str, out_window: Window, in_window: Window) -> BandWindows:
    index = EQ_BANDS.index(band)
    return tuple((out_window, in_window) if position == index else entry for position, entry in enumerate(bands))


class BandTimingEditor(QWidget):
    """Custom EQ: each band's outgoing (orange) and incoming (blue) fade, dragged on one graph.

    Each band lane shows both gain curves over the overlap and two bars under
    them, the outgoing fade on top and the incoming fade below. Drag a bar to
    move that fade, or its end to lengthen it; with ``linked`` both fades of a
    band move together. Drags snap to beats (``beat`` as a fraction of the
    overlap) unless Shift is held. ``changed`` fires on release.
    """

    changed = Signal(object)
    LABEL_WIDTH = 96.0
    LANE = 54.0
    BAR = 7.0
    EDGE_GRAB = 7.0
    AXIS = 20.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.korean = True
        self.linked = True
        self.bands: BandWindows = default_eq_bands()
        self.duration = 8.0
        self.beat: float | None = None
        """One beat as a fraction of the overlap (``None``: no tempo known, snap to 1%)."""
        self._drag: tuple[str, str, str, float, BandWindows] | None = None
        """(band, side "out"/"in", kind, press x, bands at press)."""
        self._hover: tuple[str, str, str] | None = None
        self.setMouseTracking(True)
        self.setMinimumHeight(int(len(EQ_BANDS) * (self.LANE + 8) + self.AXIS + 6))

    def sizeHint(self) -> QSize:
        return QSize(640, self.minimumHeight())

    def set_bands(self, bands: BandWindows, duration: float, beat: float | None) -> None:
        if self._drag is not None:
            return
        self.bands = bands
        self.duration = max(1e-6, duration)
        self.beat = beat
        self.update()

    # -- geometry --------------------------------------------------------------

    def _plot(self) -> tuple[float, float]:
        return self.LABEL_WIDTH, self.width() - 12.0

    def _x(self, progress: float) -> float:
        left, right = self._plot()
        return left + (right - left) * progress

    def _progress_delta(self, dx: float) -> float:
        left, right = self._plot()
        return dx / max(1.0, right - left)

    def _lane_top(self, band: str) -> float:
        return 4.0 + _DRAW_ORDER.index(band) * (self.LANE + 8)

    def _bar_rect(self, band: str, side: str) -> QRectF:
        index = EQ_BANDS.index(band)
        window = self.bands[index][0 if side == "out" else 1]
        top = self._lane_top(band) + self.LANE - 2 * self.BAR - 3 + (0 if side == "out" else self.BAR + 2)
        return QRectF(self._x(window[0]), top, max(2.0, self._x(window[1]) - self._x(window[0])), self.BAR)

    def _hit(self, x: float, y: float) -> tuple[str, str, str] | None:
        for band in EQ_BANDS:
            for side in ("out", "in"):
                rect = self._bar_rect(band, side).adjusted(0, -3, 0, 3)
                if not rect.top() <= y <= rect.bottom():
                    continue
                if abs(x - rect.left()) <= self.EDGE_GRAB:
                    return band, side, "start"
                if abs(x - rect.right()) <= self.EDGE_GRAB:
                    return band, side, "end"
                if rect.left() < x < rect.right():
                    return band, side, "move"
        return None

    # -- editing ---------------------------------------------------------------

    def _snap(self, value: float) -> float:
        step = self.beat if self.beat and self.beat >= 0.01 else 0.01
        return round(value / step) * step

    def _dragged(self, x: float, free: bool) -> BandWindows:
        band, side, kind, press_x, base = self._drag
        index = EQ_BANDS.index(band)
        out_window, in_window = base[index]
        own = out_window if side == "out" else in_window
        delta = self._progress_delta(x - press_x)
        if not free:  # snap the edge that moves (the start, for a whole-bar move)
            anchor = own[1] if kind == "end" else own[0]
            delta = self._snap(anchor + delta) - anchor
        if self.linked:
            out_window, in_window = move_window(out_window, kind, delta), move_window(in_window, kind, delta)
        elif side == "out":
            out_window = move_window(out_window, kind, delta)
        else:
            in_window = move_window(in_window, kind, delta)
        return with_band(base, band, out_window, in_window)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        hit = self._hit(event.position().x(), event.position().y())
        if event.button() != Qt.MouseButton.LeftButton or hit is None:
            super().mousePressEvent(event)
            return
        self._drag = (*hit, event.position().x(), self.bands)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        x = event.position().x()
        if self._drag is not None:
            self.bands = self._dragged(x, bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            self.update()
            return
        hit = self._hit(x, event.position().y())
        if hit != self._hover:
            self._hover = hit
            shape = (Qt.CursorShape.ArrowCursor if hit is None
                     else Qt.CursorShape.SizeAllCursor if hit[2] == "move" else Qt.CursorShape.SizeHorCursor)
            self.setCursor(shape)
            self.setToolTip(self._hit_tip(hit))
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag is None:
            super().mouseReleaseEvent(event)
            return
        base = self._drag[4]
        self._drag = None
        if self.bands != base:
            self.changed.emit(self.bands)

    def leaveEvent(self, _event) -> None:
        if self._hover is not None:
            self._hover = None
            self.update()

    def _hit_tip(self, hit: tuple[str, str, str] | None) -> str:
        if hit is None:
            return ""
        band, side, _kind = hit
        korean, english, _range = _BAND_NAMES[band]
        if self.korean:
            who = "나가는 곡이 사라지는" if side == "out" else "들어오는 곡이 올라오는"
            return f"{korean}: {who} 구간 · 끌어서 옮기고 끝을 끌어 길이를 바꿉니다 (Shift: 비트 무시)"
        who = "the outgoing song fades out" if side == "out" else "the incoming song fades in"
        return f"{english}: where {who} · drag to move, drag an end to resize (Shift: ignore beats)"

    # -- drawing ---------------------------------------------------------------

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        painter.fillRect(self.rect(), palette.base())
        muted = palette.placeholderText().color()
        small = QFont(self.font())
        small.setPointSizeF(max(7.0, small.pointSizeF() - 1.0))
        painter.setFont(small)
        left, right = self._plot()
        grid = QColor(palette.mid().color())
        grid.setAlpha(110)
        for band in _DRAW_ORDER:
            top = self._lane_top(band)
            korean, english, band_range = _BAND_NAMES[band]
            painter.setPen(muted)
            painter.drawText(QRectF(4, top, self.LABEL_WIDTH - 10, self.LANE),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                             f"{korean if self.korean else english}\n{band_range}")
            curve_rect = QRectF(left, top, right - left, self.LANE - 2 * self.BAR - 8)
            painter.setPen(QPen(grid, 1, Qt.PenStyle.DotLine))
            painter.drawLine(QPointF(left, curve_rect.top()), QPointF(right, curve_rect.top()))
            painter.drawLine(QPointF(left, curve_rect.bottom()), QPointF(right, curve_rect.bottom()))
            out_window, in_window = self.bands[EQ_BANDS.index(band)]
            hole = band_hole(out_window, in_window)
            if hole > 0.005:
                gap = QColor("#EF4444")
                gap.setAlpha(40)
                painter.fillRect(QRectF(self._x(out_window[1]), curve_rect.top(),
                                        self._x(in_window[0]) - self._x(out_window[1]), curve_rect.height()), gap)
            for window, color, entering in ((out_window, OUTGOING_COLOR, False), (in_window, INCOMING_COLOR, True)):
                self._draw_curve(painter, curve_rect, window, color, entering)
            for side, color in (("out", OUTGOING_COLOR), ("in", INCOMING_COLOR)):
                rect = self._bar_rect(band, side)
                hot = self._drag is not None and self._drag[:2] == (band, side) or (
                    self._hover is not None and self._hover[:2] == (band, side))
                fill = QColor(color)
                fill.setAlpha(230 if hot else 150)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(fill)
                painter.drawRoundedRect(rect, 3, 3)
                painter.setBrush(color.lighter(130))
                for edge in (rect.left(), rect.right()):
                    painter.drawRoundedRect(QRectF(edge - 2.5, rect.top() - 2, 5, rect.height() + 4), 2, 2)
            if hole > 0.005:
                painter.setPen(QColor("#F87171"))
                painter.drawText(QRectF(left, top - 2, right - left - 4, 14), Qt.AlignmentFlag.AlignRight,
                                 (f"⚠ {hole * self.duration:.1f}초 동안 이 대역이 비어요" if self.korean
                                  else f"⚠ this band is empty for {hole * self.duration:.1f}s"))
        bottom = self.height() - self.AXIS
        painter.setPen(muted)
        painter.drawLine(QPointF(left, bottom + 2), QPointF(right, bottom + 2))
        for step in range(5):
            progress = step / 4
            x = self._x(progress)
            painter.drawLine(QPointF(x, bottom + 2), QPointF(x, bottom + 5))
            text = f"{progress * self.duration:.1f}s"
            box = QRectF(min(max(x - 30, left - 30), right - 60), bottom + 4, 60, 14)
            painter.drawText(box, (Qt.AlignmentFlag.AlignRight if step == 4 else Qt.AlignmentFlag.AlignHCenter), text)

    def _draw_curve(self, painter: QPainter, rect: QRectF, window: Window, color: QColor, entering: bool) -> None:
        start, end = window
        path = QPainterPath()
        steps = 120
        for step in range(steps + 1):
            progress = step / steps
            amount = min(1.0, max(0.0, (progress - start) / max(1e-6, end - start)))
            gain = math.sin(amount * math.pi / 2) if entering else math.cos(amount * math.pi / 2)
            point = QPointF(self._x(progress), rect.bottom() - gain * rect.height())
            if step == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)
        painter.setPen(QPen(color, 1.8, Qt.PenStyle.DashLine if entering else Qt.PenStyle.SolidLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)


class TransitionEditorPanel(QWidget):
    """The manual controls: style cards, exact numbers, tempo match and snapping.

    ``edited`` carries a complete TransitionOverride whenever the user changes
    something; number fields debounce so typing is one edit.
    """

    edited = Signal(object)
    revert_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.korean = True
        self._override: TransitionOverride | None = None
        self._syncing = False
        self._analyses: tuple[TrackAnalysis | None, TrackAnalysis | None] = (None, None)
        self._current_bands: BandWindows | None = None
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.setInterval(450)
        self._commit_timer.timeout.connect(self._commit)

        self.style_title = QLabel()
        self.style_title.setObjectName("panelTitle")
        self.style_group = QButtonGroup(self)
        self.style_group.setExclusive(True)
        self.style_buttons: dict[str, QToolButton] = {}
        styles = QGridLayout()
        styles.setHorizontalSpacing(6)
        styles.setVerticalSpacing(6)
        for position, style in enumerate(MANUAL_STYLES):
            button = QToolButton()
            button.setObjectName("transitionStyleCard")
            button.setCheckable(True)
            button.setMinimumHeight(34)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.style_group.addButton(button)
            self.style_buttons[style] = button
            styles.addWidget(button, position // 5, position % 5)
            button.clicked.connect(lambda _checked=False, style=style: self._set_style(style))

        self.outgoing_label = QLabel()
        self.outgoing_spin = self._spin()
        self.incoming_label = QLabel()
        self.incoming_spin = self._spin()
        self.length_label = QLabel()
        self.length_spin = self._spin(maximum=MAX_DURATION_SECONDS)
        self.length_spin.setMinimum(MIN_EDIT_SECONDS)
        self.bars_label = QLabel()
        self.bars_label.setObjectName("mutedLabel")
        numbers = QGridLayout()
        numbers.setHorizontalSpacing(8)
        numbers.setVerticalSpacing(4)
        for column, (label, spin) in enumerate((
            (self.outgoing_label, self.outgoing_spin), (self.incoming_label, self.incoming_spin),
            (self.length_label, self.length_spin),
        )):
            label.setObjectName("mutedLabel")
            numbers.addWidget(label, 0, column)
            numbers.addWidget(spin, 1, column)
        numbers.addWidget(self.bars_label, 1, 3)
        numbers.setColumnStretch(3, 1)

        self.tempo_check = QCheckBox()
        self.tempo_check.toggled.connect(lambda _checked: self._commit())
        self.snap_check = QCheckBox()
        self.snap_check.setChecked(True)
        self.handoff_label = QLabel()
        self.handoff_label.setObjectName("mutedLabel")
        self.handoff_slider = QSlider(Qt.Orientation.Horizontal)
        self.handoff_slider.setRange(10, 90)
        self.handoff_slider.setValue(52)
        self.handoff_slider.setMaximumWidth(180)
        self.handoff_slider.sliderReleased.connect(self._commit)
        self.handoff_slider.valueChanged.connect(self._handoff_changed)
        self.revert_button = QPushButton()
        self.revert_button.clicked.connect(self.revert_requested)
        options = QHBoxLayout()
        options.setSpacing(12)
        options.addWidget(self.tempo_check)
        options.addWidget(self.snap_check)
        options.addWidget(self.handoff_label)
        options.addWidget(self.handoff_slider)
        options.addStretch(1)
        options.addWidget(self.revert_button)

        self.eq_title = QLabel()
        self.eq_title.setObjectName("panelTitle")
        self.eq_preset_label = QLabel()
        self.eq_preset_label.setObjectName("mutedLabel")
        self.eq_presets: dict[str, QToolButton] = {}
        presets = QHBoxLayout()
        presets.setSpacing(6)
        presets.addWidget(self.eq_title)
        presets.addSpacing(8)
        presets.addWidget(self.eq_preset_label)
        for key in EQ_PRESETS:
            button = QToolButton()
            button.setObjectName("eqPresetButton")
            button.clicked.connect(lambda _checked=False, key=key: self._commit(eq_bands=EQ_PRESETS[key][2]))
            self.eq_presets[key] = button
            presets.addWidget(button)
        presets.addStretch(1)
        self.eq_link_check = QCheckBox()
        self.eq_link_check.setChecked(True)
        presets.addWidget(self.eq_link_check)
        self.eq_editor = BandTimingEditor()
        self.eq_editor.changed.connect(lambda bands: self._commit(eq_bands=bands))
        self.eq_link_check.toggled.connect(lambda checked: setattr(self.eq_editor, "linked", checked))
        self.eq_hint = QLabel()
        self.eq_hint.setObjectName("mutedLabel")
        self.eq_hint.setWordWrap(True)
        self.eq_box = QWidget()
        eq_layout = QVBoxLayout(self.eq_box)
        eq_layout.setContentsMargins(0, 4, 0, 0)
        eq_layout.setSpacing(4)
        eq_layout.addLayout(presets)
        eq_layout.addWidget(self.eq_editor)
        eq_layout.addWidget(self.eq_hint)
        self.eq_box.hide()

        self.hint_label = QLabel()
        self.hint_label.setObjectName("mutedLabel")
        self.hint_label.setWordWrap(True)
        self.status_label = QLabel()
        self.status_label.setObjectName("previewStatusChip")
        self.status_label.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.hint_label)
        layout.addWidget(self.style_title)
        layout.addLayout(styles)
        layout.addLayout(numbers)
        layout.addLayout(options)
        layout.addWidget(self.eq_box)
        layout.addWidget(self.status_label, 0, Qt.AlignmentFlag.AlignLeft)

        self.retranslate(True)

    def _spin(self, maximum: float = 36_000.0) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(2)
        spin.setRange(0.0, maximum)
        spin.setSingleStep(0.1)
        spin.setKeyboardTracking(False)
        spin.setMinimumWidth(110)
        spin.valueChanged.connect(lambda _value: None if self._syncing else self._commit_timer.start())
        return spin

    @property
    def override(self) -> TransitionOverride | None:
        return self._override

    @property
    def snapping(self) -> bool:
        return self.snap_check.isChecked()

    def set_override(
        self, override: TransitionOverride | None, analyses: tuple[TrackAnalysis | None, TrackAnalysis | None] = (None, None),
        durations: tuple[float, float] | None = None,
    ) -> None:
        """Show ``override`` (no signal)."""
        self._commit_timer.stop()
        self._override = override
        self._analyses = analyses
        if override is None:
            return
        self._syncing = True
        try:
            if durations is not None:
                self.outgoing_spin.setMaximum(max(0.0, durations[0]))
                self.incoming_spin.setMaximum(max(0.0, durations[1]))
            self.outgoing_spin.setValue(override.outgoing_cue)
            self.incoming_spin.setValue(override.incoming_cue)
            self.length_spin.setValue(max(MIN_EDIT_SECONDS, override.duration))
            self.tempo_check.setChecked(override.tempo_match)
            self.style_buttons[override.style].setChecked(True)
            handoff = override.vocal_handoff if override.vocal_handoff is not None else 0.525
            self.handoff_slider.setValue(round(handoff * 100))
        finally:
            self._syncing = False
        self._refresh_state()

    def set_current_bands(self, bands: BandWindows | None) -> None:
        """The band timing playing now (``None``: not a band style); where Custom EQ starts."""
        self._current_bands = bands

    def _bands_for(self, override: TransitionOverride) -> BandWindows:
        if override.eq_bands is not None:
            return override.eq_bands
        return self._current_bands if self._current_bands is not None else default_eq_bands()

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))

    def _refresh_state(self) -> None:
        override = self._override
        cut = override is not None and override.style == "cut"
        self.length_spin.setEnabled(not cut)
        self.tempo_check.setEnabled(not cut and all(a is not None and a.bpm for a in self._analyses))
        vocal = override is not None and override.style == "vocal_safe_eq"
        self.handoff_label.setVisible(vocal)
        self.handoff_slider.setVisible(vocal)
        self._handoff_changed(self.handoff_slider.value())
        eq = override is not None and override.style == STYLE_EQ
        self.eq_box.setVisible(eq)
        if eq:
            incoming = self._analyses[1]
            beat = (60.0 / incoming.bpm / override.duration
                    if incoming is not None and incoming.bpm and override.duration > 0 else None)
            self.eq_editor.set_bands(self._bands_for(override), override.duration, beat)
        self.bars_label.setText("" if cut or override is None
                                else bars_text(override.duration, self._analyses[1], self.korean))

    def _handoff_changed(self, value: int) -> None:
        self.handoff_label.setText(("보컬 교대 " if self.korean else "Vocal handoff ") + f"{value}%")

    def _set_style(self, style: str) -> None:
        if self._override is not None and style != self._override.style:
            self._commit(style=style)

    def _commit(self, *, style: str | None = None, eq_bands: BandWindows | None = None) -> None:
        if self._syncing or self._override is None:
            return
        self._commit_timer.stop()
        style = style or self._override.style
        if eq_bands is not None:
            style = STYLE_EQ
        elif style == STYLE_EQ:
            eq_bands = self._bands_for(self._override)
        else:
            eq_bands = self._override.eq_bands  # kept, so switching back to Custom EQ finds it
        handoff = self.handoff_slider.value() / 100.0 if style == "vocal_safe_eq" else None
        try:
            override = replace(
                self._override,
                outgoing_cue=self.outgoing_spin.value(),
                incoming_cue=self.incoming_spin.value(),
                duration=self.length_spin.value(),
                style=style,
                tempo_match=self.tempo_check.isChecked(),
                vocal_handoff=None if handoff is not None and abs(handoff - 0.525) < 0.011 else handoff,
                eq_bands=eq_bands,
            )
        except ValueError:
            return
        if override == self._override:
            return
        self._override = override
        self._refresh_state()
        self.edited.emit(override)

    def retranslate(self, korean: bool) -> None:
        self.korean = korean
        self.style_title.setText("믹스 스타일" if korean else "Mix style")
        for style, button in self.style_buttons.items():
            names = STYLE_CHOICES[style]
            button.setText(names[0] if korean else names[1])
            button.setToolTip(names[2] if korean else names[3])
        seconds = " 초" if korean else " s"
        for spin in (self.outgoing_spin, self.incoming_spin, self.length_spin):
            spin.setSuffix(seconds)
        self.outgoing_label.setText("나가는 곡 믹스 시작" if korean else "Outgoing mix starts at")
        self.incoming_label.setText("들어오는 곡 재생 시작" if korean else "Incoming starts from")
        self.length_label.setText("겹침 길이" if korean else "Overlap length")
        self.outgoing_spin.setToolTip("나가는 곡의 이 위치에서 다음 곡이 섞이기 시작합니다." if korean
                                      else "Where in the outgoing song the next one starts mixing in.")
        self.incoming_spin.setToolTip("들어오는 곡을 이 위치부터 재생합니다. 인트로를 건너뛸 때 씁니다." if korean
                                      else "Where the incoming song starts playing; use it to skip an intro.")
        self.tempo_check.setText("템포 맞춤" if korean else "Match tempo")
        self.tempo_check.setToolTip(
            "나가는 곡을 들어오는 곡의 템포에 서서히 맞춰 박자가 겹치게 합니다. 두 곡의 BPM 분석이 필요합니다."
            if korean else "Eases the outgoing song onto the incoming tempo so the beats line up. Needs both BPMs.")
        self.snap_check.setText("비트에 맞춤" if korean else "Snap to beat")
        self.snap_check.setToolTip("끌 때 마디·비트에 붙습니다. Shift를 누르고 끌면 자유롭게 움직입니다." if korean
                                   else "Drags snap to bars and beats; hold Shift to move freely.")
        self.revert_button.setText("자동으로 되돌리기" if korean else "Back to automatic")
        self.eq_editor.korean = korean
        self.eq_editor.update()
        self.eq_title.setText("대역별 타이밍" if korean else "Band timing")
        self.eq_preset_label.setText("불러오기:" if korean else "Start from:")
        for key, button in self.eq_presets.items():
            button.setText(EQ_PRESETS[key][0 if korean else 1])
        self.eq_link_check.setText("두 곡 함께 옮기기" if korean else "Move both songs together")
        self.eq_link_check.setToolTip(
            "켜면 한 대역에서 나가는 곡과 들어오는 곡의 구간이 함께 움직여 맞교대가 유지됩니다." if korean
            else "Moves a band's outgoing and incoming fades together, so they keep swapping at the same time.")
        self.eq_hint.setText(
            "주황 막대는 나가는 곡이 사라지는 구간, 파란 막대는 들어오는 곡이 올라오는 구간입니다. "
            "막대를 끌어 옮기고 끝을 끌어 길이를 바꿉니다. 저음은 짧게 맞바꾸면 두 베이스가 겹치지 않습니다."
            if korean else
            "Orange bars are where the outgoing song fades out, blue bars where the incoming one fades in. "
            "Drag a bar to move it and an end to resize it. A short low-band swap keeps two bass lines apart.")
        self.hint_label.setText(
            "그래프에서 겹침 구간을 끌어 위치를, 양 끝을 끌어 길이를, 들어오는 곡을 끌어 시작 지점을 바꿉니다."
            if korean else
            "In the graph, drag the overlap to move it, its edges to resize it, and the incoming song to "
            "change where it starts.")
        self._refresh_state()
