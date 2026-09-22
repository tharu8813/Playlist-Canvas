"""AutoMixPlanner: Timeline + analyses + settings -> CompiledRenderPlan.

Preview and Export never see how a transition was decided (roadmap Phase 4
section 1) -- they only ever read a CompiledRenderPlan, exactly as they do
for the Sequential compiler in app/timeline/compiler.py. This module is a
second, independent producer of that same shape; it does not touch
compile_timeline()/compile_playlist() at all, so legacy (non-AutoMix)
playback is provably unaffected by anything here.

Per-clip playback rate policy (why only the incoming track's rate ever
changes): AudioRenderClip has one playback_rate for its entire span, but a
clip plays two roles -- "outgoing" for its own transition into the next
track, and "incoming" for the transition that placed it. Changing a clip's
rate after it has already been placed (i.e. when it later becomes
"outgoing") would retroactively invalidate a decision already made for its
predecessor's transition. This planner resolves that by never revisiting a
clip's rate once fixed: only the *incoming* clip of a BEAT_MATCH transition
gets a non-1.0 rate, chosen to match the *outgoing* track's own analyzed
BPM ("favor outgoing", one of Phase 3's documented target-BPM policies).
The outgoing clip's own audio placement and rate are always left exactly
as they were.

Known v1 simplification: each transition's target BPM uses the outgoing
track's raw analyzed BPM, not any rate already applied to it by an earlier
transition. Tempo drift can compound slightly over a long chain of
transitions; the fix (propagating each clip's *effective* BPM forward
through the chain) is deferred until real usage shows it matters.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.automix.candidates import (
    TransitionStrategy,
    generate_candidates,
    select_best_candidate,
)
from app.automix.compatibility import evaluate_compatibility
from app.automix.models import TrackAnalysis
from app.automix.settings import AutoMixTransitionSettings
from app.models.playlist import PlaylistTrack
from app.timeline.models import TransitionType
from app.timeline.render_plan import (
    AudioRenderClip,
    AudioRenderPlan,
    AudioRenderTransition,
    CompiledRenderPlan,
    build_presentation_and_metadata,
    validate_compiled_render_plan,
)

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
    """
    selected = [track for track in tracks if track.enabled]
    clips, transitions = _place_tracks(selected, analyses, settings)
    presentation, metadata, duration = build_presentation_and_metadata(clips)
    plan = CompiledRenderPlan(
        audio=AudioRenderPlan(clips=clips, transitions=transitions),
        presentation=presentation,
        metadata=metadata,
        duration_seconds=duration,
    )
    validate_compiled_render_plan(plan)
    return plan


def _place_tracks(
    tracks: list[PlaylistTrack],
    analyses: Mapping[str, TrackAnalysis],
    settings: AutoMixTransitionSettings,
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
            source_in = 0.0
            playback_rate = 1.0
        elif explicit_gap > _GAP_EPSILON:
            timeline_start = actual_cursor + explicit_gap
            source_in = 0.0
            playback_rate = 1.0
        else:
            previous = clips[-1]
            previous_track = tracks[index - 1]
            timeline_start, source_in, playback_rate, transition = _plan_overlap(
                previous, previous_track, track, analyses, settings, actual_cursor,
            )
            if transition is not None:
                transitions.append(transition)

        clip = AudioRenderClip(
            clip_id=f"automix:{track.id}",
            track_id=track.id,
            timeline_start=timeline_start,
            source_in=source_in,
            source_out=track.duration_seconds,
            playback_rate=playback_rate,
        )
        clips.append(clip)
        natural_cursor = natural_start + track.duration_seconds
        actual_cursor = clip.timeline_end

    return tuple(clips), tuple(transitions)


def _plan_overlap(
    previous_clip: AudioRenderClip,
    previous_track: PlaylistTrack,
    track: PlaylistTrack,
    analyses: Mapping[str, TrackAnalysis],
    settings: AutoMixTransitionSettings,
    actual_cursor: float,
) -> tuple[float, float, float, AudioRenderTransition | None]:
    """Decide clip placement for ``track`` following ``previous_clip`` with no explicit gap.

    Returns (timeline_start, source_in, playback_rate, transition_or_none).
    Falls back to plain sequential adjacency (rate 1.0, no transition)
    whenever analysis is missing, tempo is incompatible, or the best
    candidate is a CUT.
    """
    fallback = (actual_cursor, 0.0, 1.0, None)
    outgoing_analysis = analyses.get(previous_track.id)
    incoming_analysis = analyses.get(track.id)
    if outgoing_analysis is None or incoming_analysis is None or not settings.enabled:
        return fallback

    compatibility = evaluate_compatibility(outgoing_analysis, incoming_analysis, settings)
    candidates = generate_candidates(outgoing_analysis, incoming_analysis, compatibility, settings)
    best = select_best_candidate(candidates)
    if best is None or best.strategy is TransitionStrategy.CUT or best.duration_seconds <= 0.0:
        return fallback

    source_in = best.incoming_source_time
    playback_rate = 1.0
    if best.strategy is TransitionStrategy.BEAT_MATCH and compatibility.incoming_effective_bpm:
        # See module docstring: "favor outgoing" -- only the incoming clip's
        # rate ever moves, matching the outgoing track's own analyzed BPM.
        playback_rate = outgoing_analysis.bpm / compatibility.incoming_effective_bpm

    # Anchor timestamps are in the original media, not the playlist clock.
    # Keep the outgoing tail intact and fade over what remains after the anchor.
    timeline_start = previous_clip.timeline_start + (
        best.outgoing_source_time - previous_clip.source_in
    ) / previous_clip.playback_rate
    overlap = previous_clip.timeline_end - timeline_start
    incoming_available = (track.duration_seconds - source_in) / playback_rate
    if (timeline_start <= previous_clip.timeline_start or overlap <= 0.0
            or overlap > incoming_available):
        return fallback

    transition_type = _STRATEGY_TRANSITION_TYPES[best.strategy]
    transition = AudioRenderTransition(
        clip_a=previous_clip.clip_id, clip_b=f"automix:{track.id}",
        timeline_start=timeline_start, duration=overlap, type=transition_type,
    )
    return timeline_start, source_in, playback_rate, transition
