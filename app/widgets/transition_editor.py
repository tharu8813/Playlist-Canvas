"""Manual transition editing for the AutoMix editor.

Pure helpers (prefill an override from what is playing, draft a junction from
an override, snap to the beat grid, what a drag on the timeline sets) plus
TransitionPropertiesPanel, the editor's side panel. Nothing here renders
audio: an edit is committed as a TransitionOverride, the editor re-plans and
auditions the one window it changed.
"""

from __future__ import annotations

import bisect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QSlider, QToolButton, QVBoxLayout, QWidget,
)

from app.automix.models import TrackAnalysis
from app.automix.overrides import (
    EQ_BANDS, MANUAL_STYLES, MAX_DURATION_SECONDS, MIN_EQ_WINDOW, STYLE_AUTO, STYLE_CUT, STYLE_EQ,
    BandWindows, TransitionOverride, Window, pair_key,
)
from app.automix.renderer import BAND_ENVELOPES
from app.automix.structure.models import TrackStructureAnalysis
from app.models.playlist import PlaylistTrack
from app.timeline.render_plan import TransitionDsp
from app.widgets.transition_inspector import Junction, _clock, make_junction

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
        # Rounded to the microsecond: planner arithmetic leaves 184.00000000000003-style noise.
        return TransitionOverride(
            outgoing_cue=round(max(0.0, float(cue if isinstance(cue, (int, float)) else
                                              junction.outgoing.source_at(junction.start))), 6),
            incoming_cue=round(max(0.0, float(junction.incoming.source_in)), 6),
            duration=min(MAX_DURATION_SECONDS, float(transition.duration)),
            style=STYLE_AUTO,
            tempo_match=isinstance(rate, (int, float)) and abs(float(rate) - 1.0) > 1e-9,
        )
    # No overlap now (an automatic cut or back-to-back junction): keep that exact
    # boundary as a manual cut; picking a style then opens an 8 s overlap there.
    return TransitionOverride(
        outgoing_cue=round(max(0.0, float(junction.outgoing.source_out)), 6),
        incoming_cue=round(max(0.0, float(junction.incoming.source_in)), 6),
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


def snap_length(seconds: float, analysis: TrackAnalysis | None, tolerance: float | None = None) -> float:
    """``seconds`` rounded to whole beats of ``analysis``'s tempo (unchanged without one).

    With ``tolerance`` it is magnetic: whole bars pull from ``tolerance``
    seconds away, whole beats from 60% of that, and anything else stays free.
    """
    if analysis is None or not analysis.bpm:
        return seconds
    beat = 60.0 / analysis.bpm
    if tolerance is None:
        return max(beat, round(seconds / beat) * beat)
    bar = beat * (analysis.meter_numerator or 4)
    for step, reach in ((bar, tolerance), (beat, tolerance * 0.6)):
        if step < 3.0 * reach:
            continue
        nearest = max(step, round(seconds / step) * step)
        if abs(nearest - seconds) <= reach:
            return nearest
    return seconds


def magnet(seconds: float, analysis: TrackAnalysis | None, tolerance: float) -> float:
    """``seconds`` pulled onto a nearby downbeat (within ``tolerance``) or beat (60% of it); else unchanged.

    Unlike a hard grid this lets a drag land anywhere, while still clicking onto
    the beat when the pointer passes close to one.
    """
    if analysis is None:
        return seconds
    beat = 60.0 / analysis.bpm if analysis.bpm else 0.0
    bar = beat * (analysis.meter_numerator or 4)
    for grid, reach, spacing in ((analysis.downbeats, tolerance, bar), (analysis.beats, tolerance * 0.6, beat)):
        # A grid denser than 3x its pull would catch every position: at that zoom it stays off.
        if grid and (not spacing or spacing >= 3.0 * reach):
            snapped = snap(seconds, grid, reach)
            if snapped != seconds:
                return snapped
    return seconds


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


# -- timeline drags -------------------------------------------------------------------

def drag_override(
    junctions: Sequence[Junction], index: int, drawn: Junction, base: TransitionOverride, kind: str,
    delta: float, context: EditContext, *, snapping: bool, tolerance: float, korean: bool,
) -> tuple[TransitionOverride | None, str, float | None]:
    """What dragging ``kind`` ("move", "start", "end" or "incoming") by ``delta`` timeline seconds sets.

    ``drawn`` is the junction as it looked when the drag began and ``base``
    its override. The part under the pointer follows it, clicks onto nearby
    bars and beats (``snapping``; ``tolerance`` seconds), and stops at what the
    songs allow, saying why. Returns (override or None, the hint to show, the
    timeline second it snapped to or None).
    """
    outgoing_analysis = context.analyses.get(drawn.outgoing.track_id)
    incoming_analysis = context.analyses.get(drawn.incoming.track_id)
    outgoing_track = context.tracks.get(drawn.outgoing.track_id)
    incoming_track = context.tracks.get(drawn.incoming.track_id)
    head = replace(drawn.outgoing, source_out=max(drawn.outgoing.source_out,
                                                  outgoing_track.duration_seconds if outgoing_track else 0.0))
    incoming_length = incoming_track.duration_seconds if incoming_track is not None else drawn.incoming.source_out
    floor = head.timeline_at(ramp_floor(junctions, index))
    length = drawn.end - drawn.start
    limit = ""
    guide: float | None = None
    changes: dict[str, float] = {}
    if kind in ("move", "start"):
        start = drawn.start + delta
        if snapping:
            snapped = head.timeline_at(magnet(head.source_at(start), outgoing_analysis, tolerance))
            guide = snapped if abs(snapped - start) > 1e-9 else None
            start = snapped
        latest = head.timeline_end - length if kind == "move" else drawn.end - MIN_EDIT_SECONDS
        if start > latest:
            start, limit, guide = latest, ("나가는 곡 끝" if korean else "outgoing song ends"), None
        if start < floor:
            start, limit, guide = floor, ("앞 전환 끝" if korean else "previous transition"), None
        if kind == "start" and drawn.end - start > MAX_DURATION_SECONDS:
            start, limit, guide = drawn.end - MAX_DURATION_SECONDS, ("최대 60초" if korean else "60 s max"), None
        changes["outgoing_cue"] = max(0.0, head.source_at(start))
        changes["duration"] = length if kind == "move" else drawn.end - start
        hint = f"{'믹스 시작' if korean else 'Mix starts'} {_clock(start, precise=True)} · {changes['duration']:.2f}s"
    elif kind == "end":
        new_length = length + delta
        if snapping:
            snapped = snap_length(new_length, incoming_analysis, tolerance)
            guide = drawn.start + snapped if abs(snapped - new_length) > 1e-9 else None
            new_length = snapped
        longest = min(MAX_DURATION_SECONDS, head.timeline_end - drawn.start, incoming_length - base.incoming_cue)
        if new_length > longest:
            which = (0 if longest >= MAX_DURATION_SECONDS - 1e-6
                     else 1 if longest >= head.timeline_end - drawn.start - 1e-6 else 2)
            new_length, guide = longest, None
            limit = (("최대 60초", "나가는 곡 끝", "들어오는 곡 끝") if korean
                     else ("60 s max", "outgoing song ends", "incoming song ends"))[which]
        if new_length < MIN_EDIT_SECONDS:
            new_length, limit, guide = MIN_EDIT_SECONDS, ("최소 길이" if korean else "shortest"), None
        changes["duration"] = new_length
        bars = bars_text(new_length, incoming_analysis, korean)
        hint = f"{'겹침' if korean else 'Overlap'} {new_length:.2f}s" + (f" · {bars}" if bars else "")
    else:  # "incoming": slide the incoming song under its window (skip or keep its intro)
        cue = base.incoming_cue - delta
        if snapping:
            snapped = magnet(cue, incoming_analysis, tolerance)
            if abs(snapped) <= tolerance:
                snapped = 0.0
            guide = drawn.start if abs(snapped - cue) > 1e-9 else None
            cue = snapped
        latest = max(0.0, incoming_length - length)
        if cue < 0.0:
            cue, limit, guide = 0.0, ("곡의 맨 앞" if korean else "start of the song"), None
        elif cue > latest:
            cue, limit, guide = latest, ("들어오는 곡 끝" if korean else "incoming song ends"), None
        changes["incoming_cue"] = cue
        hint = f"{'B 시작' if korean else 'B starts at'} {_clock(cue, precise=True)}"
    if "duration" in changes:
        changes["duration"] = min(MAX_DURATION_SECONDS, max(MIN_EDIT_SECONDS, changes["duration"]))
    if limit:
        hint += f"  ▸ {limit}에서 멈춤" if korean else f"  ▸ stops at {limit}"
    elif guide is not None:
        hint += "  ◆ 박자" if korean else "  ◆ on the beat"
    try:
        return replace(base, **changes), hint, guide
    except ValueError:
        return None, hint, None


def edited_override(
    base: TransitionOverride, changes: Mapping[str, object], current_bands: BandWindows | None,
) -> TransitionOverride:
    """``base`` with the properties panel's ``changes`` applied.

    Custom EQ keeps its own band windows when it has them, else starts from
    what plays now (``current_bands``: the band timing rendered at the moment,
    ``None`` when that is not a band style) or the bass swap.
    """
    from app.automix.renderer import default_eq_bands

    values = dict(changes)
    if "eq_bands" in values:
        values["style"] = STYLE_EQ
    elif values.get("style", base.style) == STYLE_EQ and base.eq_bands is None:
        values["eq_bands"] = current_bands if current_bands is not None else default_eq_bands()
    return replace(base, **values)


# -- side panel -------------------------------------------------------------------------

_BASIC_STYLES = (STYLE_AUTO, "legacy", "vocal_safe_eq", STYLE_CUT)
LENGTH_PRESETS = (4.0, 8.0, 16.0)


class TransitionPropertiesPanel(QWidget):
    """The AutoMix editor's side panel: style, length, cues, tempo, vocal handoff, band presets.

    Shows one junction (``set_state``; never emits while doing so) and reports
    what the user changed as ``changed({field: value})`` -- the editor turns
    that into an override and an undo step. Number fields debounce so typing
    is one edit; ``flush()`` commits a pending one at once (before the
    selection moves or the window closes, so it lands on the junction it was
    typed for). Simple mode hides the precise controls without touching them.
    """

    changed = Signal(dict)
    reset_requested = Signal()
    link_toggled = Signal(bool)

    DEBOUNCE_MS = 450

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("automixProperties")
        self.korean = True
        self.advanced = False
        self._override: TransitionOverride | None = None
        self._manual = False
        self._band_style = False
        self._auto_style = ""
        self._analyses: tuple[TrackAnalysis | None, TrackAnalysis | None] = (None, None)
        self._syncing = False
        self._pending: dict[str, object] = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.DEBOUNCE_MS)
        self._timer.timeout.connect(self.flush)

        self.pair_label = QLabel()
        self.pair_label.setObjectName("mutedLabel")
        self.songs_label = QLabel()
        self.songs_label.setWordWrap(True)
        self.songs_label.setTextFormat(Qt.TextFormat.RichText)
        self.mode_chip = QLabel()
        self.mode_chip.setObjectName("automixModeChip")
        self.reset_button = QPushButton()
        self.reset_button.clicked.connect(self.reset_requested)

        self.style_title = self._title()
        self.style_group = QButtonGroup(self)
        self.style_buttons: dict[str, QToolButton] = {}
        self.style_grid = QGridLayout()
        self.style_grid.setSpacing(6)
        for style in MANUAL_STYLES:
            button = QToolButton()
            button.setObjectName("transitionStyleCard")
            button.setCheckable(True)
            button.setMinimumHeight(34)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda _checked=False, style=style: self._emit({"style": style}))
            self.style_group.addButton(button)
            self.style_buttons[style] = button
        self.style_description = QLabel()
        self.style_description.setObjectName("mutedLabel")
        self.style_description.setWordWrap(True)

        self.length_title = self._title()
        self.length_presets: dict[float, QToolButton] = {}
        length_row = QHBoxLayout()
        length_row.setSpacing(6)
        for seconds in LENGTH_PRESETS:
            button = QToolButton()
            button.setObjectName("eqPresetButton")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, seconds=seconds: self._emit({"duration": seconds}))
            self.length_presets[seconds] = button
            length_row.addWidget(button)
        length_row.addStretch(1)
        self.length_spin = self._spin(MAX_DURATION_SECONDS, "duration")
        self.length_spin.setMinimum(MIN_EDIT_SECONDS)
        self.bars_label = QLabel()
        self.bars_label.setObjectName("mutedLabel")
        length_value = QHBoxLayout()
        length_value.addWidget(self.length_spin)
        length_value.addWidget(self.bars_label, 1)

        self.cue_title = self._title()
        self.outgoing_label = QLabel()
        self.outgoing_spin = self._spin(36_000.0, "outgoing_cue")
        self.incoming_label = QLabel()
        self.incoming_spin = self._spin(36_000.0, "incoming_cue")
        cues = QGridLayout()
        cues.setHorizontalSpacing(8)
        cues.setVerticalSpacing(6)
        for row, (label, spin) in enumerate(((self.outgoing_label, self.outgoing_spin),
                                             (self.incoming_label, self.incoming_spin))):
            cues.addWidget(label, row, 0)
            cues.addWidget(spin, row, 1)
        cues.setColumnStretch(1, 1)

        self.tempo_title = self._title()
        self.tempo_check = QCheckBox()
        self.tempo_check.toggled.connect(lambda checked: self._emit({"tempo_match": checked}))
        self.tempo_info = QLabel()
        self.tempo_info.setObjectName("mutedLabel")
        self.tempo_info.setWordWrap(True)

        self.handoff_label = QLabel()
        self.handoff_slider = QSlider(Qt.Orientation.Horizontal)
        self.handoff_slider.setRange(10, 90)
        self.handoff_slider.valueChanged.connect(self._handoff_moved)
        self.handoff_slider.sliderReleased.connect(self.flush)
        self.handoff_value = QLabel()
        self.handoff_value.setMinimumWidth(40)
        self.handoff_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        handoff_row = QHBoxLayout()
        handoff_row.addWidget(self.handoff_slider, 1)
        handoff_row.addWidget(self.handoff_value)

        self.band_title = self._title()
        self.eq_presets: dict[str, QToolButton] = {}
        presets = QGridLayout()
        presets.setSpacing(6)
        for position, key in enumerate(EQ_PRESETS):
            button = QToolButton()
            button.setObjectName("eqPresetButton")
            button.clicked.connect(lambda _checked=False, key=key: self._emit({"eq_bands": EQ_PRESETS[key][2]}))
            self.eq_presets[key] = button
            presets.addWidget(button, position // 2, position % 2)
        self.link_check = QCheckBox()
        self.link_check.setChecked(True)
        self.link_check.toggled.connect(self.link_toggled)
        self.band_hint = QLabel()
        self.band_hint.setObjectName("mutedLabel")
        self.band_hint.setWordWrap(True)

        self.facts_title = self._title()
        self.facts = QGridLayout()
        self.facts.setHorizontalSpacing(12)
        self.facts.setVerticalSpacing(4)
        self._fact_labels: list[tuple[QLabel, QLabel]] = []

        def group(*items) -> QWidget:
            box = QWidget()
            layout = QVBoxLayout(box)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(6)
            for item in items:
                layout.addLayout(item) if not isinstance(item, QWidget) else layout.addWidget(item)
            return box

        mode_row = QHBoxLayout()
        mode_row.addWidget(self.mode_chip)
        mode_row.addStretch(1)
        mode_row.addWidget(self.reset_button)
        self.cue_box = group(self.cue_title, cues)
        self.tempo_box = group(self.tempo_title, self.tempo_check, self.tempo_info)
        self.handoff_box = group(self.handoff_label, handoff_row)
        self.band_box = group(self.band_title, presets, self.link_check, self.band_hint)
        self.length_box = group(self.length_title, length_row, length_value)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(14)
        layout.addWidget(group(self.pair_label, self.songs_label, mode_row))
        layout.addWidget(group(self.style_title, self.style_grid, self.style_description))
        layout.addWidget(self.length_box)
        layout.addWidget(self.cue_box)
        layout.addWidget(self.tempo_box)
        layout.addWidget(self.handoff_box)
        layout.addWidget(self.band_box)
        layout.addWidget(group(self.facts_title, self.facts))
        layout.addStretch(1)
        self._layout_styles()
        self.retranslate(True)

    @staticmethod
    def _title() -> QLabel:
        label = QLabel()
        label.setObjectName("panelTitle")
        return label

    def _spin(self, maximum: float, field_name: str) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(2)
        spin.setRange(0.0, maximum)
        spin.setSingleStep(0.1)
        spin.setKeyboardTracking(False)
        spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        spin.setMinimumWidth(110)
        spin.valueChanged.connect(lambda value: self._queue(field_name, value))
        return spin

    # -- state -------------------------------------------------------------------

    @property
    def override(self) -> TransitionOverride | None:
        return self._override

    @property
    def pending(self) -> bool:
        return bool(self._pending)

    def set_state(
        self, override: TransitionOverride | None, *, manual: bool, pair_text: str, songs_html: str,
        analyses: tuple[TrackAnalysis | None, TrackAnalysis | None] = (None, None),
        durations: tuple[float, float] | None = None, band_style: bool = False, auto_style: str = "",
        tempo_text: str = "", facts: Sequence[tuple[str, str]] = (),
    ) -> None:
        """Show one junction: ``override`` is what it plays (manual or prefilled from the automatic plan)."""
        self._timer.stop()
        self._pending.clear()
        self._override, self._manual = override, manual
        self._band_style, self._auto_style = band_style, auto_style
        self._analyses = analyses
        self.pair_label.setText(pair_text)
        self.songs_label.setText(songs_html)
        self.tempo_info.setText(tempo_text)
        self._set_facts(facts)
        self.setEnabled(override is not None)
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
            self.handoff_slider.setValue(round((override.vocal_handoff or 0.525) * 100))
            self.style_group.setExclusive(False)
            for style, button in self.style_buttons.items():
                button.setChecked(manual and style == override.style)
            self.style_group.setExclusive(True)
        finally:
            self._syncing = False
        self._refresh()

    def set_advanced(self, advanced: bool) -> None:
        self.advanced = advanced
        self._layout_styles()
        self._refresh()

    def flush(self) -> None:
        """Commit a pending (debounced) number edit now."""
        self._timer.stop()
        if self._pending:
            changes, self._pending = self._pending, {}
            self.changed.emit(changes)

    def _queue(self, field_name: str, value: object) -> None:
        if self._syncing or self._override is None:
            return
        self._pending[field_name] = value
        self._timer.start()

    def _emit(self, changes: dict[str, object]) -> None:
        if self._syncing or self._override is None:
            return
        self.flush()
        self.changed.emit(changes)

    def _handoff_moved(self, value: int) -> None:
        self.handoff_value.setText(f"{value}%")
        self._queue("vocal_handoff", value / 100.0)

    def _layout_styles(self) -> None:
        shown = MANUAL_STYLES if self.advanced else _BASIC_STYLES
        for button in self.style_buttons.values():
            self.style_grid.removeWidget(button)
            button.hide()
        for position, style in enumerate(shown):
            button = self.style_buttons[style]
            self.style_grid.addWidget(button, position // 2, position % 2)
            button.show()

    def _refresh(self) -> None:
        override, korean = self._override, self.korean
        cut = override is not None and override.style == STYLE_CUT
        for seconds, button in self.length_presets.items():
            button.setChecked(not cut and override is not None and abs(override.duration - seconds) < 0.01)
        self.length_box.setEnabled(not cut)
        self.cue_box.setVisible(self.advanced)
        self.tempo_box.setVisible(self.advanced)
        both_bpms = all(analysis is not None and analysis.bpm for analysis in self._analyses)
        self.tempo_check.setEnabled(not cut and both_bpms)
        self.handoff_box.setVisible(self.advanced and override is not None and override.style == "vocal_safe_eq")
        self.band_box.setVisible(self.advanced and (self._band_style or (override is not None
                                                                          and override.style == STYLE_EQ)))
        self.handoff_value.setText(f"{self.handoff_slider.value()}%")
        self.reset_button.setEnabled(self._manual)
        self.mode_chip.setProperty("manual", self._manual)
        self.mode_chip.style().unpolish(self.mode_chip)
        self.mode_chip.style().polish(self.mode_chip)
        if override is None:
            self.mode_chip.setText("")
            self.style_description.clear()
            self.bars_label.clear()
            return
        if self._manual:
            self.mode_chip.setText("✎ 직접 설정" if korean else "✎ Set by hand")
            names = STYLE_CHOICES[override.style]
            self.style_description.setText(names[2 if korean else 3])
        else:
            self.mode_chip.setText("● 자동 (분석)" if korean else "● Automatic")
            self.style_description.setText(
                (f"지금은 분석이 고른 ‘{self._auto_style}’로 섞입니다. 값을 바꾸면 직접 설정으로 바뀝니다."
                 if korean else
                 f"Analysis picked “{self._auto_style}”. Changing anything sets this transition by hand.")
                if self._auto_style else
                ("분석이 전환을 정합니다. 값을 바꾸면 직접 설정으로 바뀝니다." if korean
                 else "Analysis decides this transition. Changing anything sets it by hand."))
        self.bars_label.setText("" if cut else bars_text(override.duration, self._analyses[1], korean))

    def _set_facts(self, facts: Sequence[tuple[str, str]]) -> None:
        while len(self._fact_labels) < len(facts):
            name, value = QLabel(), QLabel()
            name.setObjectName("mutedLabel")
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row = len(self._fact_labels)
            self.facts.addWidget(name, row, 0)
            self.facts.addWidget(value, row, 1)
            self._fact_labels.append((name, value))
        for row, (name, value) in enumerate(self._fact_labels):
            shown = row < len(facts)
            name.setVisible(shown)
            value.setVisible(shown)
            if shown:
                name.setText(facts[row][0])
                value.setText(facts[row][1])

    def retranslate(self, korean: bool) -> None:
        self.korean = korean
        self.reset_button.setText("자동으로 되돌리기" if korean else "Back to automatic")
        self.reset_button.setToolTip("이 전환을 분석이 정하는 자동 전환으로 되돌립니다 (Ctrl+Backspace)" if korean
                                     else "Let analysis decide this transition again (Ctrl+Backspace)")
        self.style_title.setText("스타일" if korean else "Style")
        for style, button in self.style_buttons.items():
            names = STYLE_CHOICES[style]
            button.setText(names[0] if korean else names[1])
            button.setToolTip(names[2] if korean else names[3])
        self.length_title.setText("겹침 길이" if korean else "Overlap length")
        for seconds, button in self.length_presets.items():
            name = {4.0: ("짧게", "Short"), 8.0: ("보통", "Medium"), 16.0: ("길게", "Long")}[seconds]
            button.setText(f"{name[0 if korean else 1]} {seconds:g}s")
        unit = " 초" if korean else " s"
        for spin in (self.length_spin, self.outgoing_spin, self.incoming_spin):
            spin.setSuffix(unit)
        self.length_spin.setToolTip("두 곡이 함께 들리는 길이" if korean else "How long both songs play together")
        self.cue_title.setText("큐 지점 (원본 시간)" if korean else "Cue points (source time)")
        self.outgoing_label.setText("A 믹스 시작" if korean else "A mix starts at")
        self.incoming_label.setText("B 재생 시작" if korean else "B starts from")
        self.outgoing_spin.setToolTip("나가는 곡(A)의 이 위치에서 다음 곡이 섞이기 시작합니다." if korean
                                      else "Where in the outgoing song (A) the next one starts mixing in.")
        self.incoming_spin.setToolTip("들어오는 곡(B)을 이 위치부터 재생합니다. 인트로를 건너뛸 때 씁니다." if korean
                                      else "Where the incoming song (B) starts playing; use it to skip an intro.")
        self.tempo_title.setText("템포" if korean else "Tempo")
        self.tempo_check.setText("템포 맞춤 (A를 B의 템포로 램프)" if korean else "Match tempo (ramp A onto B)")
        self.tempo_check.setToolTip("두 곡의 BPM 분석이 필요합니다." if korean else "Needs both BPMs.")
        self.handoff_label.setText("보컬 교대 지점 (겹침 대비)" if korean else "Vocal handoff (share of the overlap)")
        self.band_title.setText("대역 타이밍" if korean else "Band timing")
        for key, button in self.eq_presets.items():
            button.setText(EQ_PRESETS[key][0 if korean else 1])
        self.link_check.setText("A·B 막대 함께 옮기기" if korean else "Move A and B bars together")
        self.band_hint.setText(
            "타임라인 아래 대역 레인에서 주황(A가 사라짐)·파랑(B가 올라옴) 막대를 끌어 조정합니다. "
            "Shift를 누르면 박자에 붙지 않습니다." if korean else
            "Drag the orange (A fades out) and blue (B fades in) bars in the band lanes under the timeline. "
            "Hold Shift to ignore beats.")
        self.facts_title.setText("현재 계획" if korean else "Current plan")
        self._refresh()
