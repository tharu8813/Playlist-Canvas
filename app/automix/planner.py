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
gets a non-1.0 rate, chosen to match the *outgoing* track's own BPM ("favor
outgoing" -- see app.automix.compatibility.resolve_target_bpm, whose result
this planner now applies directly as the incoming clip's rate instead of
recomputing an independent formula, so candidate scoring and the plan
actually rendered can no longer disagree on the target). The outgoing
clip's own audio placement and rate are always left exactly as they were.

Effective BPM propagation (Commit C, fixes the v1 simplification this
docstring used to describe): a clip already carrying a non-1.0
playback_rate -- because it was the *incoming* half of the previous
transition -- has an actual sounding tempo different from its raw analyzed
BPM. `_effective_analysis_for_outgoing` builds a `TrackAnalysis` view with
`bpm` replaced by that real, currently-playing tempo (`raw_bpm *
playback_rate`) before it is ever passed to `evaluate_compatibility`/
`generate_candidates` as the "outgoing" side of the *next* transition --
every other field (beats/downbeats/key/energy/vocal_activity, all
positions in the track's own original media time, never affected by
playback rate) passes through unchanged. Without this, a chain of several
transitions could compound tempo drift silently, since each pair would be
planned against a BPM number no longer matching what is actually playing
by the time that transition happens.

Candidate duration is authoritative (Commit C.1, fixes a real bug the
structure-anchor feature exposed): `TransitionCandidate.duration_seconds`
is now applied *directly* as `AudioRenderTransition.duration`, and the
outgoing clip's `source_out` is trimmed (via `dataclasses.replace` on the
already-appended clip -- `AudioRenderClip` is immutable) to
`best.outgoing_source_out`, instead of deriving the actual overlap from
`previous_clip.timeline_end - timeline_start`. That derivation silently
assumed the outgoing clip always plays to its own natural end
(`source_out == track.duration_seconds`, never trimmed), which was true
for every candidate *before* structure anchors existed (a tail-based cue
always lands near the track's real end anyway) but breaks whenever a
structure anchor (an early `outro_start`) sits well before it: a candidate
scored as a 16-second transition could render as a 30-second one, because
the "overlap" was actually "anchor position to the untrimmed clip's own
end," not the candidate's own `duration_seconds`. AutoMix intentionally
allows skipping part of a track's outro this way (subject to the existing
outgoing-tail-trim scoring penalty, `app.automix.candidates.WEIGHT_OUTGOING_TAIL_TRIM_PENALTY`)
-- this fix makes what actually gets rendered match what was scored,
without changing that policy. `build_presentation_and_metadata()` derives
presentation/chapter ownership purely from each window's *start* (never
from a clip's own `timeline_end`), so trimming a clip's `source_out` here
needs no special-casing there -- confirmed by
`tests/test_automix_planner.py`'s dedicated geometry tests.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace

from app.automix.candidates import (
    TransitionStrategy,
    generate_candidates,
    select_best_candidate,
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
    clips, transitions = _place_tracks(selected, analyses, structures, settings)
    presentation, metadata, duration = build_presentation_and_metadata(clips)
    plan = CompiledRenderPlan(
        audio=AudioRenderPlan(clips=clips, transitions=transitions),
        presentation=presentation,
        metadata=metadata,
        duration_seconds=duration,
    )
    validate_compiled_render_plan(plan)
    return plan


def _effective_analysis_for_outgoing(
    analysis: TrackAnalysis, playback_rate: float,
) -> TrackAnalysis:
    """A view of ``analysis`` with ``bpm`` replaced by the tempo it is
    actually sounding at right now (raw analyzed BPM * the rate already
    applied to this clip by the transition that placed it) -- see the
    module docstring's "Effective BPM propagation". ``playback_rate`` is
    always 1.0 for a clip that was never rate-shifted (the first clip, or
    one placed by a non-BEAT_MATCH/fallback transition), so this is a
    no-op in every case except a BEAT_MATCH clip chained after another.
    """
    if analysis.bpm is None or playback_rate == 1.0:
        return analysis
    return replace(analysis, bpm=analysis.bpm * playback_rate)


def _place_tracks(
    tracks: list[PlaylistTrack],
    analyses: Mapping[str, TrackAnalysis],
    structures: Mapping[str, TrackStructureAnalysis],
    settings: AutoMixTransitionSettings,
) -> tuple[tuple[AudioRenderClip, ...], tuple[AudioRenderTransition, ...]]:
    clips: list[AudioRenderClip] = []
    transitions: list[AudioRenderTransition] = []
    natural_cursor = 0.0
    actual_cursor = 0.0
    # track_id -> the playback_rate actually applied to that track's own
    # clip (1.0 unless it was the incoming half of a BEAT_MATCH transition)
    # -- consulted when that same track later becomes the *outgoing* side
    # of the next transition, so its real (rate-adjusted) tempo is what
    # gets planned against, not its raw analyzed BPM.
    applied_rates: dict[str, float] = {}

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
            timeline_start, source_in, playback_rate, transition, trimmed_outgoing_source_out = _plan_overlap(
                previous, previous_track, track, analyses, structures, settings, actual_cursor,
                applied_rates.get(previous_track.id, 1.0),
            )
            if transition is not None:
                transitions.append(transition)
                # AudioRenderClip is immutable: the previous clip was
                # already appended in an earlier iteration, so the trim
                # this transition's candidate requires (see planner.py's
                # module docstring, "Candidate duration is authoritative")
                # replaces it in place rather than mutating it.
                clips[-1] = replace(previous, source_out=trimmed_outgoing_source_out)

        clip = AudioRenderClip(
            clip_id=f"automix:{track.id}",
            track_id=track.id,
            timeline_start=timeline_start,
            source_in=source_in,
            source_out=track.duration_seconds,
            playback_rate=playback_rate,
        )
        clips.append(clip)
        applied_rates[track.id] = playback_rate
        natural_cursor = natural_start + track.duration_seconds
        actual_cursor = clip.timeline_end

    return tuple(clips), tuple(transitions)


def _plan_overlap(
    previous_clip: AudioRenderClip,
    previous_track: PlaylistTrack,
    track: PlaylistTrack,
    analyses: Mapping[str, TrackAnalysis],
    structures: Mapping[str, TrackStructureAnalysis],
    settings: AutoMixTransitionSettings,
    actual_cursor: float,
    outgoing_applied_rate: float,
) -> tuple[float, float, float, AudioRenderTransition | None, float]:
    """Decide clip placement for ``track`` following ``previous_clip`` with no explicit gap.

    Returns (timeline_start, source_in, playback_rate, transition_or_none,
    trimmed_outgoing_source_out). The last element is only meaningful when
    a transition was actually produced -- ``_place_tracks`` uses it to
    replace the already-appended previous clip's ``source_out`` (see
    module docstring, "Candidate duration is authoritative"); ignored by
    callers whenever ``transition_or_none`` is ``None``. Falls back to
    plain sequential adjacency (rate 1.0, no transition) whenever analysis
    is missing, tempo is incompatible, or the best candidate is a CUT.
    """
    fallback = (actual_cursor, 0.0, 1.0, None, previous_clip.source_out)
    outgoing_analysis = analyses.get(previous_track.id)
    incoming_analysis = analyses.get(track.id)
    if outgoing_analysis is None or incoming_analysis is None or not settings.enabled:
        return fallback

    effective_outgoing_analysis = _effective_analysis_for_outgoing(outgoing_analysis, outgoing_applied_rate)

    compatibility = evaluate_compatibility(effective_outgoing_analysis, incoming_analysis, settings)
    candidates = generate_candidates(
        effective_outgoing_analysis, incoming_analysis, compatibility, settings,
        outgoing_playback_rate=previous_clip.playback_rate,
        outgoing_structure=structures.get(previous_track.id),
        incoming_structure=structures.get(track.id),
    )
    best = select_best_candidate(candidates)
    if best is None or best.strategy is TransitionStrategy.CUT or best.duration_seconds <= 0.0:
        return fallback

    source_in = best.incoming_source_time
    # Unified with the candidate's own computed rate (see
    # compatibility.resolve_target_bpm's docstring): previously this
    # recomputed an independent "outgoing.bpm / incoming_effective_bpm"
    # formula here, which could silently diverge from what candidates.py
    # had actually scored. best.incoming_rate is already 1.0 for every
    # non-BEAT_MATCH strategy (fixed_crossfade/beat_aligned_crossfade/cut),
    # so this replaces the old strategy-specific branch too.
    playback_rate = best.incoming_rate

    # Anchor timestamps are in the original media, not the playlist clock.
    timeline_start = previous_clip.timeline_start + (
        best.outgoing_source_time - previous_clip.source_in
    ) / previous_clip.playback_rate
    if timeline_start <= previous_clip.timeline_start:
        return fallback

    # duration_seconds is authoritative (Commit C.1): the actual overlap is
    # exactly the candidate's own timeline duration, never derived from
    # previous_clip.timeline_end (which assumed the outgoing clip always
    # plays to its own untrimmed natural end -- see module docstring).
    # candidates.py already validated that best.outgoing_source_out /
    # best.incoming_source_time + this candidate's own source spans fit
    # within each track's real duration, so no further availability check
    # is needed here.
    overlap = best.duration_seconds

    transition_type = _STRATEGY_TRANSITION_TYPES[best.strategy]
    # DSP Phase 2: the mixing style is decided here, where the analysis and
    # the exact window both exist, and travels in the plan -- the renderer
    # never re-derives it. Timing above is already final and is not touched.
    decision = select_transition_dsp(
        best, compatibility, effective_outgoing_analysis, incoming_analysis,
        structures.get(previous_track.id), structures.get(track.id),
    )
    transition = AudioRenderTransition(
        clip_a=previous_clip.clip_id, clip_b=f"automix:{track.id}",
        timeline_start=timeline_start, duration=overlap, type=transition_type,
        dsp=decision.dsp, dsp_reasons=decision.reasons,
    )
    # One line per transition in the app log, for tuning against real music.
    LOGGER.info("AutoMix transition: %s", describe_transition(
        transition, previous_track.title or previous_track.id, track.title or track.id,
    ))
    return timeline_start, source_in, playback_rate, transition, best.outgoing_source_out
