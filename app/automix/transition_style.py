"""Pick how an AutoMix transition's overlap is mixed (DSP Phase 2).

The planner calls this once per transition, after the candidate (and so the
exact window) is fixed, and stores the result in
``AudioRenderTransition.dsp``. The renderer only executes that choice; it
never re-reads analysis, so Preview and Export always hear the same style.
Pure and deterministic: same inputs, same style, no randomness.
"""

from __future__ import annotations

from app.automix.analysis.key import camelot_compatible, key_to_camelot
from app.automix.candidates import TransitionCandidate, TransitionStrategy, _has_activity_in_range
from app.automix.compatibility import TransitionCompatibility
from app.automix.models import TrackAnalysis
from app.automix.structure.models import TrackStructureAnalysis
from app.timeline.render_plan import TransitionDsp

SHORT_FADE_MAX_SECONDS = 4.0
"""Below this a band handoff squeezes into ~1 s; a plain equal-power fade is
cleaner and skips the crossover."""
ENERGY_JUMP_THRESHOLD = 0.3
"""Local (else global) energy difference, on the analyzers' 0..1 scale, that
counts as a mismatch -- candidates.py already calls < 0.8 similarity a jump."""
MAX_BEAT_DRIFT_SECONDS = 0.05
"""A non-rate-matched crossfade whose kicks drift apart by more than this
(~1/10 beat at 120 BPM) over the window would flam if the lows overlapped."""


def select_transition_dsp(
    candidate: TransitionCandidate,
    compatibility: TransitionCompatibility,
    outgoing: TrackAnalysis,
    incoming: TrackAnalysis,
    outgoing_structure: TrackStructureAnalysis | None = None,
    incoming_structure: TrackStructureAnalysis | None = None,
) -> TransitionDsp | None:
    """The DSP style for ``candidate``'s window, or ``None`` to keep the type's legacy mix.

    Rules, first match wins:

    1. FIXED_CROSSFADE/CUT (no usable rhythm analysis) -> ``None``: legacy tri.
    2. window shorter than SHORT_FADE_MAX_SECONDS -> SHORT_FADE.
    3. vocals on both sides of the window, or known clashing keys -> VOCAL_SAFE_EQ.
    4. local energy jump, or (not rate-matched) kick drift across the window -> FILTER_BLEND.
    5. BEAT_MATCH -> BASS_SWAP; BEAT_ALIGNED_CROSSFADE -> ``None`` (legacy qsin).
    """
    if candidate.strategy not in (TransitionStrategy.BEAT_MATCH, TransitionStrategy.BEAT_ALIGNED_CROSSFADE):
        return None
    if candidate.duration_seconds < SHORT_FADE_MAX_SECONDS:
        return TransitionDsp.SHORT_FADE
    if _vocals_overlap(candidate, outgoing, incoming) or _keys_clash(outgoing, incoming):
        return TransitionDsp.VOCAL_SAFE_EQ
    if _energy_jump(candidate, outgoing, incoming, outgoing_structure, incoming_structure) >= ENERGY_JUMP_THRESHOLD:
        return TransitionDsp.FILTER_BLEND
    if candidate.strategy is TransitionStrategy.BEAT_MATCH:
        return TransitionDsp.BASS_SWAP
    # BEAT_ALIGNED_CROSSFADE plays the incoming track at its own tempo.
    drift = candidate.duration_seconds * compatibility.tempo_shift_percent / 100.0
    return TransitionDsp.FILTER_BLEND if drift > MAX_BEAT_DRIFT_SECONDS else None


def _vocals_overlap(candidate: TransitionCandidate, outgoing: TrackAnalysis, incoming: TrackAnalysis) -> bool:
    # Source-space windows, exactly the audio each side plays in the overlap.
    incoming_end = candidate.incoming_source_time + candidate.duration_seconds * candidate.incoming_rate
    return (
        _has_activity_in_range(outgoing.vocal_activity, candidate.outgoing_source_time, candidate.outgoing_source_out)
        and _has_activity_in_range(incoming.vocal_activity, candidate.incoming_source_time, incoming_end)
    )


def _keys_clash(outgoing: TrackAnalysis, incoming: TrackAnalysis) -> bool:
    if outgoing.key is None or incoming.key is None:
        return False
    if key_to_camelot(outgoing.key) is None or key_to_camelot(incoming.key) is None:
        return False  # unparseable is unknown, not incompatible
    return not camelot_compatible(outgoing.key, incoming.key)


def _energy_jump(
    candidate: TransitionCandidate, outgoing: TrackAnalysis, incoming: TrackAnalysis,
    outgoing_structure: TrackStructureAnalysis | None, incoming_structure: TrackStructureAnalysis | None,
) -> float:
    if outgoing_structure is not None and incoming_structure is not None:
        local_out = outgoing_structure.energy_at(candidate.outgoing_source_time)
        local_in = incoming_structure.energy_at(candidate.incoming_source_time)
        if local_out is not None and local_in is not None:
            return abs(local_out - local_in)
    if outgoing.energy is not None and incoming.energy is not None:
        return abs(outgoing.energy - incoming.energy)
    return 0.0
