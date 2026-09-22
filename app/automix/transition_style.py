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
from app.automix.candidates import TransitionCandidate, TransitionStrategy
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
VOCAL_SEGMENTS = 8
VOCAL_CONFLICT_MIN_RATIO = 1 / VOCAL_SEGMENTS
"""Both tracks singing together for at least an eighth of the window is a clash."""
VOCAL_HANDOFF_CHOICES = (0.525, 0.375, 0.625, 0.25, 0.75)
"""Mid-band handoff centers, preferred first; 0.525 is VOCAL_SAFE_EQ's default
(0.40-0.65) window. Each choice keeps the 0.25-wide swap inside the window."""


@dataclass(frozen=True, slots=True)
class TransitionDspDecision:
    """The chosen style (``None``: the type's legacy mix) and why.

    ``reasons[0]`` (prefixed ``*``) is the rule that decided; the rest are
    every fact the selector looked at: ``+`` favourable, ``-`` a conflict,
    ``?`` unknown (and therefore ignored).
    """

    dsp: TransitionDsp | None
    reasons: tuple[str, ...]
    metrics: tuple[tuple[str, object], ...] = ()
    """The same facts as ``reasons``, as (name, value) pairs for diagnostics;
    ``None`` values mean unknown."""
    vocal_handoff: float | None = None
    """VOCAL_SAFE_EQ only: window progress of the mid-band handoff (see VocalMap.handoff)."""


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
    3. both tracks singing at the same time for at least 1/8 of the window
       (see VocalMap), or known clashing keys -> VOCAL_SAFE_EQ. Vocals that
       hand over without meeting (the outgoing line ends early, the incoming
       one starts late) are not a clash.
    4. energy jump, or (not rate-matched) kick drift across the window -> FILTER_BLEND.
    5. BEAT_MATCH -> BASS_SWAP; BEAT_ALIGNED_CROSSFADE -> ``None`` (legacy qsin).
    """
    strategy = candidate.strategy
    duration = candidate.duration_seconds
    facts = [f"+ {strategy.value} ({'rate-matched' if strategy is TransitionStrategy.BEAT_MATCH else 'own tempo'})"]
    vocals = vocal_map(candidate, outgoing, incoming)
    keys = _keys_clash(outgoing, incoming)
    energy, energy_source = _energy_jump(candidate, outgoing, incoming, outgoing_structure, incoming_structure)
    drift = 0.0
    if strategy is TransitionStrategy.BEAT_ALIGNED_CROSSFADE:
        drift = duration * compatibility.tempo_shift_percent / 100.0
    conflict = vocals is not None and vocals.overlap_ratio >= VOCAL_CONFLICT_MIN_RATIO
    handoff = vocals.handoff() if conflict else None
    metrics = (
        ("vocal_overlap", vocals.overlap_ratio if vocals is not None else None),
        ("vocal_handoff", handoff), ("key_clash", keys),
        ("energy_delta", energy), ("energy_source", energy_source or None),
        ("kick_drift_ms", drift * 1000.0 if strategy is TransitionStrategy.BEAT_ALIGNED_CROSSFADE else None),
    )
    if strategy not in (TransitionStrategy.BEAT_MATCH, TransitionStrategy.BEAT_ALIGNED_CROSSFADE):
        return TransitionDspDecision(
            None, (f"* legacy crossfade: {strategy.value} has no reliable rhythm to style on",), metrics,
        )

    facts.append(_vocal_fact(vocals, handoff))
    facts.append({True: f"- keys clash ({outgoing.key} -> {incoming.key})",
                  False: f"+ keys compatible ({outgoing.key} -> {incoming.key})",
                  None: "? key unknown"}[keys])
    if energy is None:
        facts.append("? energy unknown")
    else:
        facts.append(f"{'-' if energy >= ENERGY_JUMP_THRESHOLD else '+'} {energy_source} energy delta {energy:.2f}")
    facts.append(f"  tempo delta {compatibility.tempo_shift_percent:.1f}%")
    if strategy is TransitionStrategy.BEAT_ALIGNED_CROSSFADE:
        facts.append(f"{'-' if drift > MAX_BEAT_DRIFT_SECONDS else '+'} expected kick drift {drift * 1000:.0f}ms")

    if duration < SHORT_FADE_MAX_SECONDS:
        dsp, rule = TransitionDsp.SHORT_FADE, f"transition only {duration:.1f}s (< {SHORT_FADE_MAX_SECONDS:.1f}s)"
    elif conflict or keys:
        dsp, rule = TransitionDsp.VOCAL_SAFE_EQ, "vocals overlap" if conflict else "keys clash"
    elif energy is not None and energy >= ENERGY_JUMP_THRESHOLD:
        dsp, rule = TransitionDsp.FILTER_BLEND, f"{energy_source} energy delta {energy:.2f} (>= {ENERGY_JUMP_THRESHOLD})"
    elif drift > MAX_BEAT_DRIFT_SECONDS:
        dsp, rule = TransitionDsp.FILTER_BLEND, f"kicks would drift {drift * 1000:.0f}ms"
    elif strategy is TransitionStrategy.BEAT_MATCH:
        dsp, rule = TransitionDsp.BASS_SWAP, "clean reliable beat match"
    else:
        dsp, rule = None, "aligned crossfade with no conflicts: legacy equal-power"
    return TransitionDspDecision(
        dsp, (f"* {dsp.value if dsp else 'legacy'}: {rule}", *facts), metrics,
        vocal_handoff=handoff if dsp is TransitionDsp.VOCAL_SAFE_EQ else None,
    )


def describe_transition(transition: AudioRenderTransition, outgoing_name: str, incoming_name: str) -> str:
    """One diagnostic line for a planned transition (logs, real-music tuning)."""
    return (
        f"{outgoing_name} -> {incoming_name} time={transition.timeline_start:.1f}s "
        f"duration={transition.duration:.1f}s type={transition.type.value} "
        f"dsp={transition.dsp.value if transition.dsp else 'legacy'} "
        f"reasons=[{'; '.join(reason.strip() for reason in transition.dsp_reasons)}]"
    )


@dataclass(frozen=True, slots=True)
class VocalMap:
    """Vocal coverage (0..1) of each eighth of the transition window, per side."""

    outgoing: tuple[float, ...]
    incoming: tuple[float, ...]

    @property
    def overlap_ratio(self) -> float:
        """Share of the window in which both tracks sing at once."""
        return sum(min(o, i) for o, i in zip(self.outgoing, self.incoming)) / VOCAL_SEGMENTS

    def handoff(self) -> float | None:
        """Where the mid band (the voice) should change hands, as window progress.

        Before the handoff only the outgoing voice is heard, after it only the
        incoming one, so it goes where the least singing is cut off: late when
        the outgoing line runs long, early when the incoming one starts early.
        ``None`` keeps the style's default (0.40-0.65) when nothing beats it.
        """
        def lost(point: float) -> float:
            centers = [(index + 0.5) / VOCAL_SEGMENTS for index in range(VOCAL_SEGMENTS)]
            return (sum(i for c, i in zip(centers, self.incoming) if c < point)
                    + sum(o for c, o in zip(centers, self.outgoing) if c > point))

        best = min(VOCAL_HANDOFF_CHOICES, key=lost)  # first choice (the default) wins ties
        return None if best == VOCAL_HANDOFF_CHOICES[0] else best


def vocal_map(candidate: TransitionCandidate, outgoing: TrackAnalysis, incoming: TrackAnalysis) -> VocalMap | None:
    """Each side's vocal coverage across the window; ``None`` if either side is unknown."""
    if not outgoing.vocal_activity or not incoming.vocal_activity:
        return None  # "no spans" cannot be told apart from "not analyzed"
    # Source-space windows, exactly the audio each side plays in the overlap.
    return VocalMap(
        _coverage(outgoing.vocal_activity, candidate.outgoing_source_time, candidate.outgoing_source_out),
        _coverage(incoming.vocal_activity, candidate.incoming_source_time,
                  candidate.incoming_source_time + candidate.duration_seconds * candidate.incoming_rate),
    )


def _coverage(spans: tuple[tuple[float, float], ...], start: float, end: float) -> tuple[float, ...]:
    step = (end - start) / VOCAL_SEGMENTS
    if step <= 0.0:
        return (0.0,) * VOCAL_SEGMENTS
    return tuple(
        min(1.0, sum(max(0.0, min(b, span_end) - max(a, span_start)) for span_start, span_end in spans) / step)
        for a, b in ((start + k * step, start + (k + 1) * step) for k in range(VOCAL_SEGMENTS))
    )


def _vocal_fact(vocals: VocalMap | None, handoff: float | None) -> str:
    if vocals is None:
        return "? vocal activity unknown"
    if vocals.overlap_ratio >= VOCAL_CONFLICT_MIN_RATIO:
        where = f"hand off at {handoff:.0%}" if handoff is not None else "default handoff"
        return f"- vocals overlap for {vocals.overlap_ratio:.0%} of the window ({where})"
    if any(vocals.outgoing) and any(vocals.incoming):
        return "+ vocals hand over without singing together"
    return "+ vocals on at most one side of the window"


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
