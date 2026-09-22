"""Transition candidate generation and explainable scoring.

Pure data/functions only (roadmap Phase 3 section 12): no Qt, no FFmpeg
process, no project mutation, and no CompiledRenderPlan yet -- Phase 4
consumes this module's output to build one. Given identical
TrackAnalysis/settings input, output order and scores are always the
same: bar lengths are tried in one fixed order and nothing here uses
randomness (roadmap section 10, "Determinism").

Scoring weights, for a later developer tuning them. The base five (roadmap
Phase 3) sum to 1.0; the Phase 7 advanced three are additive bonuses/
penalties on top, applied only when both tracks actually have that data --
with none of it, the score is identical to Phase 3's (roadmap Phase 7
section 8 / section 13's "same basic result when advanced data
unavailable"):

    Factor                                          Weight   Effect
    ---------------------------------------------------------------
    Average BPM confidence                          0.35     +
    Downbeat/meter confidence (BEAT_MATCH only)      0.15     +
    Tempo shift vs. the allowed budget               0.25     -
    Requested bar length actually used                0.15     +
    Cue proximity to the ideal anchor (beat/downbeat) 0.10     +
    Compatible key (Camelot wheel), if both known     0.06     + (bonus only, never a penalty -- section 3)
    Similar energy level, if both known               0.04     +
    Vocal activity on both sides of the overlap,      0.10     - (if both tracks have vocal_activity data)
      if both known

Commit C adds five more, same discipline -- additive, and each independently
a no-op without the relevant structure/trim data, never changing a result
that had none of it:

    Structure anchor alignment (outgoing or incoming, each) 0.06  + (bonus/hint only, see generate_candidates)
    Local energy continuity at the actual cue points        0.05  + (more precise than the global scalar above)
    Incoming trim beyond INCOMING_TRIM_SOFT_LIMIT_SECONDS   0.08  -
    Outgoing tail beyond MAXIMUM_OUTGOING_TAIL_TRIM_SECONDS 0.05  -
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.automix.analysis.key import camelot_compatible
from app.automix.compatibility import TransitionCompatibility, resolve_target_bpm
from app.automix.models import TrackAnalysis
from app.automix.settings import AutoMixTransitionSettings
from app.automix.structure.models import TrackStructureAnalysis

BAR_LENGTHS = (4, 8, 16)
DEFAULT_METER_NUMERATOR = 4

WEIGHT_CONFIDENCE = 0.35
WEIGHT_METER = 0.15
WEIGHT_TEMPO = 0.25
WEIGHT_LENGTH = 0.15
WEIGHT_PROXIMITY = 0.10

WEIGHT_HARMONIC_BONUS = 0.06
WEIGHT_ENERGY_CONTINUITY = 0.04
WEIGHT_VOCAL_OVERLAP_PENALTY = 0.10

# Commit C: structure-aware additions. Same discipline as the Phase 7 block
# above -- additive bonuses/penalties applied only when the relevant
# structure data actually exists, so a candidate's score is identical to
# the pre-Commit-C result whenever no TrackStructureAnalysis is available
# for either side (roadmap: "structure가 없으면 기존 planner 결과 유지").
WEIGHT_STRUCTURE_ANCHOR_BONUS = 0.06
"""Bonus when a candidate's cue lands near a structure anchor (outro/
section boundary for outgoing, intro_end/section boundary for incoming) --
a hint the anchor was actually musically meaningful for this candidate,
never a requirement (roadmap: "structure anchor를 무조건 transition point로
쓰지 말고 candidate bonus/hint로만 사용")."""
WEIGHT_LOCAL_ENERGY_CONTINUITY = 0.05
"""Local (structure energy_curve, time-resolved) energy continuity bonus,
additive on top of WEIGHT_ENERGY_CONTINUITY's existing global-scalar
comparison -- more precise when available, since it compares energy at the
actual cue points instead of each track's single overall energy figure."""
WEIGHT_INCOMING_TRIM_PENALTY = 0.08
WEIGHT_OUTGOING_TAIL_TRIM_PENALTY = 0.05

STRUCTURE_ANCHOR_PROXIMITY_TOLERANCE_SECONDS = 6.0
"""How close a candidate's cue must land to a structure anchor to count as
"anchored" for scoring -- generous enough to survive bar-length/downbeat-
snap rounding, tight enough not to credit an unrelated candidate."""
INCOMING_TRIM_SOFT_LIMIT_SECONDS = 30.0
"""Beyond this much of the incoming track skipped before the transition
even starts, penalize increasingly -- trimming away most of an intro is a
much more aggressive edit than the bar-length-based candidates ever
produce on their own; only a structure anchor (a long intro_end) can push
a candidate this far in, and it should not win purely on that anchor's
say-so."""
MAXIMUM_OUTGOING_TAIL_TRIM_SECONDS = 60.0
"""A structure anchor (outro_start/late section) suggesting a transition
start more than this many seconds before the track's own natural end is
treated with rising skepticism -- a plausible outro is usually tens of
seconds, not minutes; an anchor this far from the end more likely reflects
a structure-analysis error than a deliberately long instrumental outro."""


class TransitionStrategy(str, Enum):
    """A planning-time classification, not a DSP instruction.

    Distinct from ``app.timeline.models.TransitionType``: that enum
    describes what an ``AudioRenderTransition`` should eventually render;
    this one describes which family of candidate a planner considered and
    why, before any render-facing type is chosen (Phase 4's concern).
    """

    BEAT_MATCH = "beat_match"
    BEAT_ALIGNED_CROSSFADE = "beat_aligned_crossfade"
    FIXED_CROSSFADE = "fixed_crossfade"
    CUT = "cut"


@dataclass(frozen=True, slots=True)
class TransitionCandidate:
    """One possible transition point between two tracks, with its own score."""

    from_track_id: str
    to_track_id: str

    outgoing_source_time: float
    incoming_source_time: float

    bars: int
    duration_seconds: float

    outgoing_bpm: float | None
    incoming_bpm: float | None
    target_bpm: float | None

    outgoing_rate: float
    incoming_rate: float

    score: float
    confidence: float

    strategy: TransitionStrategy
    reasons: tuple[str, ...]


def select_best_candidate(candidates: list[TransitionCandidate]) -> TransitionCandidate | None:
    """Highest-scoring candidate, first one seen on a tie (stable, no randomness)."""
    if not candidates:
        return None
    best = candidates[0]
    for candidate in candidates[1:]:
        if candidate.score > best.score:
            best = candidate
    return best


def generate_candidates(
    outgoing: TrackAnalysis, incoming: TrackAnalysis,
    compatibility: TransitionCompatibility, settings: AutoMixTransitionSettings,
    *,
    outgoing_structure: TrackStructureAnalysis | None = None,
    incoming_structure: TrackStructureAnalysis | None = None,
) -> list[TransitionCandidate]:
    """Generate every transition candidate worth considering for this pair.

    Degrades through, from best to worst (roadmap section 9):
    BEAT_MATCH (both tracks' beat_alignment_quality is "reliable") ->
    BEAT_ALIGNED_CROSSFADE (BPM usable but beat/bar confidence is not) ->
    FIXED_CROSSFADE (incompatible tempo or missing BPM, but both tracks
    have enough source duration for a plain timed crossfade) -> CUT (not
    enough room for even a fixed crossfade).

    ``outgoing_structure``/``incoming_structure`` are optional and
    independent of each other (one-sided structure data is used safely --
    neither side requires the other): when given, they add extra
    structure-anchored candidates (outro/section-boundary-based for
    outgoing, intro_end/section-boundary-based for incoming) on top of the
    regular bar-length candidates, and contribute scoring bonuses/
    penalties (see _score_beat_candidate) -- never a replacement for them,
    and never a forced transition point. With neither given, the result is
    byte-for-byte identical to calling this without the keyword arguments
    at all (roadmap: "structure가 없으면 기존 planner 결과 유지").
    """
    if outgoing.bpm is None or incoming.bpm is None:
        return _fallback_candidates(
            outgoing, incoming, settings, reasons=("- BPM is unknown for one or both tracks",),
        )
    if not compatibility.compatible:
        return _fallback_candidates(outgoing, incoming, settings, reasons=compatibility.reasons)

    outgoing_quality = outgoing.beat_alignment_quality()
    incoming_quality = incoming.beat_alignment_quality()
    if outgoing_quality == "insufficient" or incoming_quality == "insufficient":
        return _fallback_candidates(
            outgoing, incoming, settings,
            reasons=("- beat confidence is too low to align a transition",),
        )

    if outgoing_quality == "reliable" and incoming_quality == "reliable":
        strategy = TransitionStrategy.BEAT_MATCH
    else:
        strategy = TransitionStrategy.BEAT_ALIGNED_CROSSFADE

    candidates = [
        candidate
        for bars in BAR_LENGTHS
        if (candidate := _beat_based_candidate(
            outgoing, incoming, compatibility, bars, strategy, settings,
            outgoing_structure=outgoing_structure, incoming_structure=incoming_structure,
        )) is not None
    ]

    outgoing_anchor = _structure_outgoing_anchor(outgoing_structure)
    incoming_anchor = _structure_incoming_anchor(incoming_structure)
    if outgoing_anchor is not None or incoming_anchor is not None:
        # Additional candidates anchored at the structure hint(s) instead of
        # the default tail/head positions -- still snapped to the nearest
        # real beat/downbeat and scored like any other candidate, so a bad
        # anchor simply loses to a better-scoring regular candidate rather
        # than being trusted outright.
        candidates.extend(
            candidate
            for bars in BAR_LENGTHS
            if (candidate := _beat_based_candidate(
                outgoing, incoming, compatibility, bars, strategy, settings,
                outgoing_naive_override=outgoing_anchor, incoming_naive_override=incoming_anchor,
                outgoing_structure=outgoing_structure, incoming_structure=incoming_structure,
            )) is not None
        )

    if not candidates:
        return _fallback_candidates(
            outgoing, incoming, settings,
            reasons=("- no bar length fit within the transition-length and track-duration limits",),
        )
    return candidates


def _structure_outgoing_anchor(structure: TrackStructureAnalysis | None) -> float | None:
    """A candidate cue position near the end of the track, from structure
    data: outro_start if known, else the start of the last section, else
    None -- a hint only, always subject to downbeat/beat snapping and
    ordinary scoring like any other candidate (never a forced cut point)."""
    if structure is None:
        return None
    if structure.outro_start_seconds is not None:
        return structure.outro_start_seconds
    if structure.sections:
        return structure.sections[-1].start_seconds
    return None


def _structure_incoming_anchor(structure: TrackStructureAnalysis | None) -> float | None:
    """A candidate cue position near the start of the track, from structure
    data: intro_end if known, else the end of the first section, else
    None -- same "hint only" discipline as _structure_outgoing_anchor."""
    if structure is None:
        return None
    if structure.intro_end_seconds is not None:
        return structure.intro_end_seconds
    if structure.sections:
        return structure.sections[0].end_seconds
    return None


def _beat_based_candidate(
    outgoing: TrackAnalysis, incoming: TrackAnalysis, compatibility: TransitionCompatibility,
    bars: int, strategy: TransitionStrategy, settings: AutoMixTransitionSettings,
    *,
    outgoing_naive_override: float | None = None,
    incoming_naive_override: float | None = None,
    outgoing_structure: TrackStructureAnalysis | None = None,
    incoming_structure: TrackStructureAnalysis | None = None,
) -> TransitionCandidate | None:
    assert outgoing.bpm is not None and incoming.bpm is not None and compatibility.incoming_effective_bpm is not None

    if strategy is TransitionStrategy.BEAT_MATCH:
        target_bpm = resolve_target_bpm(outgoing)
        outgoing_rate = target_bpm / outgoing.bpm
        incoming_rate = target_bpm / compatibility.incoming_effective_bpm
        seconds_per_bar = (outgoing.meter_numerator or DEFAULT_METER_NUMERATOR) * 60.0 / target_bpm
    else:
        target_bpm = None
        outgoing_rate = 1.0
        incoming_rate = 1.0
        seconds_per_bar = (outgoing.meter_numerator or DEFAULT_METER_NUMERATOR) * 60.0 / outgoing.bpm

    duration_seconds = bars * seconds_per_bar
    if not (settings.min_transition_seconds <= duration_seconds <= settings.max_transition_seconds):
        return None
    if duration_seconds > outgoing.duration_seconds or duration_seconds > incoming.duration_seconds:
        return None

    naive_outgoing_time = (
        outgoing_naive_override if outgoing_naive_override is not None
        else max(0.0, outgoing.duration_seconds - duration_seconds)
    )
    naive_outgoing_time = max(0.0, min(naive_outgoing_time, outgoing.duration_seconds))
    outgoing_anchors = outgoing.downbeats if strategy is TransitionStrategy.BEAT_MATCH else outgoing.beats
    outgoing_source_time, outgoing_snap = _nearest_anchor(outgoing_anchors, naive_outgoing_time)

    naive_incoming_time = incoming_naive_override if incoming_naive_override is not None else 0.0
    naive_incoming_time = max(0.0, min(naive_incoming_time, incoming.duration_seconds))
    incoming_anchors = incoming.downbeats if strategy is TransitionStrategy.BEAT_MATCH else incoming.beats
    incoming_source_time, incoming_snap = _nearest_anchor(incoming_anchors, naive_incoming_time)
    if incoming.duration_seconds - incoming_source_time < duration_seconds:
        return None

    confidence = min(outgoing.bpm_confidence, incoming.bpm_confidence)
    score, reasons = _score_beat_candidate(
        outgoing, incoming, compatibility, bars, strategy, settings, outgoing_snap, incoming_snap,
        outgoing_source_time, incoming_source_time, duration_seconds,
        outgoing_structure=outgoing_structure, incoming_structure=incoming_structure,
    )
    return TransitionCandidate(
        from_track_id=outgoing.track_id, to_track_id=incoming.track_id,
        outgoing_source_time=outgoing_source_time, incoming_source_time=incoming_source_time,
        bars=bars, duration_seconds=duration_seconds,
        outgoing_bpm=outgoing.bpm, incoming_bpm=incoming.bpm, target_bpm=target_bpm,
        outgoing_rate=outgoing_rate, incoming_rate=incoming_rate,
        score=score, confidence=confidence, strategy=strategy, reasons=reasons,
    )


def _nearest_anchor(anchors: tuple[float, ...], naive_time: float) -> tuple[float, float]:
    """The closest beat/downbeat to ``naive_time``, and how far it was (seconds)."""
    if not anchors:
        return naive_time, 0.0
    nearest = min(anchors, key=lambda anchor: abs(anchor - naive_time))
    return nearest, abs(nearest - naive_time)


def _score_beat_candidate(
    outgoing: TrackAnalysis, incoming: TrackAnalysis, compatibility: TransitionCompatibility,
    bars: int, strategy: TransitionStrategy, settings: AutoMixTransitionSettings,
    outgoing_snap: float, incoming_snap: float,
    outgoing_source_time: float, incoming_source_time: float, duration_seconds: float,
    *,
    outgoing_structure: TrackStructureAnalysis | None = None,
    incoming_structure: TrackStructureAnalysis | None = None,
) -> tuple[float, tuple[str, ...]]:
    reasons: list[str] = list(compatibility.reasons)
    score = 0.0

    confidence_component = (outgoing.bpm_confidence + incoming.bpm_confidence) / 2.0
    score += WEIGHT_CONFIDENCE * confidence_component
    reasons.append(
        f"{'+' if confidence_component >= 0.5 else '-'} average beat confidence {confidence_component:.2f}"
    )

    if strategy is TransitionStrategy.BEAT_MATCH:
        meter_component = (outgoing.meter_confidence + incoming.meter_confidence) / 2.0
        score += WEIGHT_METER * meter_component
        reasons.append(
            f"{'+' if meter_component >= 0.5 else '-'} downbeat confidence {meter_component:.2f}"
        )

    tempo_budget = max(0.01, settings.max_tempo_change_percent)
    tempo_component = max(0.0, 1.0 - compatibility.tempo_shift_percent / tempo_budget)
    score += WEIGHT_TEMPO * tempo_component

    length_component = 1.0 if bars == settings.preferred_bars else 0.7
    score += WEIGHT_LENGTH * length_component
    reasons.append(f"+ {bars} bars available")

    snap_total = outgoing_snap + incoming_snap
    proximity_component = max(0.0, 1.0 - snap_total / 4.0)
    score += WEIGHT_PROXIMITY * proximity_component
    anchor_name = "downbeat" if strategy is TransitionStrategy.BEAT_MATCH else "beat"
    if incoming_snap > 1.0:
        reasons.append(f"- incoming cue is {incoming_snap:.1f}s from the nearest {anchor_name}")
    else:
        reasons.append(f"+ incoming cue close to the nearest {anchor_name}")

    if outgoing.key is not None and incoming.key is not None:
        if camelot_compatible(outgoing.key, incoming.key):
            score += WEIGHT_HARMONIC_BONUS
            reasons.append("+ compatible key")
        # No penalty for an incompatible key: key is a bonus modifier only,
        # never a blocker (roadmap Phase 7 section 3).

    if outgoing.energy is not None and incoming.energy is not None:
        energy_similarity = max(0.0, 1.0 - abs(outgoing.energy - incoming.energy))
        score += WEIGHT_ENERGY_CONTINUITY * energy_similarity
        if energy_similarity >= 0.8:
            reasons.append("+ similar energy level")

    if outgoing.vocal_activity and incoming.vocal_activity:
        # Both windows are the *actual* transition span now (previously the
        # outgoing side checked all the way to the track's own end, a wider
        # window than the real overlap whenever a structure/other anchor
        # placed the cue well before the natural tail).
        outgoing_tail_has_vocals = _has_activity_in_range(
            outgoing.vocal_activity, outgoing_source_time, outgoing_source_time + duration_seconds,
        )
        incoming_head_has_vocals = _has_activity_in_range(
            incoming.vocal_activity, incoming_source_time, incoming_source_time + duration_seconds,
        )
        if outgoing_tail_has_vocals and incoming_head_has_vocals:
            score -= WEIGHT_VOCAL_OVERLAP_PENALTY
            reasons.append("- vocal overlap likely during the transition")

    # -- Commit C: structure-aware bonuses/penalties, additive on top of the
    # above and each independently no-op without the relevant data (roadmap:
    # "structure가 없으면 기존 planner 결과 유지", "one-sided structure도
    # 안전하게 사용").
    outgoing_anchor = _structure_outgoing_anchor(outgoing_structure)
    if outgoing_anchor is not None and abs(outgoing_source_time - outgoing_anchor) <= STRUCTURE_ANCHOR_PROXIMITY_TOLERANCE_SECONDS:
        score += WEIGHT_STRUCTURE_ANCHOR_BONUS
        reasons.append("+ outgoing cue aligns with a structure anchor (outro/late section)")

    incoming_anchor = _structure_incoming_anchor(incoming_structure)
    if incoming_anchor is not None and abs(incoming_source_time - incoming_anchor) <= STRUCTURE_ANCHOR_PROXIMITY_TOLERANCE_SECONDS:
        score += WEIGHT_STRUCTURE_ANCHOR_BONUS
        reasons.append("+ incoming cue aligns with a structure anchor (intro end/early section)")

    if outgoing_structure is not None and incoming_structure is not None:
        outgoing_local_energy = outgoing_structure.energy_at(outgoing_source_time)
        incoming_local_energy = incoming_structure.energy_at(incoming_source_time)
        if outgoing_local_energy is not None and incoming_local_energy is not None:
            # More precise than the global-scalar energy comparison above:
            # this compares energy at the actual cue points, not each
            # track's single overall figure.
            local_energy_similarity = max(0.0, 1.0 - abs(outgoing_local_energy - incoming_local_energy))
            score += WEIGHT_LOCAL_ENERGY_CONTINUITY * local_energy_similarity
            if local_energy_similarity >= 0.8:
                reasons.append("+ similar local energy at the cue points")
            else:
                reasons.append("- local energy jumps across the transition")

    if incoming_source_time > INCOMING_TRIM_SOFT_LIMIT_SECONDS:
        # Deliberately uncapped (unlike the other components above): a
        # moderate overshoot should cost a little, but an extreme one (an
        # implausible structure anchor, not a real intro) must be able to
        # outweigh even a simultaneous structure-anchor bonus and drive the
        # final clamped score toward 0 -- see
        # test_falls_back_to_the_regular_bar_candidate_when_the_anchor_is_implausible.
        trim_severity = (incoming_source_time - INCOMING_TRIM_SOFT_LIMIT_SECONDS) / INCOMING_TRIM_SOFT_LIMIT_SECONDS
        score -= WEIGHT_INCOMING_TRIM_PENALTY * trim_severity
        reasons.append(f"- incoming cue trims {incoming_source_time:.1f}s off the start of the track")

    outgoing_tail_unused = outgoing.duration_seconds - outgoing_source_time
    if outgoing_tail_unused > MAXIMUM_OUTGOING_TAIL_TRIM_SECONDS:
        trim_severity = (outgoing_tail_unused - MAXIMUM_OUTGOING_TAIL_TRIM_SECONDS) / MAXIMUM_OUTGOING_TAIL_TRIM_SECONDS
        score -= WEIGHT_OUTGOING_TAIL_TRIM_PENALTY * trim_severity
        reasons.append(f"- outgoing cue starts {outgoing_tail_unused:.1f}s before the track's own end")

    return max(0.0, min(1.0, score)), tuple(reasons)


def _has_activity_in_range(spans: tuple[tuple[float, float], ...], start: float, end: float) -> bool:
    return any(span_start < end and span_end > start for span_start, span_end in spans)


def _fallback_candidates(
    outgoing: TrackAnalysis, incoming: TrackAnalysis, settings: AutoMixTransitionSettings,
    reasons: tuple[str, ...],
) -> list[TransitionCandidate]:
    """FIXED_CROSSFADE if both tracks have room for one, otherwise CUT."""
    available = min(outgoing.duration_seconds, incoming.duration_seconds)
    duration_seconds = min(settings.fallback_crossfade_seconds, available)
    if duration_seconds < settings.min_transition_seconds:
        return [TransitionCandidate(
            from_track_id=outgoing.track_id, to_track_id=incoming.track_id,
            outgoing_source_time=outgoing.duration_seconds, incoming_source_time=0.0,
            bars=0, duration_seconds=0.0,
            outgoing_bpm=outgoing.bpm, incoming_bpm=incoming.bpm, target_bpm=None,
            outgoing_rate=1.0, incoming_rate=1.0,
            score=0.1, confidence=0.0, strategy=TransitionStrategy.CUT,
            reasons=reasons + ("- not enough source duration for even a fixed crossfade",),
        )]
    return [TransitionCandidate(
        from_track_id=outgoing.track_id, to_track_id=incoming.track_id,
        outgoing_source_time=max(0.0, outgoing.duration_seconds - duration_seconds),
        incoming_source_time=0.0,
        bars=0, duration_seconds=duration_seconds,
        outgoing_bpm=outgoing.bpm, incoming_bpm=incoming.bpm, target_bpm=None,
        outgoing_rate=1.0, incoming_rate=1.0,
        score=0.3, confidence=0.0, strategy=TransitionStrategy.FIXED_CROSSFADE,
        reasons=reasons + (f"+ {duration_seconds:.1f}s fixed crossfade fits both tracks",),
    )]
