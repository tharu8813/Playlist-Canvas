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

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QSlider, QToolButton, QVBoxLayout, QWidget,
)

from app.automix.models import TrackAnalysis
from app.automix.overrides import (
    MANUAL_STYLES, MAX_DURATION_SECONDS, STYLE_AUTO, STYLE_CUT, TransitionOverride, pair_key,
)
from app.automix.structure.models import TrackStructureAnalysis
from app.models.playlist import PlaylistTrack
from app.widgets.transition_inspector import Junction, make_junction

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
        self.bars_label.setText("" if cut or override is None
                                else bars_text(override.duration, self._analyses[1], self.korean))

    def _handoff_changed(self, value: int) -> None:
        self.handoff_label.setText(("보컬 교대 " if self.korean else "Vocal handoff ") + f"{value}%")

    def _set_style(self, style: str) -> None:
        if self._override is not None and style != self._override.style:
            self._commit(style=style)

    def _commit(self, *, style: str | None = None) -> None:
        if self._syncing or self._override is None:
            return
        self._commit_timer.stop()
        style = style or self._override.style
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
        self.hint_label.setText(
            "그래프에서 겹침 구간을 끌어 위치를, 양 끝을 끌어 길이를, 들어오는 곡을 끌어 시작 지점을 바꿉니다."
            if korean else
            "In the graph, drag the overlap to move it, its edges to resize it, and the incoming song to "
            "change where it starts.")
        self._refresh_state()
