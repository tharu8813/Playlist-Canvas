"""Transition candidate generation and explainable scoring.

Pure data/functions only (roadmap Phase 3 section 12): no Qt, no FFmpeg
process, no project mutation, and no CompiledRenderPlan yet -- Phase 4
consumes this module's output to build one. Given identical
TrackAnalysis/settings input, output order and scores are always the
same: bar lengths are tried in one fixed order and nothing here uses
randomness (roadmap section 10, "Determinism").

Scoring weights (must sum to 1.0), for a later developer tuning them:

    Factor                                          Weight   Effect
    ---------------------------------------------------------------
    Average BPM confidence                          0.35     +
    Downbeat/meter confidence (BEAT_MATCH only)      0.15     +
    Tempo shift vs. the allowed budget               0.25     -
    Requested bar length actually used                0.15     +
    Cue proximity to the ideal anchor (beat/downbeat) 0.10     +
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.automix.compatibility import TransitionCompatibility, resolve_target_bpm
from app.automix.models import TrackAnalysis
from app.automix.settings import AutoMixTransitionSettings

BAR_LENGTHS = (4, 8, 16)
DEFAULT_METER_NUMERATOR = 4

WEIGHT_CONFIDENCE = 0.35
WEIGHT_METER = 0.15
WEIGHT_TEMPO = 0.25
WEIGHT_LENGTH = 0.15
WEIGHT_PROXIMITY = 0.10


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
) -> list[TransitionCandidate]:
    """Generate every transition candidate worth considering for this pair.

    Degrades through, from best to worst (roadmap section 9):
    BEAT_MATCH (both tracks' beat_alignment_quality is "reliable") ->
    BEAT_ALIGNED_CROSSFADE (BPM usable but beat/bar confidence is not) ->
    FIXED_CROSSFADE (incompatible tempo or missing BPM, but both tracks
    have enough source duration for a plain timed crossfade) -> CUT (not
    enough room for even a fixed crossfade).
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
        )) is not None
    ]
    if not candidates:
        return _fallback_candidates(
            outgoing, incoming, settings,
            reasons=("- no bar length fit within the transition-length and track-duration limits",),
        )
    return candidates


def _beat_based_candidate(
    outgoing: TrackAnalysis, incoming: TrackAnalysis, compatibility: TransitionCompatibility,
    bars: int, strategy: TransitionStrategy, settings: AutoMixTransitionSettings,
) -> TransitionCandidate | None:
    assert outgoing.bpm is not None and incoming.bpm is not None and compatibility.incoming_effective_bpm is not None

    if strategy is TransitionStrategy.BEAT_MATCH:
        target_bpm = resolve_target_bpm(
            outgoing, compatibility.incoming_effective_bpm, outgoing.bpm_confidence, incoming.bpm_confidence,
        )
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

    naive_outgoing_time = max(0.0, outgoing.duration_seconds - duration_seconds)
    outgoing_anchors = outgoing.downbeats if strategy is TransitionStrategy.BEAT_MATCH else outgoing.beats
    outgoing_source_time, outgoing_snap = _nearest_anchor(outgoing_anchors, naive_outgoing_time)

    incoming_anchors = incoming.downbeats if strategy is TransitionStrategy.BEAT_MATCH else incoming.beats
    incoming_source_time, incoming_snap = _nearest_anchor(incoming_anchors, 0.0)
    if incoming.duration_seconds - incoming_source_time < duration_seconds:
        return None

    confidence = min(outgoing.bpm_confidence, incoming.bpm_confidence)
    score, reasons = _score_beat_candidate(
        outgoing, incoming, compatibility, bars, strategy, settings, outgoing_snap, incoming_snap,
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

    return min(1.0, score), tuple(reasons)


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
