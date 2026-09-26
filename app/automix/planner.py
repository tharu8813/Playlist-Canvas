"""AutoMixPlanner: Timeline + analyses + settings -> CompiledRenderPlan.

Preview and Export never see how a transition was decided (roadmap Phase 4
section 1) -- they only ever read a CompiledRenderPlan, exactly as they do
for the Sequential compiler in app/timeline/compiler.py. This module is a
second, independent producer of that same shape; it does not touch
compile_timeline()/compile_playlist() at all, so legacy (non-AutoMix)
playback is provably unaffected by anything here.

Tempo policy (DJ-style): every track plays at its own tempo from its first
beat until its last bars. For BEAT_MATCH the *outgoing* track eases onto
the incoming track's tempo over up to RAMP_BARS bars before their overlap
(AudioRenderClip.tempo_ramp) and holds it through the overlap; the
incoming track never changes speed. An earlier policy instead played the
whole incoming track at the outgoing tempo, which then carried into the
next pair, so a playlist drifted further from its tracks' real tempos with
every transition and never came back.

Silence: the first track starts at its first sound, the last stops at its
last, and a CUT or unplanned junction butts the two sounds together.

Candidate duration is authoritative (Commit C.1): the planner applies
`TransitionCandidate.duration_seconds` and `outgoing_source_out` directly.
Candidates now retain the audible outgoing ending and finalize their actual
source span and timeline duration before scoring. An early structure hint
cannot discard the remaining audio or inflate the rendered overlap after
scoring. Presentation and chapter ownership still follow transition starts.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace

from app.automix.candidates import (
    TransitionStrategy,
    _structure_incoming_anchor,
    _structure_outgoing_anchor,
    audible_end,
    audible_start,
    generate_candidates,
    select_best_candidate,
    vocal_intro_end,
    vocal_outro_start,
)
from app.automix.compatibility import evaluate_compatibility
from app.automix.models import TrackAnalysis
from app.automix.settings import AutoMixTransitionSettings
from app.automix.structure.models import TrackStructureAnalysis
from app.automix.transition_style import describe_transition, select_transition_dsp
from app.models.playlist import PlaylistTrack
from app.timeline.models import TransitionType
from app.timeline.render_plan import (
    AudioRenderClip,
    AudioRenderPlan,
    AudioRenderTransition,
    CompiledRenderPlan,
    TempoRamp,
    build_presentation_and_metadata,
    validate_compiled_render_plan,
)

LOGGER = logging.getLogger(__name__)
_GAP_EPSILON = 1e-6

_STRATEGY_TRANSITION_TYPES = {
    TransitionStrategy.BEAT_MATCH: TransitionType.BEAT_MATCH,
    TransitionStrategy.BEAT_ALIGNED_CROSSFADE: TransitionType.EQUAL_POWER,
    TransitionStrategy.FIXED_CROSSFADE: TransitionType.CROSSFADE,
}
"""CUT is deliberately absent: a CUT candidate means no overlap at all, so
no AudioRenderTransition is emitted for that boundary (see _place_tracks).
TransitionType.AUTOMIX is reserved until a renderer gives it a distinct
meaning (roadmap section 9)."""


def compile_automix(
    tracks: Sequence[PlaylistTrack],
    analyses: Mapping[str, TrackAnalysis],
    settings: AutoMixTransitionSettings,
    structures: Mapping[str, TrackStructureAnalysis] | None = None,
    *, log_diagnostics: bool = True,
) -> CompiledRenderPlan:
    """Compile enabled ``tracks`` into a CompiledRenderPlan with AutoMix overlaps.

    Disabled tracks are skipped entirely (never occupy a clip or influence
    placement), matching Sequential's ``enabled_only=True`` behavior. A
    track with an explicit ``start_time_seconds`` gap preserves that gap
    verbatim rather than being overlapped into (roadmap section 12: "For
    v1, preserving explicit requested gaps ... is a reasonable conservative
    policy"). Missing or incompatible analysis on any one pair only
    degrades that pair -- it never prevents earlier or later pairs from
    getting a full AutoMix transition.

    ``structures`` is optional (defaults to ``None``, meaning "no structure
    data at all") and independent per track: a track missing from it, or
    entirely absent, only ever removes the structure-aware
    bonuses/candidates for that side of a pair (see
    ``app.automix.candidates.generate_candidates``) -- rhythm-only
    planning behaves exactly as it did before this parameter existed.
    """
    selected = [track for track in tracks if track.enabled]
    structures = structures or {}
    analyses = {
        track_id: _with_lyric_vocals(track, analyses[track_id])
        for track in selected if (track_id := track.id) in analyses
    }
    clips, transitions = _place_tracks(selected, analyses, structures, settings, log_diagnostics)
    presentation, metadata, duration = build_presentation_and_metadata(clips)
    plan = CompiledRenderPlan(
        audio=AudioRenderPlan(clips=clips, transitions=transitions),
        presentation=presentation,
        metadata=metadata,
        duration_seconds=duration,
    )
    validate_compiled_render_plan(plan)
    return plan


LYRIC_LINE_MAX_SECONDS = 8.0
"""A synced-lyrics line counts as sung until the next line or this long, so a
long instrumental break before the next line is not taken for singing."""


def _with_lyric_vocals(track: PlaylistTrack, analysis: TrackAnalysis) -> TrackAnalysis:
    """``analysis`` plus where ``track``'s synced lyrics say it is sung.

    Cue times are lyric time; lyric time = audio time + the track's lyric
    offset (see app.preview.frame_state.resolve_lyrics_cue_state).
    """
    offset = float(track.lyrics_timing_offset_seconds)
    spans = []
    for cue in track.lyrics:
        if not str(cue.get("text", "")).strip():
            continue
        start = max(0.0, float(cue["start"]) - offset)
        end = min(analysis.duration_seconds, float(cue["end"]) - offset, start + LYRIC_LINE_MAX_SECONDS)
        if end > start:
            spans.append((start, end))
    return replace(analysis, lyric_vocal_spans=tuple(spans)) if spans else analysis


def _place_tracks(
    tracks: list[PlaylistTrack],
    analyses: Mapping[str, TrackAnalysis],
    structures: Mapping[str, TrackStructureAnalysis],
    settings: AutoMixTransitionSettings,
    log_diagnostics: bool = True,
) -> tuple[tuple[AudioRenderClip, ...], tuple[AudioRenderTransition, ...]]:
    clips: list[AudioRenderClip] = []
    transitions: list[AudioRenderTransition] = []
    natural_cursor = 0.0
    actual_cursor = 0.0

    for index, track in enumerate(tracks):
        natural_floor = natural_cursor
        requested_start = track.start_time_seconds
        natural_start = (
            max(natural_floor, requested_start) if requested_start is not None else natural_floor
        )
        explicit_gap = natural_start - natural_floor

        if index == 0:
            timeline_start = natural_start
            source_in = _audible_start(analyses.get(track.id), settings)  # no dead air before the mix
        elif explicit_gap > _GAP_EPSILON:
            timeline_start = actual_cursor + explicit_gap
            source_in = 0.0
        else:
            previous = clips[-1]
            into_previous = transitions[-1] if transitions and transitions[-1].clip_b == previous.clip_id else None
            # The outgoing tempo ramp must not start while the previous transition is still mixing.
            ramp_floor = (previous.source_at(into_previous.timeline_start + into_previous.duration)
                          if into_previous is not None else previous.source_in)
            outgoing_clip, timeline_start, source_in, transition = _plan_overlap(
                previous, tracks[index - 1], track, analyses, structures, settings, ramp_floor, log_diagnostics,
            )
            # AudioRenderClip is immutable: the outgoing clip's trimmed tail
            # (and tempo ramp) replaces the one appended last iteration.
            clips[-1] = outgoing_clip
            if transition is not None:
                transitions.append(transition)

        clip = AudioRenderClip(
            clip_id=f"automix:{track.id}",
            track_id=track.id,
            timeline_start=timeline_start,
            source_in=source_in,
            source_out=track.duration_seconds,
        )
        clips.append(clip)
        natural_cursor = natural_start + track.duration_seconds
        actual_cursor = clip.timeline_end

    if clips:  # nor after it
        last = clips[-1]
        clips[-1] = replace(last, source_out=_audible_end(analyses.get(last.track_id), settings, last))
    return tuple(clips), tuple(transitions)


RAMP_BARS = 8
"""The outgoing track eases onto the incoming tempo over this many of its own
bars before their overlap (fewer when the track has no room): one phrase, the
way a DJ rides the pitch fader instead of jumping it."""


def _audible_start(analysis: TrackAnalysis | None, settings: AutoMixTransitionSettings) -> float:
    return audible_start(analysis) if analysis is not None and settings.enabled else 0.0


def _audible_end(analysis: TrackAnalysis | None, settings: AutoMixTransitionSettings, clip: AudioRenderClip) -> float:
    """Where ``clip`` should stop at the latest: its track's audible end, never before it starts."""
    if analysis is None or not settings.enabled:
        return clip.source_out
    return max(clip.source_in, min(clip.source_out, audible_end(analysis)))


def _plan_overlap(
    previous_clip: AudioRenderClip,
    previous_track: PlaylistTrack,
    track: PlaylistTrack,
    analyses: Mapping[str, TrackAnalysis],
    structures: Mapping[str, TrackStructureAnalysis],
    settings: AutoMixTransitionSettings,
    ramp_floor: float,
    log_diagnostics: bool = True,
) -> tuple[AudioRenderClip, float, float, AudioRenderTransition | None]:
    """Decide how ``track`` follows ``previous_clip`` with no explicit gap.

    Returns (outgoing clip -- ``previous_clip`` with its tail trimmed and,
    for BEAT_MATCH, its tempo ramp --, incoming timeline_start, incoming
    source_in, transition_or_none). The incoming clip always plays at its
    own tempo. Without a transition (missing analysis, CUT) the two tracks
    butt together with the silence between their sounds removed.
    """
    outgoing_analysis = analyses.get(previous_track.id)
    incoming_analysis = analyses.get(track.id)

    def adjacent(outgoing_source_out: float, incoming_source_in: float):
        outgoing_clip = replace(previous_clip, source_out=max(previous_clip.source_in, outgoing_source_out))
        return outgoing_clip, outgoing_clip.timeline_end, incoming_source_in, None

    fallback = adjacent(_audible_end(outgoing_analysis, settings, previous_clip),
                        _audible_start(incoming_analysis, settings))
    if outgoing_analysis is None or incoming_analysis is None or not settings.enabled:
        return fallback

    compatibility = evaluate_compatibility(outgoing_analysis, incoming_analysis, settings)
    candidates = generate_candidates(
        outgoing_analysis, incoming_analysis, compatibility, settings,
        outgoing_playback_rate=previous_clip.playback_rate,
        outgoing_structure=structures.get(previous_track.id),
        incoming_structure=structures.get(track.id),
    )
    best = select_best_candidate(candidates)
    if best is None or best.duration_seconds <= 0.0:
        return fallback
    if best.strategy is TransitionStrategy.CUT:
        return adjacent(best.outgoing_source_out, best.incoming_source_time)

    source_in = best.incoming_source_time
    cue = best.outgoing_source_time
    outgoing_clip = replace(previous_clip, source_out=best.outgoing_source_out)
    ramp_seconds = 0.0
    if abs(best.outgoing_rate - 1.0) > 1e-9:
        # Walk the outgoing track onto the incoming tempo over its last bars
        # before the cue, then hold that rate through the overlap: its beats
        # land on the incoming ones, and the incoming track never changes speed.
        bar = (outgoing_analysis.meter_numerator or 4) * 60.0 / outgoing_analysis.bpm
        ramp_start = min(cue, max(cue - RAMP_BARS * bar, ramp_floor, previous_clip.source_in))
        ramp_seconds = cue - ramp_start
        outgoing_clip = replace(outgoing_clip, tempo_ramp=TempoRamp(ramp_start, cue, best.outgoing_rate))

    # Anchor timestamps are in the original media, not the playlist clock.
    timeline_start = outgoing_clip.timeline_at(cue)
    if timeline_start <= previous_clip.timeline_start:
        return fallback

    # duration_seconds is authoritative (Commit C.1): the candidate already
    # sized it as the outgoing tail from the cue at the overlap rate, which
    # is exactly outgoing_clip.timeline_end - timeline_start.
    overlap = best.duration_seconds

    transition_type = _STRATEGY_TRANSITION_TYPES[best.strategy]
    # DSP Phase 2: the mixing style is decided here, where the analysis and
    # the exact window both exist, and travels in the plan -- the renderer
    # never re-derives it. Timing above is already final and is not touched.
    decision = select_transition_dsp(
        best, compatibility, outgoing_analysis, incoming_analysis,
        structures.get(previous_track.id), structures.get(track.id),
    )
    outgoing_structure = structures.get(previous_track.id)
    incoming_structure = structures.get(track.id)
    details = (
        ("strategy", best.strategy.value),
        ("bars", best.bars),
        ("score", best.score),
        ("outgoing_bpm", outgoing_analysis.bpm),
        ("incoming_bpm", incoming_analysis.bpm),
        ("target_bpm", best.target_bpm),
        ("outgoing_rate", best.outgoing_rate),
        ("tempo_ramp_seconds", ramp_seconds),
        ("incoming_rate", best.incoming_rate),
        ("tempo_delta_percent", compatibility.tempo_shift_percent),
        ("half_double_tempo", compatibility.used_half_double),
        ("outgoing_beat_confidence", outgoing_analysis.bpm_confidence),
        ("incoming_beat_confidence", incoming_analysis.bpm_confidence),
        ("outgoing_downbeat_confidence", outgoing_analysis.meter_confidence),
        ("incoming_downbeat_confidence", incoming_analysis.meter_confidence),
        # "basic" = no downbeat model and no vocal detection; "*-novocals" =
        # vocal detection failed. Preview surfaces both (AutoMixDetailsPanel).
        ("outgoing_analyzer", outgoing_analysis.analyzer_id),
        ("incoming_analyzer", incoming_analysis.analyzer_id),
        ("outgoing_cue", best.outgoing_source_time),
        ("outgoing_cut", best.outgoing_source_out),
        ("incoming_cue", best.incoming_source_time),
        ("outgoing_tail_trimmed", outgoing_analysis.duration_seconds - best.outgoing_source_out),
        # Vocal-based sections: None = not measured (never "no vocals").
        ("outgoing_vocal_outro_start", vocal_outro_start(outgoing_analysis)),
        ("incoming_vocal_intro_end", vocal_intro_end(incoming_analysis)),
        ("outgoing_structure_anchor", _structure_outgoing_anchor(outgoing_structure)),
        ("incoming_structure_anchor", _structure_incoming_anchor(incoming_structure)),
        ("outgoing_key", outgoing_analysis.key),
        ("incoming_key", incoming_analysis.key),
        *decision.metrics,
    )
    transition = AudioRenderTransition(
        clip_a=previous_clip.clip_id, clip_b=f"automix:{track.id}",
        timeline_start=timeline_start, duration=overlap, type=transition_type,
        dsp=decision.dsp, dsp_reasons=decision.reasons, details=details, vocal_handoff=decision.vocal_handoff,
    )
    # One line per transition in the app log, for tuning against real music.
    if log_diagnostics:
        LOGGER.info("AutoMix transition: %s", describe_transition(
            transition, previous_track.title or previous_track.id, track.title or track.id,
        ))
    return outgoing_clip, timeline_start, source_in, transition
