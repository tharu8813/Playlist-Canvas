"""Compare cue, length and EQ handoff together using measured source audio.

This is a deterministic acoustic cost model, not a learned quality score.
The renderer executes the selected envelopes unchanged in preview and export.
"""
from __future__ import annotations

from dataclasses import replace
from functools import lru_cache

import numpy as np

from app.automix.analysis.key import shift_key
from app.automix.analysis.vocals import merge_spans
from app.automix.candidates import (
    TransitionCandidate, TransitionStrategy, audible_start, incoming_landmarks, key_shift, sung_spans,
)
from app.automix.compatibility import TransitionCompatibility
from app.automix.models import TrackAnalysis
from app.automix.music import character, music_tags_at
from app.automix.renderer import BAND_ENVELOPES
from app.automix.structure.models import TrackStructureAnalysis
from app.automix.transition_style import TransitionDspDecision, select_transition_dsp, vocal_map
from app.timeline.render_plan import TransitionDsp

SAMPLES = 48
_PROGRESS = (np.arange(SAMPLES) + 0.5) / SAMPLES


def choose_transition(
    candidates: list[TransitionCandidate], compatibility: TransitionCompatibility,
    outgoing: TrackAnalysis, incoming: TrackAnalysis,
    outgoing_structure: TrackStructureAnalysis | None, incoming_structure: TrackStructureAnalysis | None,
    *, previous_style: TransitionDsp | None = None,
) -> tuple[TransitionCandidate | None, TransitionDspDecision | None]:
    """Joint selection; without complete measurements keep the existing rules."""
    best = decision = None
    for candidate in candidates:
        heard = outgoing
        shift = key_shift(outgoing, incoming) if abs(candidate.outgoing_rate - 1.0) > 1e-9 else None
        if candidate.strategy is TransitionStrategy.BEAT_MATCH and shift:
            heard = replace(outgoing, key=shift_key(outgoing.key, shift))
        selected = select_transition_dsp(candidate, compatibility, heard, incoming,
                                         outgoing_structure, incoming_structure, previous_style=previous_style)
        candidate, selected = optimize_handoff(candidate, selected, heard, incoming, incoming_structure,
                                                 previous_style=previous_style)
        if best is None or candidate.score > best.score:
            best, decision = candidate, selected
    return best, decision


def _features(analysis: TrackAnalysis, times: np.ndarray) -> np.ndarray | None:
    curves = (analysis.rms_curve, analysis.bass_curve, analysis.brightness_curve, analysis.percussive_curve)
    indices = times.astype(int)
    if np.any(times < 0.0) or any(not curve or np.max(indices) >= len(curve) for curve in curves):
        return None
    return np.array([[curve[index] for index in indices] for curve in curves])


@lru_cache(maxsize=512)
def _gains(window: tuple[float, float], incoming: bool) -> np.ndarray:
    start, end = window
    phase = np.clip((_PROGRESS - start) / max(1e-6, end - start), 0.0, 1.0) * np.pi / 2
    return np.sin(phase) if incoming else np.cos(phase)


def _singing(analysis: TrackAnalysis, times: np.ndarray) -> np.ndarray:
    return np.array([any(a <= time < b for a, b in sung_spans(analysis)) for time in times], dtype=float)


def optimize_handoff(
    candidate: TransitionCandidate, decision: TransitionDspDecision,
    outgoing: TrackAnalysis, incoming: TrackAnalysis, incoming_structure: TrackStructureAnalysis | None,
    *, previous_style: TransitionDsp | None = None,
) -> tuple[TransitionCandidate, TransitionDspDecision]:
    """Compare audible holes, bass/voice collisions and lost words across envelopes.

    ponytail: power/centroid proxy, not waveform audition or a perceptual ML
    judge; use listening ratings to calibrate before claiming human DJ quality.
    """
    duration = candidate.duration_seconds
    if duration <= 0.0 or candidate.strategy is TransitionStrategy.PHRASE_EXIT:
        return candidate, decision
    out_times = candidate.outgoing_source_time + _PROGRESS * duration * candidate.outgoing_rate
    in_times = candidate.incoming_source_time + _PROGRESS * duration * candidate.incoming_rate
    out, inc = _features(outgoing, out_times), _features(incoming, in_times)
    if out is None or inc is None:
        return candidate, decision

    vocals = vocal_map(candidate, outgoing, incoming)
    unknown = vocals is None
    out_voice, in_voice = _singing(outgoing, out_times), _singing(incoming, in_times)
    facts = dict(decision.metrics)
    clash = bool(facts.get("key_clash")) * min(outgoing.key_confidence, incoming.key_confidence)
    gentle = max(character(music_tags_at(a, float(t[0]), float(t[-1])))[0]
                 for a, t in ((outgoing, out_times), (incoming, in_times))) >= 0.55
    beat = 60.0 / incoming.bpm if incoming.bpm else 0.5
    centers = [0.1, 0.25, 0.5, 0.525, 0.75, 0.9, 0.95]
    if incoming.beat_alignment_quality() == "reliable":
        for target in centers[:]:
            downbeats = [(b - candidate.incoming_source_time) / duration for b in incoming.downbeats
                         if 0.12 <= (b - candidate.incoming_source_time) / duration <= 0.88]
            if downbeats:
                centers.append(min(downbeats, key=lambda p: abs(p - target)))
    for a, b in sung_spans(outgoing):
        point = (b - candidate.outgoing_source_time) / candidate.outgoing_rate / duration
        if 0.12 <= point <= 0.88:
            centers.append(point)
    for a, b in sung_spans(incoming):
        point = (a - candidate.incoming_source_time) / duration
        if 0.12 <= point <= 0.88:
            centers.append(point)
    centers = sorted(set(round(c, 6) for c in centers))
    width = min(0.25, max(0.06, beat / duration))

    def window(center: float) -> tuple[float, float]:
        return max(0.0, center - width / 2), min(1.0, center + width / 2)

    options = []
    if decision.dsp is TransitionDsp.DROP_IN:
        options.append((TransitionDsp.DROP_IN, None))
    elif duration < 2.0 or (gentle and not unknown and not clash):
        options.append((TransitionDsp.SHORT_FADE, None))
    else:
        if duration < 4.0 or (not unknown and not clash):
            options.append((TransitionDsp.SHORT_FADE, None))
        allowed = (TransitionDsp.VOCAL_SAFE_EQ,) if unknown or clash or facts.get("vocal_overlap", 0) else tuple(BAND_ENVELOPES)
        for style in allowed:
            default = tuple(BAND_ENVELOPES[style][band] for band in ("low", "mid", "high"))
            if style is TransitionDsp.VOCAL_SAFE_EQ and decision.vocal_handoff is not None:
                mid = (decision.vocal_handoff - 0.125, decision.vocal_handoff + 0.125)
                default = (default[0], (mid, mid), default[2])
            options.append((style, default))
            for low_center in centers:
                for voice_center in centers:
                    low, mid = window(low_center), window(voice_center)
                    options.append((style, ((low, low), (mid, mid), default[2])))

    # The spectral features are fractions, not stem separation. Predict band
    # power, then use the exact qsin gains that the renderer will execute.
    def bands(features: np.ndarray) -> np.ndarray:
        bass = features[1]
        high = np.minimum(1 - bass, features[2])
        return np.array([bass, 1 - bass - high, high]) * features[0] ** 2

    out_power, in_power = bands(out), bands(inc)
    # Compare against the solo music immediately after the transition. An
    # intro whose drums arrive at the END of the blend must not be mistaken
    # for the quiet level the next track will keep playing at.
    after_times = np.minimum(incoming.duration_seconds - 1e-6,
                             candidate.incoming_source_time + duration + (np.arange(6) + 0.5) * beat / 3)
    after = _features(incoming, after_times)
    after_power = bands(after) if after is not None else in_power[:, -6:]
    def reference_to(out_level, in_level):
        return np.exp((1 - _PROGRESS) * np.log(max(1e-10, float(out_level)))
                      + _PROGRESS * np.log(max(1e-10, float(in_level))))
    reference = reference_to(np.mean(out[0, :6] ** 2), np.mean(np.sum(after_power, axis=0)))
    bass_reference = reference_to(np.mean(out_power[0, :6]), np.mean(after_power[0]))

    def evaluate(style, windows):
        if windows is None:
            go = np.tile(_gains((0.0, 1.0), False), (3, 1))
            gi = np.ones_like(go) if style is TransitionDsp.DROP_IN else np.tile(_gains((0.0, 1.0), True), (3, 1))
        else:
            go = np.array([_gains(w[0], False) for w in windows])
            gi = np.array([_gains(w[1], True) for w in windows])
        power = np.sum(out_power * go ** 2 + in_power * gi ** 2, axis=0)
        delta = 10 * np.log10(np.maximum(power, 1e-10) / reference)
        holes = float(np.mean(np.maximum(0.0, -delta - 1.5)))
        bumps = float(np.mean(np.maximum(0.0, delta - 2.0)))
        lost = float(np.mean(out_voice * (1 - go[1]) ** 2 + in_voice * (1 - gi[1]) ** 2)) * duration
        voice_collision = float(np.mean(out_voice * in_voice * go[1] * gi[1])) * duration
        bass_collision = float(np.mean(np.minimum(out[1], inc[1]) * out[3] * inc[3] * go[0] * gi[0])) * duration
        bass_level = out_power[0] * go[0] ** 2 + in_power[0] * gi[0] ** 2
        bass_hole = float(np.mean(np.maximum(0.0, -10 * np.log10(np.maximum(bass_level, 1e-10) / bass_reference) - 3)))
        harmony = float(np.mean(go[1] * gi[1])) * clash * duration
        rhythm = 0.0
        if candidate.strategy is not TransitionStrategy.BEAT_MATCH and outgoing.bpm and incoming.bpm:
            ratio = incoming.bpm / outgoing.bpm
            effective = min((ratio, ratio / 2, ratio * 2), key=lambda r: abs(r - 1.0))
            drift_beats = duration * outgoing.bpm / 60 * abs(effective - 1.0)
            rhythm = duration * min(1.0, drift_beats) * float(np.mean(out[3] * inc[3] * go[1] * gi[1]))
        cost = (0.055 * holes + 0.04 * bumps + 0.06 * lost + 0.15 * voice_collision
                + 0.035 * bass_collision + 0.035 * harmony + 0.025 * bass_hole + 0.06 * rhythm)
        if unknown:
            # Unknown singing is not proof of an instrumental. Keep the voice
            # handoff near the middle and avoid long speculative overlays.
            cost += 0.004 * duration
            if windows is not None:
                cost += 0.02 * abs(sum(windows[1][0]) / 2 - 0.525)
        # Only a near-tie prefers another style; musical safety dominates variety.
        if style is previous_style:
            cost += 0.008
        return cost, holes, bumps, lost, voice_collision, bass_collision

    scored = [(evaluate(style, windows), style, windows) for style, windows in options]
    metrics, style, windows = min(scored, key=lambda row: row[0][0])
    cost, holes, bumps, lost, voices, bass = metrics
    landing = candidate.incoming_source_time + duration
    landmarks = incoming_landmarks(incoming, incoming_structure)
    distance = min((abs(landing - point) for point in landmarks), default=None)
    landing_bonus = 0.10 * max(0.0, 1 - distance / max(beat, 0.1)) if distance is not None else 0.0
    trim = max(0.0, candidate.incoming_source_time - audible_start(incoming))
    skipped_words = sum(max(0.0, min(b, candidate.incoming_source_time) - max(a, audible_start(incoming)))
                        for a, b in merge_spans(list(sung_spans(incoming)), incoming.duration_seconds))
    # A head trim is paid for even below the old 30-second soft limit.
    score = candidate.score - cost + landing_bonus - 0.015 * trim - 0.10 * skipped_words
    reason = (f"* acoustic search: {style.value}, predicted hole {holes:.2f}dB, "
              f"voice collision {voices:.2f}s; "
              + ("independently timed bass/voice handoff" if windows else "full-band handoff"))
    handoff = sum(windows[1][0]) / 2 if windows is not None and style is TransitionDsp.VOCAL_SAFE_EQ else None
    decision = replace(decision, dsp=style, band_windows=windows, vocal_handoff=handoff,
                       reasons=(reason, *decision.reasons[1:]), metrics=(
                       *((name, handoff if name == "vocal_handoff" else value) for name, value in decision.metrics),
                       ("acoustic_cost", cost), ("predicted_hole_db", holes), ("predicted_bump_db", bumps),
                       ("obscured_vocal_seconds", lost), ("voice_collision_seconds", voices),
                       ("skipped_vocal_seconds", skipped_words),
                       ("bass_collision_cost", bass), ("landing_distance_seconds", distance)))
    return replace(candidate, score=score, reasons=(*candidate.reasons, reason)), decision
