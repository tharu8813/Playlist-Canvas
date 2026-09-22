"""Pick how an AutoMix transition's overlap is mixed (DSP Phase 2).

The planner calls this once per transition, after the candidate (and so the
exact window) is fixed, and stores the result in
``AudioRenderTransition.dsp``/``dsp_reasons``. The renderer only executes
that choice; it never re-reads analysis, so Preview and Export always hear
the same style. Pure and deterministic: same inputs, same decision.

Missing data is never evidence: an unknown key, energy, or vocal activity
simply cannot trigger its rule, so sparse analysis degrades to the plain
BASS_SWAP (BEAT_MATCH) or legacy qsin (BEAT_ALIGNED_CROSSFADE) mix.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.automix.analysis.key import camelot_compatible, key_to_camelot
from app.automix.candidates import TransitionCandidate, TransitionStrategy, _has_activity_in_range
from app.automix.compatibility import TransitionCompatibility
from app.automix.models import TrackAnalysis
from app.automix.structure.models import TrackStructureAnalysis
from app.timeline.render_plan import AudioRenderTransition, TransitionDsp

SHORT_FADE_MAX_SECONDS = 4.0
"""Below this a band handoff squeezes into ~1 s; a plain equal-power fade is
cleaner and skips the crossover."""
ENERGY_JUMP_THRESHOLD = 0.3
"""Local (else global) energy difference, on the analyzers' 0..1 scale, that
counts as a mismatch -- candidates.py already calls < 0.8 similarity a jump."""
MAX_BEAT_DRIFT_SECONDS = 0.05
"""A non-rate-matched crossfade whose kicks drift apart by more than this
(~1/10 beat at 120 BPM) over the window would flam if the lows overlapped."""


@dataclass(frozen=True, slots=True)
class TransitionDspDecision:
    """The chosen style (``None``: the type's legacy mix) and why.

    ``reasons[0]`` (prefixed ``*``) is the rule that decided; the rest are
    every fact the selector looked at: ``+`` favourable, ``-`` a conflict,
    ``?`` unknown (and therefore ignored).
    """

    dsp: TransitionDsp | None
    reasons: tuple[str, ...]


def select_transition_dsp(
    candidate: TransitionCandidate,
    compatibility: TransitionCompatibility,
    outgoing: TrackAnalysis,
    incoming: TrackAnalysis,
    outgoing_structure: TrackStructureAnalysis | None = None,
    incoming_structure: TrackStructureAnalysis | None = None,
) -> TransitionDspDecision:
    """Decide the DSP style for ``candidate``'s window. Rules, first match wins:

    1. FIXED_CROSSFADE/CUT (no usable rhythm analysis) -> ``None``: legacy tri.
    2. window shorter than SHORT_FADE_MAX_SECONDS -> SHORT_FADE.
    3. vocals on both sides of the window, or known clashing keys -> VOCAL_SAFE_EQ.
    4. energy jump, or (not rate-matched) kick drift across the window -> FILTER_BLEND.
    5. BEAT_MATCH -> BASS_SWAP; BEAT_ALIGNED_CROSSFADE -> ``None`` (legacy qsin).
    """
    strategy = candidate.strategy
    duration = candidate.duration_seconds
    facts = [f"+ {strategy.value} ({'rate-matched' if strategy is TransitionStrategy.BEAT_MATCH else 'own tempo'})"]
    if strategy not in (TransitionStrategy.BEAT_MATCH, TransitionStrategy.BEAT_ALIGNED_CROSSFADE):
        return TransitionDspDecision(None, (f"* legacy crossfade: {strategy.value} has no reliable rhythm to style on",))

    vocals = _vocals_overlap(candidate, outgoing, incoming)
    facts.append({True: "- vocals active in both transition windows",
                  False: "+ vocals not active in both transition windows",
                  None: "? vocal activity unknown"}[vocals])
    keys = _keys_clash(outgoing, incoming)
    facts.append({True: f"- keys clash ({outgoing.key} -> {incoming.key})",
                  False: f"+ keys compatible ({outgoing.key} -> {incoming.key})",
                  None: "? key unknown"}[keys])
    energy, energy_source = _energy_jump(candidate, outgoing, incoming, outgoing_structure, incoming_structure)
    if energy is None:
        facts.append("? energy unknown")
    else:
        facts.append(f"{'-' if energy >= ENERGY_JUMP_THRESHOLD else '+'} {energy_source} energy delta {energy:.2f}")
    facts.append(f"  tempo delta {compatibility.tempo_shift_percent:.1f}%")
    drift = 0.0
    if strategy is TransitionStrategy.BEAT_ALIGNED_CROSSFADE:
        drift = duration * compatibility.tempo_shift_percent / 100.0
        facts.append(f"{'-' if drift > MAX_BEAT_DRIFT_SECONDS else '+'} expected kick drift {drift * 1000:.0f}ms")

    if duration < SHORT_FADE_MAX_SECONDS:
        dsp, rule = TransitionDsp.SHORT_FADE, f"transition only {duration:.1f}s (< {SHORT_FADE_MAX_SECONDS:.1f}s)"
    elif vocals or keys:
        dsp, rule = TransitionDsp.VOCAL_SAFE_EQ, "vocals overlap" if vocals else "keys clash"
    elif energy is not None and energy >= ENERGY_JUMP_THRESHOLD:
        dsp, rule = TransitionDsp.FILTER_BLEND, f"{energy_source} energy delta {energy:.2f} (>= {ENERGY_JUMP_THRESHOLD})"
    elif drift > MAX_BEAT_DRIFT_SECONDS:
        dsp, rule = TransitionDsp.FILTER_BLEND, f"kicks would drift {drift * 1000:.0f}ms"
    elif strategy is TransitionStrategy.BEAT_MATCH:
        dsp, rule = TransitionDsp.BASS_SWAP, "clean reliable beat match"
    else:
        dsp, rule = None, "aligned crossfade with no conflicts: legacy equal-power"
    return TransitionDspDecision(dsp, (f"* {dsp.value if dsp else 'legacy'}: {rule}", *facts))


def describe_transition(transition: AudioRenderTransition, outgoing_name: str, incoming_name: str) -> str:
    """One diagnostic line for a planned transition (logs, real-music tuning)."""
    return (
        f"{outgoing_name} -> {incoming_name} time={transition.timeline_start:.1f}s "
        f"duration={transition.duration:.1f}s type={transition.type.value} "
        f"dsp={transition.dsp.value if transition.dsp else 'legacy'} "
        f"reasons=[{'; '.join(reason.strip() for reason in transition.dsp_reasons)}]"
    )


def _vocals_overlap(candidate: TransitionCandidate, outgoing: TrackAnalysis, incoming: TrackAnalysis) -> bool | None:
    if not outgoing.vocal_activity or not incoming.vocal_activity:
        return None  # "no spans" cannot be told apart from "not analyzed"
    # Source-space windows, exactly the audio each side plays in the overlap.
    incoming_end = candidate.incoming_source_time + candidate.duration_seconds * candidate.incoming_rate
    return (
        _has_activity_in_range(outgoing.vocal_activity, candidate.outgoing_source_time, candidate.outgoing_source_out)
        and _has_activity_in_range(incoming.vocal_activity, candidate.incoming_source_time, incoming_end)
    )


def _keys_clash(outgoing: TrackAnalysis, incoming: TrackAnalysis) -> bool | None:
    if outgoing.key is None or incoming.key is None:
        return None
    if key_to_camelot(outgoing.key) is None or key_to_camelot(incoming.key) is None:
        return None  # unparseable is unknown, not incompatible
    return not camelot_compatible(outgoing.key, incoming.key)


def _energy_jump(
    candidate: TransitionCandidate, outgoing: TrackAnalysis, incoming: TrackAnalysis,
    outgoing_structure: TrackStructureAnalysis | None, incoming_structure: TrackStructureAnalysis | None,
) -> tuple[float | None, str]:
    if outgoing_structure is not None and incoming_structure is not None:
        local_out = outgoing_structure.energy_at(candidate.outgoing_source_time)
        local_in = incoming_structure.energy_at(candidate.incoming_source_time)
        if local_out is not None and local_in is not None:
            return abs(local_out - local_in), "local"
    if outgoing.energy is not None and incoming.energy is not None:
        return abs(outgoing.energy - incoming.energy), "global"
    return None, ""
