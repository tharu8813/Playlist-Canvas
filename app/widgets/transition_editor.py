"""Manual transition editing for the AutoMix editor.

Pure helpers (prefill an override from what is playing, draft a junction from
an override, snap to the beat grid, what a drag on the timeline sets) plus
TransitionPropertiesPanel, the editor's side panel. Nothing here renders
audio: an edit is committed as a TransitionOverride, the editor re-plans and
auditions the one window it changed.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QSlider, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from app.automix.models import TrackAnalysis
from app.automix.overrides import (
    ECHO_BEAT_CHOICES, EQ_BANDS, MANUAL_STYLES, MAX_DURATION_SECONDS, MAX_ECHO_FEEDBACK, MAX_RAMP_SECONDS,
    MIN_EQ_WINDOW, ROLL_BEAT_CHOICES,
    STYLE_ALIASES, STYLE_AUTO,
    STYLE_CUT, STYLE_EQ, BandWindows, TransitionOverride, Window, pair_key,
)
from app.automix.renderer import BAND_ENVELOPES
from app.automix.structure.models import TrackStructureAnalysis
from app.models.playlist import PlaylistTrack
from app.timeline.render_plan import TransitionDsp
from app.widgets.transition_inspector import Junction, _clock, make_junction

DEFAULT_MANUAL_SECONDS = 8.0
MIN_EDIT_SECONDS = 0.5

STYLE_CHOICES = {
    # style: (Korean, English, Korean tip, English tip) -- the tip says what it sounds like.
    STYLE_AUTO: ("자동 추천", "Auto pick", "두 곡을 분석해 이 구간에 어울리는 방식을 고릅니다.",
                 "Analysis picks what suits these two songs here."),
    "bass_swap": ("베이스 스왑", "Bass swap", "저음을 가운데서 한 번에 교대해 두 곡의 베이스가 겹쳐 웅웅거리지 않습니다.",
                  "The lows change hands at once mid-way, so two bass lines never rumble together."),
    "vocal_safe_eq": ("보컬 보호", "Vocal-safe", "저음에 이어 보컬 대역도 교대해 두 목소리가 동시에 들리는 시간을 줄입니다.",
                      "The lows, then the vocal band change hands, so two voices barely overlap."),
    "filter_sweep": ("필터 스윕", "Filter sweep", "나가는 곡이 저음부터 빠지며 점점 얇아지고, 다음 곡이 그 자리를 채웁니다.",
                     "The outgoing song thins out from the bottom up while the next one fills in."),
    "filter_blend": ("필터 블렌드", "Filter blend", "다음 곡이 고음부터 살짝 들리다가 점점 온전하게 들어옵니다.",
                     "The next song is heard highs-first, then arrives in full."),
    "short_fade": ("크로스페이드", "Crossfade", "한 곡이 작아지는 동안 다음 곡이 커집니다. 가장 무난한 전환입니다.",
                   "One song gets quieter as the next gets louder: the safest blend."),
    "drop_in": ("드롭인", "Drop in", "다음 곡이 처음부터 제 음량으로 시작하고, 앞 곡은 그 아래로 사라집니다.",
                "The next song starts at full level; the previous one fades away under it."),
    "echo_out": ("에코 아웃", "Echo out", "앞 곡이 멈추고 마지막 박자의 메아리만 박자에 맞춰 잦아듭니다. "
                 "템포가 달라도 어색하지 않습니다.",
                 "The song stops and only an echo of its last beat dies away in time; "
                 "works across any tempo."),
    "tape_stop": ("테이프 스톱", "Tape stop", "턴테이블 전원이 꺼지듯 앞 곡이 느려지며 음이 내려가다 멈춥니다.",
                  "The song slows and drops in pitch to a halt, like a turntable losing power."),
    "downbeat_cut": ("컷", "Cut", "겹치지 않고 큐 지점에서 바로 다음 곡으로 넘어갑니다. 큐를 박에 두면 "
                     "박에서 박으로 깔끔하게 끊깁니다.",
                     "No overlap: straight to the next song at the cue. "
                     "Cues on beats give a clean beat-to-beat cut."),
    "beat_roll": ("비트 롤", "Beat roll", "큐의 짧은 박자 구간을 반복하며 잦아듭니다. 반복 길이를 박 단위로 정합니다.",
                  "Repeats a short beat slice at the cue while fading out. Set the loop length in beats."),
    "lowpass_out": ("로우패스", "Lowpass", "앞 곡의 고음을 점차 닫으며 넘깁니다. 마지막 필터 주파수를 직접 정합니다.",
                    "Closes the outgoing song's highs while blending. Set the final filter cutoff."),
    "legacy": ("크로스페이드", "Crossfade", "한 곡이 작아지는 동안 다음 곡이 커집니다.",
               "One song gets quieter as the next gets louder."),
    "cut": ("컷", "Cut", "겹치지 않고 큐 지점에서 바로 다음 곡으로 넘어갑니다.",
            "No overlap: straight to the next song at the cue."),
    "eq": ("EQ 직접", "Custom EQ", "저음·중음·고음을 각각 언제 넘길지 대역 레인에서 직접 정합니다.",
           "You set when the lows, mids and highs each change hands, in the band lanes."),
}
SNAP_UNITS = ("off", "beat", "bar")
"""What a drag may snap to (the editor's snap menu)."""
ITEMS = ("style", "duration", "outgoing_cue", "incoming_cue", "tempo", "bands", "effect")
"""What the editor marks as changed from the automatic plan, and resets one at a time."""
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
            # A phrase exit's move is not what "auto" re-picks for a hand-set window: keep it.
            style=(transition.dsp.value if details.get("strategy") == "phrase_exit" and transition.dsp is not None
                   else STYLE_AUTO),
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


def snap_units(analysis: TrackAnalysis | None) -> tuple[str, ...]:
    """The grid units a song's analysis can be trusted for: bars and beats, beats only, or none."""
    quality = analysis.beat_alignment_quality() if analysis is not None and analysis.beats else "insufficient"
    return {"reliable": ("beat", "bar"), "bpm_only": ("beat",)}.get(quality, ())


def snap_length(seconds: float, analysis: TrackAnalysis | None, tolerance: float | None = None,
                unit: str = "beat") -> float:
    """``seconds`` rounded to whole beats of ``analysis``'s tempo (unchanged without one).

    With ``tolerance`` it is magnetic: whole bars pull from ``tolerance``
    seconds away, whole beats (``unit`` "beat") from 60% of that, and
    anything else stays free.
    """
    if analysis is None or not analysis.bpm:
        return seconds
    beat = 60.0 / analysis.bpm
    if tolerance is None:
        return max(beat, round(seconds / beat) * beat)
    bar = beat * (analysis.meter_numerator or 4)
    for step, reach in ((bar, tolerance), (beat, tolerance * 0.6))[:1 if unit == "bar" else 2]:
        if step < 3.0 * reach:
            continue
        nearest = max(step, round(seconds / step) * step)
        if abs(nearest - seconds) <= reach:
            return nearest
    return seconds


def magnet(seconds: float, analysis: TrackAnalysis | None, tolerance: float, unit: str = "beat") -> float:
    """``seconds`` pulled onto a nearby downbeat (within ``tolerance``) or beat (60% of it; not
    for ``unit`` "bar"); else unchanged.

    Unlike a hard grid this lets a drag land anywhere, while still clicking onto
    the beat when the pointer passes close to one.
    """
    if analysis is None:
        return seconds
    beat = 60.0 / analysis.bpm if analysis.bpm else 0.0
    bar = beat * (analysis.meter_numerator or 4)
    grids = ((analysis.downbeats, tolerance, bar), (analysis.beats, tolerance * 0.6, beat))
    for grid, reach, spacing in grids[:1 if unit == "bar" else 2]:
        # A grid denser than 3x its pull would catch every position: at that zoom it stays off.
        if grid and (not spacing or spacing >= 3.0 * reach):
            snapped = snap(seconds, grid, reach)
            if snapped != seconds:
                return snapped
    return seconds


def bars_text(seconds: float, analysis: TrackAnalysis | None, korean: bool) -> str:
    if analysis is None or not analysis.bpm or "beat" not in snap_units(analysis):
        return ""
    bar = (analysis.meter_numerator or 4) * 60.0 / analysis.bpm
    bars = seconds / bar
    return f"{bars:.1f}마디" if korean else f"{bars:.1f} bars"


def bar_position(seconds: float, analysis: TrackAnalysis | None, korean: bool) -> str:
    """Where ``seconds`` (source time) falls on the song's bar grid, e.g. "57마디 3박" (only on a trusted grid)."""
    if analysis is None or "bar" not in snap_units(analysis) or not analysis.downbeats:
        return ""
    bar = bisect.bisect_right(analysis.downbeats, seconds + 1e-6) - 1
    if bar < 0:
        return ""
    beat = sum(1 for time in analysis.beats if analysis.downbeats[bar] - 1e-6 <= time <= seconds + 1e-6)
    return f"{bar + 1}마디 {max(1, beat)}박" if korean else f"bar {bar + 1}, beat {max(1, beat)}"


def clock(seconds: float) -> str:
    """m:ss.cc -- the precision the editor's number fields take."""
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    minutes = int(seconds // 60)
    return f"{sign}{minutes}:{seconds - minutes * 60:05.2f}"


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

DRAG_FIELDS = {"move": ("outgoing_cue",), "outgoing": ("outgoing_cue",), "start": ("outgoing_cue", "duration"),
               "end": ("duration",), "incoming": ("incoming_cue",), "ramp": ()}
"""The saved fields each timeline drag changes (a locked one stops the drag)."""
FIELD_NAMES = {"outgoing_cue": ("A 큐", "A cue"), "incoming_cue": ("B 시작", "B start"),
               "duration": ("겹침 길이", "overlap length")}


def drag_override(
    junctions: Sequence[Junction], index: int, drawn: Junction, base: TransitionOverride, kind: str,
    delta: float, context: EditContext, *, snapping: bool, tolerance: float, korean: bool, unit: str = "beat",
) -> tuple[TransitionOverride | None, str, float | None]:
    """What dragging ``kind`` ("move"/"outgoing", "start", "end", "incoming" or "ramp") by ``delta``
    timeline seconds sets.

    ``drawn`` is the junction as it looked when the drag began and ``base``
    its override. The part under the pointer follows it, clicks onto nearby
    bars (``unit`` "bar") or bars and beats (``snapping``; ``tolerance``
    seconds), and stops at what the songs allow, saying why. A field the user
    locked does not move. Returns (override or None, the hint to show -- the
    new value and how far it moved --, the timeline second it snapped to or None).
    """
    held = [name for name in DRAG_FIELDS.get(kind, ()) if name in base.locked]
    if held:
        names = ", ".join(FIELD_NAMES[name][0 if korean else 1] for name in held)
        return None, (f"{names} 고정됨 · 속성 패널에서 고정을 풀면 움직일 수 있습니다" if korean
                      else f"{names} is locked · unlock it in the properties panel to move it"), None
    kind = "move" if kind == "outgoing" else kind
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
    advice = ""
    guide: float | None = None
    changes: dict[str, float] = {}
    if kind in ("move", "start"):
        start = drawn.start + delta
        if snapping:
            snapped = head.timeline_at(magnet(head.source_at(start), outgoing_analysis, tolerance, unit))
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
        hint = (f"{'A 큐' if korean else 'A cue'} {clock(changes['outgoing_cue'])} "
                f"(Δ {changes['outgoing_cue'] - base.outgoing_cue:+.2f}s)")
        if kind == "start":
            hint += f" · {'겹침' if korean else 'overlap'} {changes['duration']:.2f}s"
    elif kind == "end":
        new_length = length + delta
        if snapping:
            snapped = snap_length(new_length, incoming_analysis, tolerance, unit)
            guide = drawn.start + snapped if abs(snapped - new_length) > 1e-9 else None
            new_length = snapped
        # An echo rings on past the outgoing song's end: only the incoming song and 60 s bound it.
        outgoing_room = math.inf if base.style == "echo_out" else head.timeline_end - drawn.start
        longest = min(MAX_DURATION_SECONDS, outgoing_room, incoming_length - base.incoming_cue)
        if new_length > longest:
            which = (0 if longest >= MAX_DURATION_SECONDS - 1e-6
                     else 1 if longest >= outgoing_room - 1e-6 else 2)
            new_length, guide = longest, None
            limit = (("최대 60초", "나가는 곡 끝", "들어오는 곡 끝") if korean
                     else ("60 s max", "outgoing song ends", "incoming song ends"))[which]
            if which == 1:  # A has nothing left after this: a longer mix has to start earlier
                advice = (" · 더 길게는 시작 핸들을 앞으로 끄세요" if korean
                          else " · drag the start handle earlier for a longer mix")
        if new_length < MIN_EDIT_SECONDS:
            new_length, limit, guide = MIN_EDIT_SECONDS, ("최소 길이" if korean else "shortest"), None
        changes["duration"] = new_length
        bars = bars_text(new_length, incoming_analysis, korean)
        hint = (f"{'겹침' if korean else 'Overlap'} {new_length:.2f}s" + (f" · {bars}" if bars else "")
                + f" (Δ {new_length - length:+.2f}s)")
    elif kind == "ramp":  # where the outgoing song starts easing onto the new tempo
        ramp = drawn.outgoing.tempo_ramp
        if ramp is None:
            return None, "", None
        # Before its ramp the clip plays at its own rate: the new start is placed on that mapping.
        plain = replace(head, tempo_ramp=None)
        cue = ramp.source_end
        earliest = max(ramp_floor(junctions, index), drawn.outgoing.source_in, cue - MAX_RAMP_SECONDS)
        source = plain.source_at(plain.timeline_at(ramp.source_start) + delta)
        if snapping:
            reach = tolerance * plain.playback_rate
            snapped = cue if abs(cue - source) <= reach else magnet(source, outgoing_analysis, reach, unit)
            guide = plain.timeline_at(snapped) if abs(snapped - source) > 1e-9 else None
            source = snapped
        if source > cue:
            source, limit, guide = cue, ("믹스 시작" if korean else "the mix start"), None
        if source < earliest:
            source, guide = earliest, None
            limit = (("최대 2분" if korean else "2 min max") if earliest == cue - MAX_RAMP_SECONDS
                     else ("앞 전환 끝" if korean else "previous transition"))
        changes["ramp_seconds"] = round(cue - source, 6)
        span = changes["ramp_seconds"]
        bars = bars_text(span, outgoing_analysis, korean)
        hint = ((f"템포 변경 시작 {_clock(plain.timeline_at(source), precise=True)} · "
                 + (f"{bars} 동안" if bars else f"{span:.1f}초 동안") if span > 1e-6 else "템포 즉시 변경")
                if korean else
                (f"Tempo change starts {_clock(plain.timeline_at(source), precise=True)} · over "
                 + (bars or f"{span:.1f} s") if span > 1e-6 else "Tempo changes at once"))
    else:  # "incoming": slide the incoming song under its window (skip or keep its intro)
        cue = base.incoming_cue - delta
        if snapping:
            snapped = magnet(cue, incoming_analysis, tolerance, unit)
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
        hint = f"{'B 시작' if korean else 'B starts at'} {clock(cue)} (Δ {cue - base.incoming_cue:+.2f}s)"
    if "duration" in changes:
        changes["duration"] = min(MAX_DURATION_SECONDS, max(MIN_EDIT_SECONDS, changes["duration"]))
    if limit:
        hint += (f"  ▸ {limit}에서 멈춤" if korean else f"  ▸ stops at {limit}") + advice
    elif guide is not None:
        hint += (("  ◆ 마디에 붙음" if korean else "  ◆ on a bar") if unit == "bar"
                 else ("  ◆ 박자에 붙음" if korean else "  ◆ on the beat"))
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


# -- what plays vs. what was asked for ---------------------------------------------------

def style_key(junction: Junction) -> str:
    """The style ``junction`` renders with, as a STYLE_CHOICES key."""
    from app.automix.renderer import transition_dsp_style

    transition = junction.transition
    if transition is None:
        return STYLE_CUT
    if transition.band_windows is not None:
        return STYLE_EQ
    style = transition_dsp_style(transition)
    return style.value if style is not None and style.value in STYLE_CHOICES else "legacy"


def planned_values(junction: Junction) -> dict[str, object]:
    """What ``junction`` actually plays, in the editor's terms (after every limit the planner applied)."""
    from app.automix.renderer import band_windows_of

    transition = junction.transition
    details = dict(transition.details) if transition is not None else {}
    cue = details.get("outgoing_cue")
    return {
        "outgoing_cue": float(cue) if isinstance(cue, (int, float)) else junction.outgoing.source_out,
        "incoming_cue": junction.incoming.source_in,
        "duration": junction.end - junction.start,
        "style": shown_style(style_key(junction)),
        "tempo": (round(float(details.get("outgoing_rate") or 1.0), 4),
                  round(float(details.get("tempo_ramp_seconds") or 0.0), 2), details.get("key_shift_semitones")),
        "bands": band_windows_of(transition) if transition is not None else None,
    }


_EFFECT_FIELDS = {"echo_out": ("echo_beats", "echo_feedback", "echo_low_cut"), "tape_stop": ("tape_entry",),
                  "vocal_safe_eq": ("vocal_handoff",), "beat_roll": ("roll_beats",),
                  "lowpass_out": ("filter_cutoff_hz",)}


def changed_items(current: Junction, automatic: Junction | None, override: TransitionOverride | None) -> set[str]:
    """ITEMS in which ``current`` (planned with ``override``) differs from the automatic plan of the same junction."""
    if override is None or automatic is None:
        return set()
    now, auto = planned_values(current), planned_values(automatic)
    items = {name for name in ("outgoing_cue", "incoming_cue", "duration") if abs(now[name] - auto[name]) > 0.01}
    items.update(name for name in ("style", "tempo") if now[name] != auto[name])
    if now["style"] == STYLE_EQ and now["bands"] != auto["bands"]:
        items.add("bands")
    defaults = TransitionOverride(0.0)
    if any(getattr(override, name) != getattr(defaults, name) for name in _EFFECT_FIELDS.get(override.style, ())):
        items.add("effect")
    return items


def reset_item(override: TransitionOverride, automatic: TransitionOverride, item: str) -> TransitionOverride:
    """``override`` with one ITEM back on its automatic value (``automatic``: the automatic plan as an override)."""
    defaults = TransitionOverride(0.0)
    unlock = tuple(name for name in override.locked if name != item)
    changes: dict[str, object] = {
        "style": {"style": automatic.style, "eq_bands": None, "vocal_handoff": None},
        "duration": {"duration": automatic.duration, "locked": unlock},
        "outgoing_cue": {"outgoing_cue": automatic.outgoing_cue, "locked": unlock},
        "incoming_cue": {"incoming_cue": automatic.incoming_cue, "locked": unlock},
        "tempo": {"tempo_match": automatic.tempo_match, "ramp_seconds": None, "key_shift": None},
        "bands": {"eq_bands": None, "style": automatic.style if override.style == STYLE_EQ else override.style},
        "effect": {name: getattr(defaults, name) for names in _EFFECT_FIELDS.values() for name in names},
    }[item]
    return replace(override, **changes)


def applied_notes(override: TransitionOverride, junction: Junction, durations: tuple[float, float],
                  korean: bool) -> list[str]:
    """Where the plan could not play ``override`` as set: what plays instead, and why."""
    now = planned_values(junction)
    cut = override.style in _CUT_STYLES
    notes = []
    if junction.transition is None and not cut:
        return ["겹칠 공간이 없어 컷으로 재생됩니다 (두 곡의 남은 길이 부족)" if korean
                else "No room to overlap, so it plays as a cut (not enough of either song left)"]
    requested, applied = override.outgoing_cue, now["outgoing_cue"]
    if abs(requested - applied) > 0.01:
        why = (("앞 전환이 끝나기 전에는 시작할 수 없음" if korean else "it cannot start before the previous mix ends")
               if applied > requested else ("나가는 곡이 끝남" if korean else "the outgoing song ends"))
        notes.append(f"A 큐 {clock(requested)} → {clock(applied)}: {why}" if korean
                     else f"A cue {clock(requested)} → {clock(applied)}: {why}")
    requested, applied = override.incoming_cue, now["incoming_cue"]
    if abs(requested - applied) > 0.01:
        notes.append(f"B 시작 {clock(requested)} → {clock(applied)}: 들어오는 곡 끝에 너무 가까움" if korean
                     else f"B start {clock(requested)} → {clock(applied)}: too close to the incoming song's end")
    requested, applied = override.duration, now["duration"]
    if not cut and requested - applied > 0.01:
        rate = now["tempo"][0] or 1.0
        outgoing_room = (durations[0] - now["outgoing_cue"]) / rate
        why = (("나가는 곡이 끝남" if korean else "the outgoing song ends") if applied >= outgoing_room - 0.02
               and override.style != "echo_out" else ("들어오는 곡이 끝남" if korean else "the incoming song ends")
               if applied >= durations[1] - now["incoming_cue"] - 0.02 else "")
        notes.append((f"겹침 {requested:.2f}초 → {applied:.2f}초" if korean
                      else f"Overlap {requested:.2f} s → {applied:.2f} s") + (f": {why}" if why else ""))
    return notes


def recommendation_note(junction: Junction, override: TransitionOverride, korean: bool) -> str:
    """What a "keep these values" recommendation did: held them, or could not (and what it would pick)."""
    details = dict(junction.transition.details) if junction.transition is not None else {}
    names = ", ".join(FIELD_NAMES[name][0 if korean else 1] for name in override.locked)
    state = details.get("recommendation")
    if state == "locked":
        return (f"자동 추천을 따르며 고정한 {names}은(는) 그대로 둡니다." if korean
                else f"Following the automatic recommendation, keeping your {names}.")
    if state == "no_analysis":
        return ("분석이 끝나야 추천할 수 있어 저장된 값으로 재생합니다." if korean
                else "Analysis is needed to recommend; the saved values play meanwhile.")
    nearest = ""
    if isinstance(details.get("nearest_outgoing_cue"), (int, float)):
        nearest = (f" 가장 가까운 추천: A 큐 {clock(details['nearest_outgoing_cue'])} · "
                   f"B 시작 {clock(details['nearest_incoming_cue'])} · 길이 {details['nearest_duration']:.2f}초"
                   if korean else
                   f" Nearest recommendation: A cue {clock(details['nearest_outgoing_cue'])}, "
                   f"B start {clock(details['nearest_incoming_cue'])}, {details['nearest_duration']:.2f} s long.")
    return ((f"고정한 {names}을(를) 지키는 자동 추천이 없어 값을 바꾸지 않고 저장된 값으로 재생합니다.{nearest}"
             if korean else
             f"No automatic recommendation keeps your {names}, so nothing was changed; the saved values play.{nearest}")
            if state == "no_fit" else "")


# -- side panel -------------------------------------------------------------------------

STYLE_GROUPS = (
    # (Korean heading, English heading, styles) -- what the editor offers, by kind of move.
    ("페이드", "Fades", ("short_fade", "drop_in")),
    ("EQ 믹스 · 박자가 맞는 곡", "EQ mixes · beat-matched songs",
     ("bass_swap", "vocal_safe_eq", "filter_blend", "filter_sweep", STYLE_EQ)),
    ("효과 · 템포가 달라도 됨", "Effects · any tempo",
     ("echo_out", "tape_stop", "downbeat_cut", "beat_roll", "lowpass_out")),
)
_BASIC_STYLES = ("short_fade", "vocal_safe_eq", "echo_out", "downbeat_cut", "beat_roll", "lowpass_out")
"""Simple mode's choices besides automatic: one of each kind."""
LENGTH_PRESETS = (4.0, 8.0, 16.0)
_CUT_STYLES = (STYLE_CUT, "downbeat_cut")
"""Styles without an overlap to size."""
KEY_SHIFT_CHOICES = (None, 0, -2, -1, 1, 2)


def shown_style(style: str) -> str:
    """The editor button that stands for saved ``style`` (see STYLE_ALIASES)."""
    return STYLE_ALIASES.get(style, style)


def effect_length(style: str, outgoing: TrackAnalysis | None) -> float | None:
    """The window an effect's move naturally takes, as the automatic phrase exit sizes it (None: not an effect)."""
    from app.automix.exits import ECHO_BARS, ECHO_MAX_SECONDS, TAPE_STOP_SECONDS

    bar = (outgoing.meter_numerator or 4) * 60.0 / outgoing.bpm if outgoing is not None and outgoing.bpm else 2.0
    if style == "echo_out":
        return round(min(ECHO_BARS * bar, ECHO_MAX_SECONDS), 3)
    if style == "tape_stop":
        return round(min(max(bar, TAPE_STOP_SECONDS[0]), TAPE_STOP_SECONDS[1]), 3)
    if style in ("beat_roll", "lowpass_out"):
        return round(min(8.0, 2 * bar), 3)
    return None


SELECTIONS = ("transition", "outgoing", "incoming", "tempo", "band:high", "band:mid", "band:low")
"""What can be selected on the timeline; the side panel shows that target's settings."""
_BAND_NAMES = {"low": ("저음", "Lows"), "mid": ("중음", "Mids"), "high": ("고음", "Highs")}


def selection_name(selection: str, korean: bool) -> str:
    if selection.startswith("band:"):
        name = _BAND_NAMES[selection[5:]]
        return f"{name[0]} 대역" if korean else f"{name[1]} band"
    names = {"transition": ("전환 구간", "Transition"), "outgoing": ("A 큐 · 나가는 곡", "A cue · outgoing song"),
             "incoming": ("B 시작 · 들어오는 곡", "B start · incoming song"), "tempo": ("템포 변경", "Tempo change")}
    return names.get(selection, names["transition"])[0 if korean else 1]


class _FieldHeader(QWidget):
    """A setting's title with its state: "changed" mark, keep (lock) toggle, reset-to-automatic."""

    def __init__(self, item: str, lockable: bool, parent: QWidget | None = None) -> None:
        from app.ui.studio_icons import lock_icon

        super().__init__(parent)
        self.item = item
        self.title = QLabel()
        self.title.setObjectName("panelTitle")
        self.title.setWordWrap(True)  # a narrow panel wraps the title instead of growing past its width
        self.mark = QLabel()
        self.mark.setObjectName("changedMark")
        self.lock = QToolButton()
        self.lock.setObjectName("lockButton")
        self.lock.setCheckable(True)
        self.lock.setIcon(lock_icon())
        self.lock.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.lock.setVisible(lockable)
        self.reset = QToolButton()
        self.reset.setObjectName("itemResetButton")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self.title)
        row.addWidget(self.mark)
        row.addStretch(1)
        row.addWidget(self.reset)
        row.addWidget(self.lock)

    def show_state(self, changed: bool, locked: bool, korean: bool) -> None:
        self.mark.setVisible(changed)
        self.reset.setVisible(changed)
        self.lock.blockSignals(True)
        self.lock.setChecked(locked)
        self.lock.blockSignals(False)
        self.lock.setText(("고정됨" if locked else "고정") if korean else ("Kept" if locked else "Keep"))

    def retranslate(self, korean: bool) -> None:
        self.mark.setText("●")
        self.mark.setToolTip("자동 결과와 다른 값" if korean else "Differs from the automatic result")
        self.mark.setAccessibleName(self.mark.toolTip())
        self.reset.setText("자동값으로" if korean else "Reset")
        self.reset.setToolTip("이 항목만 자동 결과로 되돌립니다" if korean else "Put just this back on the automatic value")
        self.lock.setToolTip(
            "이 값 유지: 자동 추천을 다시 받아도 이 값은 바뀌지 않고, 타임라인에서 실수로 끌리지 않습니다" if korean
            else "Keep this value: a new automatic recommendation keeps it, and timeline drags leave it alone")
        self.lock.setAccessibleName(self.title.text() + (" 고정" if korean else " keep"))


class TransitionPropertiesPanel(QWidget):
    """The AutoMix editor's side panel: the selected target's settings.

    One page per timeline selection (SELECTIONS): the transition (style,
    length, where it sits), each song's cue, the tempo change, one band's
    timing. Shows one junction (``set_state``; never emits while doing so)
    and reports what the user changed as ``changed({field: value})`` -- the
    editor turns that into an override and an undo step. Number fields
    debounce so typing is one edit; ``flush()`` commits a pending one at once,
    typed text not yet confirmed with Enter included (before the selection
    moves or the window closes, so it lands on the junction it was typed
    for). Simple mode shows the transition page only and hides the precise
    controls without touching their values.
    """

    changed = Signal(dict)
    reset_requested = Signal()
    """Automatic again -- or, with kept values, a new recommendation that keeps them."""
    item_reset = Signal(str)
    lock_toggled = Signal(str, bool)
    link_toggled = Signal(bool)

    DEBOUNCE_MS = 450

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("automixProperties")
        self.korean = True
        self.advanced = False
        self.selection = "transition"
        self._override: TransitionOverride | None = None
        self._manual = False
        self._band_style = False
        self._auto_style = ""
        self._analyses: tuple[TrackAnalysis | None, TrackAnalysis | None] = (None, None)
        self._bands: BandWindows | None = None
        self._changed: set[str] = set()
        self._locked: tuple[str, ...] = ()
        self._recommend = False
        self._syncing = False
        self._pending: dict[str, object] = {}
        self._spins: list[QDoubleSpinBox] = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.DEBOUNCE_MS)
        self._timer.timeout.connect(self.flush)
        self.headers: dict[str, _FieldHeader] = {}

        def header(item: str, lockable: bool = False) -> _FieldHeader:
            widget = _FieldHeader(item, lockable)
            widget.reset.clicked.connect(lambda _checked=False, item=item: self._reset_item(item))
            widget.lock.toggled.connect(lambda locked, item=item: self._lock(item, locked))
            self.headers.setdefault(item, widget)
            self._all_headers.append(widget)
            return widget

        self._all_headers: list[_FieldHeader] = []

        # -- header: what is selected, whose, and its state --
        self.selection_title = QLabel()
        self.selection_title.setObjectName("selectionTitle")
        self.pair_label = QLabel()
        self.pair_label.setObjectName("mutedLabel")
        self.songs_label = QLabel()
        self.songs_label.setWordWrap(True)
        self.songs_label.setTextFormat(Qt.TextFormat.RichText)
        self.mode_chip = QLabel()
        self.mode_chip.setObjectName("automixModeChip")
        self.mode_chip.setWordWrap(True)
        self.reset_button = QPushButton()
        self.reset_button.clicked.connect(self._request_reset)
        self.summary_label = QLabel()
        self.summary_label.setObjectName("automixSummary")
        self.summary_label.setWordWrap(True)
        self.note_label = QLabel()
        self.note_label.setObjectName("automixNote")
        self.note_label.setWordWrap(True)

        # -- transition page: style, its effect details, length, position --
        self.style_header = header("style")
        self.style_title = self.style_header.title
        self.style_group = QButtonGroup(self)
        self.style_buttons: dict[str, QToolButton] = {}
        self.style_grid = QGridLayout()
        self.style_grid.setHorizontalSpacing(6)
        self.style_grid.setVerticalSpacing(6)
        for style in (STYLE_AUTO, *(style for *_names, styles in STYLE_GROUPS for style in styles)):
            button = QToolButton()
            button.setObjectName("transitionStyleCard")
            button.setCheckable(True)
            button.setMinimumHeight(30)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda _checked=False, style=style: self._choose_style(style))
            self.style_group.addButton(button)
            self.style_buttons[style] = button
        self.group_labels = []
        for _group in STYLE_GROUPS:
            label = QLabel()
            label.setObjectName("styleGroupLabel")
            self.group_labels.append(label)
        self.style_description = QLabel()
        self.style_description.setObjectName("mutedLabel")
        self.style_description.setWordWrap(True)

        self.length_header = header("duration", lockable=True)
        self.length_title = self.length_header.title
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
        self.bars_spin = QDoubleSpinBox()
        self.bars_spin.setDecimals(2)
        self.bars_spin.setSingleStep(1.0)
        self.bars_spin.setKeyboardTracking(False)
        self.bars_spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.bars_spin.valueChanged.connect(self._bars_typed)
        self._spins.append(self.bars_spin)
        self.bars_label = QLabel()
        self.bars_label.setObjectName("mutedLabel")
        length_value = QHBoxLayout()
        length_value.addWidget(self.length_spin, 1)
        length_value.addWidget(self.bars_spin, 1)
        length_value.addWidget(self.bars_label)

        self.position_header = header("outgoing_cue", lockable=True)
        self.position_spin = self._spin(36_000.0, "outgoing_cue")
        self.position_clock = QLabel()
        self.position_clock.setObjectName("mutedLabel")
        position_row = QVBoxLayout()
        position_row.addWidget(self.position_spin)
        position_row.addWidget(self.position_clock)

        # -- one song's cue --
        self.outgoing_header = header("outgoing_cue", lockable=True)
        self.outgoing_label = QLabel()
        self.outgoing_label.setObjectName("mutedLabel")
        self.outgoing_label.setWordWrap(True)
        self.outgoing_spin = self._spin(36_000.0, "outgoing_cue")
        self.outgoing_clock = QLabel()
        self.outgoing_clock.setObjectName("mutedLabel")
        self.incoming_header = header("incoming_cue", lockable=True)
        self.incoming_label = QLabel()
        self.incoming_label.setObjectName("mutedLabel")
        self.incoming_label.setWordWrap(True)
        self.incoming_spin = self._spin(36_000.0, "incoming_cue")
        self.incoming_clock = QLabel()
        self.incoming_clock.setObjectName("mutedLabel")
        self.cue_facts = {side: QLabel() for side in ("outgoing", "incoming")}
        for label in self.cue_facts.values():
            label.setObjectName("mutedLabel")
            label.setWordWrap(True)

        # -- tempo --
        self.tempo_header = header("tempo")
        self.tempo_title = self.tempo_header.title
        self.tempo_check = QCheckBox()
        self.tempo_check.toggled.connect(lambda checked: self._emit({"tempo_match": checked}))
        self.ramp_label = QLabel()
        self.ramp_auto_check = QCheckBox()
        self.ramp_auto_check.toggled.connect(self._ramp_auto_toggled)
        self.ramp_spin = self._spin(MAX_RAMP_SECONDS, "ramp_seconds")
        self.ramp_bars = QLabel()
        self.ramp_bars.setObjectName("mutedLabel")
        ramp_row = QHBoxLayout()
        ramp_row.addWidget(self.ramp_auto_check)
        ramp_row.addWidget(self.ramp_spin, 1)
        ramp_row.addWidget(self.ramp_bars)
        self.key_label = QLabel()
        self.key_combo = QComboBox()
        self.key_combo.currentIndexChanged.connect(self._key_chosen)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_label)
        key_row.addWidget(self.key_combo, 1)
        self.tempo_info = QLabel()
        self.tempo_info.setObjectName("mutedLabel")
        self.tempo_info.setWordWrap(True)
        self.ramp_shape = QLabel()
        self.ramp_shape.setObjectName("mutedLabel")
        self.ramp_shape.setWordWrap(True)

        # -- one band's timing (band styles) --
        self.band_header = header("bands")
        self.band_title = self.band_header.title
        self.band_spins: dict[tuple[str, int], QDoubleSpinBox] = {}
        band_grid = QGridLayout()
        band_grid.setHorizontalSpacing(8)
        band_grid.setVerticalSpacing(6)
        self.band_row_labels = {side: QLabel() for side in ("out", "in")}
        for row, side in enumerate(("out", "in")):
            band_grid.addWidget(self.band_row_labels[side], row, 0)
            for column in (0, 1):
                spin = QDoubleSpinBox()
                spin.setRange(0.0, 100.0)
                spin.setDecimals(1)
                spin.setSingleStep(1.0)
                spin.setSuffix(" %")
                spin.setKeyboardTracking(False)
                spin.setAlignment(Qt.AlignmentFlag.AlignRight)
                spin.setMinimumWidth(64)
                spin.valueChanged.connect(lambda _value, side=side: self._band_typed(side))
                self._spins.append(spin)
                self.band_spins[(side, column)] = spin
                band_grid.addWidget(spin, row, column + 1)
        band_grid.setColumnStretch(1, 1)
        band_grid.setColumnStretch(2, 1)
        self.band_columns = QLabel()
        self.band_columns.setObjectName("mutedLabel")
        self.band_hole_label = QLabel()
        self.band_hole_label.setObjectName("automixNote")
        self.band_hole_label.setWordWrap(True)
        self.band_unavailable = QLabel()
        self.band_unavailable.setObjectName("mutedLabel")
        self.band_unavailable.setWordWrap(True)
        self.eq_presets: dict[str, QToolButton] = {}
        presets = QGridLayout()
        presets.setSpacing(6)
        for position, key in enumerate(EQ_PRESETS):
            button = QToolButton()
            button.setObjectName("eqPresetButton")
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda _checked=False, key=key: self._emit({"eq_bands": EQ_PRESETS[key][2]}))
            self.eq_presets[key] = button
            presets.addWidget(button, position // 2, position % 2)
        self.presets_label = QLabel()
        self.presets_label.setObjectName("styleGroupLabel")
        self.link_check = QCheckBox()
        self.link_check.setChecked(True)
        self.link_check.toggled.connect(self.link_toggled)
        self.band_hint = QLabel()
        self.band_hint.setObjectName("mutedLabel")
        self.band_hint.setWordWrap(True)

        # -- style details: only the chosen style's own settings are shown --
        self.detail_header = header("effect")
        self.detail_title = self.detail_header.title
        self.echo_beats_label = QLabel()
        self.echo_beat_buttons: dict[float, QToolButton] = {}
        echo_beats_row = QHBoxLayout()
        echo_beats_row.setSpacing(6)
        for beats in ECHO_BEAT_CHOICES:
            button = QToolButton()
            button.setObjectName("eqPresetButton")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, beats=beats: self._emit({"echo_beats": beats}))
            self.echo_beat_buttons[beats] = button
            echo_beats_row.addWidget(button)
        echo_beats_row.addStretch(1)
        self.echo_tail_label = QLabel()
        self.echo_feedback_slider = QSlider(Qt.Orientation.Horizontal)
        self.echo_feedback_slider.setRange(20, round(MAX_ECHO_FEEDBACK * 100))
        self.echo_feedback_slider.valueChanged.connect(self._echo_feedback_moved)
        self.echo_feedback_slider.sliderReleased.connect(self.flush)
        self.echo_feedback_value = QLabel()
        self.echo_feedback_value.setMinimumWidth(64)
        self.echo_feedback_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        echo_tail_row = QHBoxLayout()
        echo_tail_row.addWidget(self.echo_feedback_slider, 1)
        echo_tail_row.addWidget(self.echo_feedback_value)
        self.echo_low_cut_check = QCheckBox()
        self.echo_low_cut_check.toggled.connect(lambda checked: self._emit({"echo_low_cut": checked}))

        self.tape_entry_label = QLabel()
        self.tape_entry_slider = QSlider(Qt.Orientation.Horizontal)
        self.tape_entry_slider.setRange(20, 95)
        self.tape_entry_slider.valueChanged.connect(self._tape_entry_moved)
        self.tape_entry_slider.sliderReleased.connect(self.flush)
        self.tape_entry_value = QLabel()
        self.tape_entry_value.setMinimumWidth(40)
        self.tape_entry_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        tape_row = QHBoxLayout()
        tape_row.addWidget(self.tape_entry_slider, 1)
        tape_row.addWidget(self.tape_entry_value)

        self.roll_label = QLabel()
        self.roll_combo = QComboBox()
        for beats in ROLL_BEAT_CHOICES:
            self.roll_combo.addItem(f"{beats:g}", beats)
        self.roll_combo.currentIndexChanged.connect(
            lambda index: self._emit({"roll_beats": self.roll_combo.itemData(index)}))
        self.cutoff_label = QLabel()
        self.cutoff_spin = QDoubleSpinBox()
        self.cutoff_spin.setRange(200.0, 8000.0)
        self.cutoff_spin.setDecimals(0)
        self.cutoff_spin.setSingleStep(100.0)
        self.cutoff_spin.setSuffix(" Hz")
        self.cutoff_spin.valueChanged.connect(lambda value: self._queue("filter_cutoff_hz", value))
        self.cutoff_spin.editingFinished.connect(self.flush)

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

        def page(*items) -> QWidget:
            box = group(*items)
            box.layout().setSpacing(18)
            box.layout().addStretch(1)
            return box

        mode_row = QHBoxLayout()
        mode_row.addWidget(self.reset_button)
        mode_row.addStretch(1)
        self.echo_box = group(self.echo_beats_label, echo_beats_row, self.echo_tail_label, echo_tail_row,
                              self.echo_low_cut_check)
        self.tape_box = group(self.tape_entry_label, tape_row)
        self.handoff_box = group(self.handoff_label, handoff_row)
        self.roll_box = group(self.roll_label, self.roll_combo)
        self.cutoff_box = group(self.cutoff_label, self.cutoff_spin)
        # One "details" section: whichever of these belongs to the chosen style.
        self.detail_box = group(self.detail_header, self.echo_box, self.tape_box, self.handoff_box,
                                self.roll_box, self.cutoff_box)
        self.length_box = group(self.length_header, length_row, length_value)
        self.position_box = group(self.position_header, position_row)
        self.cue_box = self.position_box
        """Where the window sits (advanced mode); each song's own cue page has the same field."""
        self.tempo_box = group(self.tempo_header, self.tempo_check, self.ramp_label, ramp_row, key_row,
                               self.tempo_info, self.ramp_shape)
        self.band_box = group(self.band_header, self.band_unavailable, self.band_columns, band_grid,
                              self.band_hole_label, self.link_check, self.presets_label, presets, self.band_hint)
        self.pages = QStackedWidget()
        self.page_widgets = {
            "transition": page(group(self.style_header, self.style_grid, self.style_description),
                               self.detail_box, self.length_box, self.position_box),
            "outgoing": page(group(self.outgoing_header, self.outgoing_label, self.outgoing_spin,
                                   self.outgoing_clock), self.cue_facts["outgoing"]),
            "incoming": page(group(self.incoming_header, self.incoming_label, self.incoming_spin,
                                   self.incoming_clock), self.cue_facts["incoming"]),
            "tempo": page(self.tempo_box),
            "band": page(self.band_box),
        }
        for widget in self.page_widgets.values():
            self.pages.addWidget(widget)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 20)
        layout.setSpacing(16)
        heading = group(self.selection_title, self.pair_label, self.songs_label, self.mode_chip, mode_row,
                        self.summary_label, self.note_label)
        layout.addWidget(heading)
        layout.addWidget(self.pages)
        self.facts_box = group(self.facts_title, self.facts)
        layout.addWidget(self.facts_box)
        layout.addStretch(1)
        for label in self.findChildren(QLabel):
            label.setWordWrap(True)  # the panel keeps its width in any language; text wraps instead
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
        spin.setMinimumWidth(90)
        spin.valueChanged.connect(lambda value: self._queue(field_name, value))
        self._spins.append(spin)
        return spin

    # -- state -------------------------------------------------------------------

    @property
    def override(self) -> TransitionOverride | None:
        return self._override

    @property
    def pending(self) -> bool:
        return bool(self._pending) or any(spin.lineEdit().isModified() for spin in self._spins)

    def set_state(
        self, override: TransitionOverride | None, *, manual: bool, pair_text: str, songs_html: str,
        analyses: tuple[TrackAnalysis | None, TrackAnalysis | None] = (None, None),
        durations: tuple[float, float] | None = None, band_style: bool = False, auto_style: str = "",
        tempo_text: str = "", facts: Sequence[tuple[str, str]] = (), changed: Sequence[str] = (),
        notes: Sequence[str] = (), summary: str = "", bands: BandWindows | None = None,
        recommend: bool = False, cue_facts: tuple[str, str] = ("", ""),
    ) -> None:
        """Show one junction: ``override`` is what it plays (manual or prefilled from the automatic plan).

        ``changed``: ITEMS that differ from the automatic plan; ``notes``: where
        the plan could not play what was asked; ``summary``: advanced settings
        in force (shown in simple mode); ``bands``: the band windows playing.
        """
        self._timer.stop()
        self._pending.clear()
        self._override, self._manual = override, manual
        self._band_style, self._auto_style = band_style, auto_style
        self._analyses = analyses
        self._bands = bands
        self._changed = set(changed)
        self._locked = override.locked if override is not None else ()
        self._recommend = recommend
        self.pair_label.setText(pair_text)
        self.songs_label.setText(songs_html)
        self.tempo_info.setText(tempo_text)
        self.note_label.setText("\n".join(notes))
        self.note_label.setVisible(bool(notes))
        self.summary_label.setText(summary)
        for side, text in zip(("outgoing", "incoming"), cue_facts):
            self.cue_facts[side].setText(text)
        self._set_facts(facts)
        self.setEnabled(override is not None)
        if override is None:
            self._refresh()
            return
        self._syncing = True
        try:
            if durations is not None:
                for spin in (self.outgoing_spin, self.position_spin):
                    spin.setMaximum(max(0.0, durations[0]))
                self.incoming_spin.setMaximum(max(0.0, durations[1]))
            values = {self.outgoing_spin: override.outgoing_cue, self.position_spin: override.outgoing_cue,
                      self.incoming_spin: override.incoming_cue,
                      self.length_spin: max(MIN_EDIT_SECONDS, override.duration)}
            bar = self._bar_seconds()
            if bar:
                self.bars_spin.setRange(MIN_EDIT_SECONDS / bar, MAX_DURATION_SECONDS / bar)
                values[self.bars_spin] = override.duration / bar
            values[self.ramp_spin] = override.ramp_seconds or 0.0
            if bands is not None:
                band = EQ_BANDS.index(self._band())
                for (side, column), spin in self.band_spins.items():
                    values[spin] = bands[band][0 if side == "out" else 1][column] * 100.0
            for spin, value in values.items():
                # A value being typed stays as typed (analysis may re-plan meanwhile).
                if not (spin.hasFocus() and spin.lineEdit().isModified()):
                    spin.setValue(value)
            self.ramp_auto_check.setChecked(override.ramp_seconds is None)
            self.tempo_check.setChecked(override.tempo_match)
            self.handoff_slider.setValue(round((override.vocal_handoff or 0.525) * 100))
            self.echo_feedback_slider.setValue(round(override.echo_feedback * 100))
            self.echo_low_cut_check.setChecked(override.echo_low_cut)
            for beats, button in self.echo_beat_buttons.items():
                button.setChecked(abs(override.echo_beats - beats) < 1e-9)
            self.tape_entry_slider.setValue(round(override.tape_entry * 100))
            self.roll_combo.setCurrentIndex(ROLL_BEAT_CHOICES.index(override.roll_beats))
            if not (self.cutoff_spin.hasFocus() and self.cutoff_spin.lineEdit().isModified()):
                self.cutoff_spin.setValue(override.filter_cutoff_hz)
            self.key_combo.setCurrentIndex(KEY_SHIFT_CHOICES.index(override.key_shift)
                                           if override.key_shift in KEY_SHIFT_CHOICES else 0)
            self.style_group.setExclusive(False)
            for style, button in self.style_buttons.items():
                button.setChecked(manual and style == shown_style(override.style))
            self.style_group.setExclusive(True)
        finally:
            self._syncing = False
        self._refresh()

    def set_advanced(self, advanced: bool) -> None:
        self.advanced = advanced
        self._layout_styles()
        self._refresh()

    def set_selection(self, selection: str) -> None:
        """Show ``selection``'s page (simple mode always shows the transition's)."""
        self.flush()
        self.selection = selection if selection in SELECTIONS else "transition"
        self._refresh()

    def flush(self) -> None:
        """Commit a pending (debounced) edit now, including text typed but not yet confirmed."""
        for spin in self._spins:
            if spin.lineEdit().isModified():
                spin.lineEdit().setModified(False)
                spin.interpretText()
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

    def _request_reset(self) -> None:
        self.flush()
        self.reset_requested.emit()

    def _reset_item(self, item: str) -> None:
        self.flush()
        self.item_reset.emit(item)

    def _lock(self, item: str, locked: bool) -> None:
        if self._syncing or self._override is None:
            return
        self.flush()
        self.lock_toggled.emit(item, locked)

    def _band(self) -> str:
        return self.selection[5:] if self.selection.startswith("band:") else "low"

    def _bar_seconds(self) -> float | None:
        incoming = self._analyses[1]
        if incoming is None or "bar" not in snap_units(incoming):
            return None
        return (incoming.meter_numerator or 4) * 60.0 / incoming.bpm

    def _bars_typed(self, bars: float) -> None:
        bar = self._bar_seconds()
        if bar:
            self._queue("duration", round(bars * bar, 6))

    def _band_typed(self, side: str) -> None:
        if self._syncing or self._override is None or self._bands is None:
            return
        window = tuple(self.band_spins[(side, column)].value() / 100.0 for column in (0, 1))
        if window[1] - window[0] < MIN_EQ_WINDOW:  # start past end: keep the shortest fade
            window = (min(window[0], 1.0 - MIN_EQ_WINDOW), min(window[0], 1.0 - MIN_EQ_WINDOW) + MIN_EQ_WINDOW)
        band = EQ_BANDS.index(self._band())
        pending = self._pending.get("eq_bands", self._bands)
        out_window, in_window = pending[band]
        self._queue("eq_bands", with_band(pending, self._band(), *((window, in_window) if side == "out"
                                                                     else (out_window, window))))

    def _ramp_auto_toggled(self, automatic: bool) -> None:
        self.ramp_spin.setEnabled(not automatic)
        if not self._syncing:
            self._emit({"ramp_seconds": None if automatic else round(self.ramp_spin.value(), 6)})

    def _choose_style(self, style: str) -> None:
        """Pick ``style``; an effect whose move has its own length gets it (echo: two
        bars, stop: one) instead of a long blend's window -- unless the length is kept."""
        changes: dict[str, object] = {"style": style}
        natural = effect_length(style, self._analyses[0])
        if (natural is not None and self._override is not None and self._override.duration > natural * 1.5
                and "duration" not in self._locked):
            changes["duration"] = natural
        self._emit(changes)

    def _handoff_moved(self, value: int) -> None:
        self.handoff_value.setText(f"{value}%")
        self._queue("vocal_handoff", value / 100.0)

    def _echo_feedback_moved(self, value: int) -> None:
        self._show_echo_feedback(value)
        self._queue("echo_feedback", value / 100.0)

    def _show_echo_feedback(self, value: int) -> None:
        # How much quieter each repeat is: the number a DJ reads off an echo unit.
        self.echo_feedback_value.setText(f"{20 * math.log10(value / 100.0):.1f} dB")

    def _tape_entry_moved(self, value: int) -> None:
        self.tape_entry_value.setText(f"{value}%")
        self._queue("tape_entry", value / 100.0)

    def _key_chosen(self, index: int) -> None:
        if 0 <= index < len(KEY_SHIFT_CHOICES):
            self._emit({"key_shift": KEY_SHIFT_CHOICES[index]})

    def _layout_styles(self) -> None:
        """Automatic on its own row, then the styles: grouped by kind in advanced
        mode, one of each kind in simple mode."""
        for widget in (*self.style_buttons.values(), *self.group_labels):
            self.style_grid.removeWidget(widget)
            widget.hide()
        columns = 3 if self.advanced else 2
        self.style_grid.addWidget(self.style_buttons[STYLE_AUTO], 0, 0, 1, columns)
        self.style_buttons[STYLE_AUTO].show()
        row = 1
        groups = STYLE_GROUPS if self.advanced else ((None, None, _BASIC_STYLES),)
        chosen = shown_style(self._override.style) if self._override is not None and self._manual else None
        for index, (_korean, _english, styles) in enumerate(groups):
            if self.advanced:
                label = self.group_labels[index]
                self.style_grid.addWidget(label, row, 0, 1, columns)
                label.show()
                row += 1
            elif chosen not in (*styles, STYLE_AUTO, None):
                styles = (*styles, chosen)  # a style picked in advanced mode stays visible (and lit)
            for position, style in enumerate(styles):
                button = self.style_buttons[style]
                self.style_grid.addWidget(button, row + position // columns, position % columns)
                button.show()
            row += (len(styles) + columns - 1) // columns
        for column in range(3):
            self.style_grid.setColumnStretch(column, 1 if column < columns else 0)

    def _refresh(self) -> None:
        override, korean = self._override, self.korean
        selection = self.selection if self.advanced else "transition"
        self.pages.setCurrentWidget(self.page_widgets["band" if selection.startswith("band:") else selection])
        self.selection_title.setText(selection_name(selection, korean))
        style = shown_style(override.style) if override is not None and self._manual else None
        if not self.advanced:
            self._layout_styles()  # the style chosen in advanced mode stays in view
        cut = style in _CUT_STYLES
        for seconds, button in self.length_presets.items():
            button.setChecked(not cut and override is not None and abs(override.duration - seconds) < 0.01)
        self.length_box.setEnabled(not cut)
        # For an echo the window is how long its repeats ring (past the song's end if need be).
        self.length_title.setText(("에코가 울리는 시간" if korean else "Echo ring time") if style == "echo_out"
                                  else ("겹침 길이" if korean else "Overlap length"))
        self.position_box.setVisible(self.advanced)
        self.facts_box.setVisible(self.advanced)
        self.summary_label.setVisible(not self.advanced and bool(self.summary_label.text()))
        both_bpms = all(analysis is not None and analysis.bpm for analysis in self._analyses)
        self.tempo_check.setEnabled(not cut and both_bpms)
        self.key_combo.setEnabled(not cut)
        self.ramp_auto_check.setEnabled(not cut)
        self.ramp_spin.setEnabled(not cut and not self.ramp_auto_check.isChecked())
        bar = self._bar_seconds()
        self.bars_spin.setVisible(bar is not None)
        self.bars_label.setVisible(bar is None)
        ramp_bars = bars_text(self.ramp_spin.value(), self._analyses[0], korean)
        self.ramp_bars.setText(ramp_bars)
        # Effects are what the user came to shape: their settings show in both modes.
        self.echo_box.setVisible(style == "echo_out")
        self.tape_box.setVisible(style == "tape_stop")
        self.roll_box.setVisible(style == "beat_roll")
        self.cutoff_box.setVisible(style == "lowpass_out")
        self.roll_label.setText("반복 길이 (박) · BPM 미분석 시 120 BPM 기준" if korean
                               else "Loop length (beats) · 120 BPM if tempo unknown")
        self.cutoff_label.setText("마지막 로우패스 주파수" if korean else "Final lowpass cutoff")
        self.handoff_box.setVisible(self.advanced and style == "vocal_safe_eq")
        details = [box for box in (self.echo_box, self.tape_box, self.handoff_box,
                                  self.roll_box, self.cutoff_box) if not box.isHidden()]
        self.detail_box.setVisible(bool(details))
        name = (STYLE_CHOICES[style][0 if korean else 1] if style in STYLE_CHOICES and style != STYLE_AUTO
                else self._auto_style)
        self.detail_title.setText((f"{name} 세부 설정" if korean else f"{name} settings") if name
                                  else ("세부 설정" if korean else "Settings"))
        self.handoff_value.setText(f"{self.handoff_slider.value()}%")
        self.tape_entry_value.setText(f"{self.tape_entry_slider.value()}%")
        self._show_echo_feedback(self.echo_feedback_slider.value())
        for spin, clock_label, analysis in ((self.position_spin, self.position_clock, self._analyses[0]),
                                            (self.outgoing_spin, self.outgoing_clock, self._analyses[0]),
                                            (self.incoming_spin, self.incoming_clock, self._analyses[1])):
            position = bar_position(spin.value(), analysis, korean)
            clock_label.setText(f"= {clock(spin.value())}" + (f" · {position}" if position else ""))
        self._refresh_band(korean)
        for widget in self._all_headers:
            widget.show_state(widget.item in self._changed, widget.item in self._locked, korean)
        self.reset_button.setEnabled(override is not None and (self._manual or self._recommend))
        self.reset_button.setText(
            ("자동 추천 다시 받기" if korean else "Recommend again") if self._locked
            else ("자동으로 되돌리기" if korean else "Back to automatic"))
        self.reset_button.setToolTip(
            ("고정한 값은 그대로 두고 나머지를 분석이 다시 추천합니다 (Ctrl+Backspace)" if korean
             else "Analysis recommends everything again except the values you kept (Ctrl+Backspace)")
            if self._locked else
            ("이 전환을 분석이 정하는 자동 전환으로 되돌립니다 (Ctrl+Backspace)" if korean
             else "Let analysis decide this transition again (Ctrl+Backspace)"))
        self.mode_chip.setProperty("manual", self._manual)
        self.mode_chip.style().unpolish(self.mode_chip)
        self.mode_chip.style().polish(self.mode_chip)
        if override is None:
            self.mode_chip.setText("")
            self.style_description.clear()
            self.bars_label.clear()
            return
        count = len(self._changed)
        if self._manual:
            self.mode_chip.setText((f"직접 설정 · 자동과 다른 항목 {count}개 (● 표시)" if korean
                                    else f"Set by hand · {count} differ from auto (marked ●)") if count
                                   else ("직접 설정" if korean else "Set by hand"))
            self.style_description.setText(STYLE_CHOICES[override.style][2 if korean else 3])
        else:
            kept = ", ".join(FIELD_NAMES[name][0 if korean else 1] for name in self._locked)
            self.mode_chip.setText((f"● 자동 추천 · {kept} 고정" if korean else f"● Recommended · {kept} kept")
                                   if self._recommend else ("● 자동 (분석)" if korean else "● Automatic"))
            self.style_description.setText(
                (f"지금은 분석이 고른 ‘{self._auto_style}’로 섞입니다. 값을 바꾸면 직접 설정으로 바뀝니다."
                 if korean else
                 f"Analysis picked “{self._auto_style}”. Changing anything sets this transition by hand.")
                if self._auto_style else
                ("분석이 전환을 정합니다. 값을 바꾸면 직접 설정으로 바뀝니다." if korean
                 else "Analysis decides this transition. Changing anything sets it by hand."))
        self.bars_label.setText("" if cut else bars_text(override.duration, self._analyses[1], korean))

    def _refresh_band(self, korean: bool) -> None:
        band = self._band()
        name = _BAND_NAMES[band][0 if korean else 1]
        self.band_title.setText(f"{name} 교대 타이밍" if korean else f"{name}: when they change hands")
        editable = self._bands is not None
        for spin in self.band_spins.values():
            spin.setEnabled(editable)
        self.band_unavailable.setVisible(not editable)
        self.link_check.setEnabled(editable)
        if editable:
            out_window, in_window = self._bands[EQ_BANDS.index(band)]
            hole = band_hole(out_window, in_window)
            self.band_hole_label.setText(
                (f"겹침의 {hole * 100:.0f}% 동안 이 대역이 두 곡 모두 작아져 비어 들립니다." if korean
                 else f"For {hole * 100:.0f}% of the overlap neither song plays this band at full level: it sounds hollow.")
                if hole > 0.005 else "")
            self.band_hole_label.setVisible(hole > 0.005)
        else:
            self.band_hole_label.hide()

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
        for style, button in self.style_buttons.items():
            names = STYLE_CHOICES[style]
            button.setText(names[0] if korean else names[1])
            button.setToolTip(names[2] if korean else names[3])
        for label, (korean_name, english_name, _styles) in zip(self.group_labels, STYLE_GROUPS):
            label.setText(korean_name if korean else english_name)
        self.style_title.setText("전환 스타일" if korean else "Transition style")
        self.echo_beats_label.setText("에코 간격" if korean else "Echo delay")
        for beats, button in self.echo_beat_buttons.items():
            button.setText({0.5: ("½박", "½ beat"), 1.0: ("1박", "1 beat"), 2.0: ("2박", "2 beats")}[beats][
                0 if korean else 1])
        self.echo_tail_label.setText("여운 감쇠 (반복마다 줄어드는 양)" if korean
                                     else "Tail decay (how much each repeat drops)")
        self.echo_feedback_slider.setToolTip(
            "오른쪽일수록 에코가 천천히 줄어 오래 남습니다. 울리는 시간은 위의 '에코가 울리는 시간'으로 정합니다."
            if korean else
            "Further right, each repeat drops less and the echo rings on longer. "
            "How long it rings is the 'Echo ring time' above.")
        self.echo_low_cut_check.setText("에코 저음 걷어내기" if korean else "Cut the echo's lows")
        self.echo_low_cut_check.setToolTip("에코의 저음이 다음 곡의 킥과 겹치지 않게 합니다" if korean
                                           else "Keeps the echo's lows off the next song's kick")
        self.tape_entry_label.setText("다음 곡이 들어오는 지점 (멈춤 구간 대비)" if korean
                                      else "Where the next song comes in (share of the stop)")
        self.key_label.setText("키 맞춤" if korean else "Key match")
        self.key_combo.setToolTip(
            "나가는 곡의 끝부분만 반음 단위로 옮겨 다음 곡의 키와 어울리게 합니다. 템포 램프 구간 동안 서서히 바뀝니다."
            if korean else
            "Glides the outgoing song's tail by semitones to suit the next song's key, over the tempo ramp.")
        syncing, self._syncing = self._syncing, True
        try:
            index = max(0, self.key_combo.currentIndex())
            self.key_combo.clear()
            for shift in KEY_SHIFT_CHOICES:
                self.key_combo.addItem(
                    ("자동 (분석이 확실할 때)" if korean else "Automatic (when the keys are clear)") if shift is None
                    else ("끄기" if korean else "Off") if shift == 0
                    else (f"{shift:+d} 반음" if korean else f"{shift:+d} semitone{'s' if abs(shift) > 1 else ''}"))
            self.key_combo.setCurrentIndex(index)
        finally:
            self._syncing = syncing
        for seconds, button in self.length_presets.items():
            name = {4.0: ("짧게", "Short"), 8.0: ("보통", "Medium"), 16.0: ("길게", "Long")}[seconds]
            button.setText(f"{seconds:g}초" if korean else f"{seconds:g} s")
            button.setToolTip(f"{name[0]} · {seconds:g}초 겹침" if korean else f"{name[1]} · {seconds:g} s overlap")
        unit = " 초" if korean else " s"
        for spin in (self.length_spin, self.outgoing_spin, self.incoming_spin, self.position_spin, self.ramp_spin):
            spin.setSuffix(unit)
        self.bars_spin.setSuffix(" 마디" if korean else " bars")
        self.length_spin.setToolTip("두 곡이 함께 들리는 길이 (초)" if korean else "How long both songs play together (s)")
        self.bars_spin.setToolTip("같은 길이를 들어오는 곡의 마디로 입력" if korean
                                  else "The same length in the incoming song's bars")
        self.position_header.title.setText("전환 위치 · A 큐" if korean else "Where it sits · A cue")
        self.outgoing_header.title.setText("A 큐 (나가는 곡의 원본 시간)" if korean else "A cue (outgoing song time)")
        self.incoming_header.title.setText("B 시작 (들어오는 곡의 원본 시간)" if korean
                                           else "B start (incoming song time)")
        self.outgoing_label.setText("나가는 곡(A)의 이 지점에서 다음 곡이 섞이기 시작합니다." if korean
                                    else "Where in the outgoing song (A) the next one starts mixing in.")
        self.incoming_label.setText("들어오는 곡(B)을 이 지점부터 재생합니다. 인트로를 건너뛸 때 씁니다." if korean
                                    else "Where the incoming song (B) starts playing; use it to skip an intro.")
        for spin in (self.outgoing_spin, self.position_spin):
            spin.setToolTip("초 단위로 입력 · Enter로 적용" if korean else "Seconds · Enter applies")
        self.incoming_spin.setToolTip("초 단위로 입력 · Enter로 적용" if korean else "Seconds · Enter applies")
        self.tempo_title.setText("템포 맞춤" if korean else "Tempo match")
        self.tempo_check.setText("A를 B의 템포로 맞추기" if korean else "Match A to B's tempo")
        self.tempo_check.setToolTip(
            "켜면 템포 차이가 커도 A를 B의 템포로 맞춥니다. 두 곡의 BPM 분석이 필요합니다." if korean
            else "When on, A is matched to B's tempo however far apart they are. Needs both BPMs.")
        self.ramp_label.setText("템포가 바뀌는 구간 (믹스 시작 전)" if korean else "How long A takes to change tempo")
        self.ramp_auto_check.setText("자동" if korean else "Auto")
        self.ramp_auto_check.setToolTip("자동: 8마디 (템포 차이가 크면 16마디)" if korean
                                        else "Automatic: 8 bars (16 for a large tempo gap)")
        self.ramp_shape.setText("속도는 이 구간을 32단계로 나눠 고르게 바뀝니다 (렌더러 고정)." if korean
                                else "The speed changes evenly in 32 steps over it (fixed by the renderer).")
        self.handoff_label.setText("보컬 교대 지점 (겹침 대비)" if korean else "Vocal handoff (share of the overlap)")
        for key, button in self.eq_presets.items():
            button.setText(EQ_PRESETS[key][0 if korean else 1])
        self.presets_label.setText("모든 대역에 프리셋 적용" if korean else "Apply a preset to every band")
        self.band_row_labels["out"].setText("A 사라짐" if korean else "A fades out")
        self.band_row_labels["in"].setText("B 올라옴" if korean else "B fades in")
        self.band_columns.setText("겹침 구간 안의 시작 – 끝 (%) · 곡선: 등전력 사인 (렌더러 고정)" if korean
                                  else "Start – end within the overlap (%) · curve: equal-power sine (fixed)")
        self.band_unavailable.setText(
            "이 스타일은 대역을 나눠 섞지 않습니다. 아래 프리셋이나 스타일 ‘EQ 직접’을 고르면 대역별로 조절할 수 있습니다."
            if korean else
            "This style does not mix band by band. Pick a preset below or the ‘Custom EQ’ style to set each band.")
        self.link_check.setText("A·B 막대 함께 옮기기" if korean else "Move A and B together")
        self.band_hint.setText(
            "타임라인의 대역 레인에서 A(주황, 실선)·B(파랑, 점선) 막대를 끌어도 됩니다. "
            "스냅 설정을 따르며 Shift를 누르는 동안 스냅이 반대로 바뀝니다." if korean else
            "You can also drag the A (orange, solid) and B (blue, dashed) bars in the band lanes. "
            "They follow the snap setting; holding Shift inverts it.")
        self.facts_title.setText("현재 계획" if korean else "Current plan")
        for widget in self._all_headers:
            widget.retranslate(korean)
        self._refresh()
