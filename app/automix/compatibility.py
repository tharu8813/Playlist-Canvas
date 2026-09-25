"""BPM compatibility between two tracks, independent of candidate placement.

Pure data/functions only (roadmap Phase 3 section 12): no Qt, no FFmpeg, no
project mutation. This answers "how much would these two tempos need to
shift to align" -- app/automix/candidates.py answers "where, and is it
worth doing" using this result.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.automix.models import TrackAnalysis
from app.automix.settings import AutoMixTransitionSettings


@dataclass(frozen=True, slots=True)
class TransitionCompatibility:
    """How well two tracks' tempos align, and at what cost.

    ``incoming_effective_bpm`` is ``incoming_bpm`` after whichever
    half/double octave fold produced the smallest required shift -- see
    evaluate_compatibility()'s docstring. It is the number a planner
    should actually divide into a target tempo for the incoming track's
    playback rate, not ``incoming_bpm`` itself.
    """

    from_track_id: str
    to_track_id: str
    compatible: bool
    tempo_shift_percent: float
    used_half_double: bool
    outgoing_bpm: float | None
    incoming_bpm: float | None
    incoming_effective_bpm: float | None
    reasons: tuple[str, ...]


def evaluate_compatibility(
    outgoing: TrackAnalysis, incoming: TrackAnalysis, settings: AutoMixTransitionSettings,
) -> TransitionCompatibility:
    """Compare ``outgoing``'s and ``incoming``'s tempos.

    Tempo-shift percent is computed against whichever of incoming_bpm,
    incoming_bpm*2, or incoming_bpm/2 (when ``allow_half_double_tempo``)
    is closest to outgoing_bpm -- half/double-tempo tracks (roadmap
    example: 90 vs. 170) are compared as if they were the same tempo class
    rather than penalized for a raw, meaningless percentage gap between
    mismatched octaves.
    """
    if outgoing.bpm is None or incoming.bpm is None:
        return TransitionCompatibility(
            from_track_id=outgoing.track_id, to_track_id=incoming.track_id,
            compatible=False, tempo_shift_percent=0.0, used_half_double=False,
            outgoing_bpm=outgoing.bpm, incoming_bpm=incoming.bpm, incoming_effective_bpm=None,
            reasons=("BPM is unknown for one or both tracks",),
        )

    octave_candidates: list[tuple[float, bool]] = [(incoming.bpm, False)]
    if settings.allow_half_double_tempo:
        octave_candidates.append((incoming.bpm * 2.0, True))
        octave_candidates.append((incoming.bpm / 2.0, True))

    best_shift_percent = float("inf")
    best_effective_bpm = incoming.bpm
    best_used_half_double = False
    for candidate_bpm, used_half_double in octave_candidates:
        shift_percent = abs(candidate_bpm - outgoing.bpm) / outgoing.bpm * 100.0
        if shift_percent < best_shift_percent:
            best_shift_percent = shift_percent
            best_effective_bpm = candidate_bpm
            best_used_half_double = used_half_double

    compatible = best_shift_percent <= settings.max_tempo_change_percent
    reasons: list[str] = []
    if best_used_half_double:
        reasons.append(
            f"+ compared using half/double tempo ({incoming.bpm:.1f} -> {best_effective_bpm:.1f} BPM)"
        )
    if compatible:
        reasons.append(f"+ tempo delta only {best_shift_percent:.1f}%")
    else:
        reasons.append(
            f"- tempo delta {best_shift_percent:.1f}% exceeds the "
            f"{settings.max_tempo_change_percent:.1f}% limit"
        )
    return TransitionCompatibility(
        from_track_id=outgoing.track_id, to_track_id=incoming.track_id,
        compatible=compatible, tempo_shift_percent=best_shift_percent,
        used_half_double=best_used_half_double,
        outgoing_bpm=outgoing.bpm, incoming_bpm=incoming.bpm,
        incoming_effective_bpm=best_effective_bpm,
        reasons=tuple(reasons),
    )


