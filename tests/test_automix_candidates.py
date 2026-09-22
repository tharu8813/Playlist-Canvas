from __future__ import annotations

import unittest
from dataclasses import replace

from app.automix.candidates import (
    BAR_LENGTHS,
    TransitionStrategy,
    generate_candidates,
    select_best_candidate,
)
from app.automix.compatibility import evaluate_compatibility
from app.automix.models import TrackAnalysis
from app.automix.settings import AutoMixTransitionSettings


def _beats(bpm: float, duration: float) -> tuple[float, ...]:
    interval = 60.0 / bpm
    beats: list[float] = []
    t = 0.0
    while t < duration:
        beats.append(round(t, 6))
        t += interval
    return tuple(beats)


def _analysis(
    track_id: str, bpm: float | None, duration: float = 180.0, *,
    bpm_confidence: float = 0.9, meter_confidence: float = 0.8, with_downbeats: bool = True,
    with_beats: bool = True,
) -> TrackAnalysis:
    beats = _beats(bpm, duration) if (bpm and with_beats) else ()
    downbeats = beats[0::4] if (with_downbeats and beats) else ()
    return TrackAnalysis(
        track_id=track_id, source_path=f"{track_id}.mp3", duration_seconds=duration,
        bpm=bpm, bpm_confidence=bpm_confidence if bpm is not None else 0.0,
        beats=beats, downbeats=downbeats,
        meter_numerator=4 if downbeats else None, meter_denominator=4 if downbeats else None,
        meter_confidence=meter_confidence if downbeats else 0.0,
    )


class GenerateCandidatesTests(unittest.TestCase):
    def _generate(self, outgoing: TrackAnalysis, incoming: TrackAnalysis, settings: AutoMixTransitionSettings | None = None):
        settings = settings or AutoMixTransitionSettings()
        compatibility = evaluate_compatibility(outgoing, incoming, settings)
        return generate_candidates(outgoing, incoming, compatibility, settings)

    def test_high_confidence_downbeats_produce_beat_match(self) -> None:
        outgoing = _analysis("a", 128.0, duration=200.0)
        incoming = _analysis("b", 128.0, duration=200.0)
        candidates = self._generate(outgoing, incoming)
        self.assertTrue(candidates)
        self.assertTrue(all(c.strategy is TransitionStrategy.BEAT_MATCH for c in candidates))

    def test_no_downbeats_falls_back_to_beat_aligned_crossfade(self) -> None:
        outgoing = _analysis("a", 128.0, duration=200.0, with_downbeats=False)
        incoming = _analysis("b", 128.0, duration=200.0, with_downbeats=False)
        candidates = self._generate(outgoing, incoming)
        self.assertTrue(candidates)
        self.assertTrue(all(c.strategy is TransitionStrategy.BEAT_ALIGNED_CROSSFADE for c in candidates))
        self.assertTrue(all(c.outgoing_rate == 1.0 and c.incoming_rate == 1.0 for c in candidates))

    def test_no_beats_still_allows_bpm_only_crossfade_when_confidence_present(self) -> None:
        outgoing = _analysis("a", 128.0, duration=200.0, with_beats=False)
        incoming = _analysis("b", 128.0, duration=200.0, with_beats=False)
        candidates = self._generate(outgoing, incoming)
        self.assertTrue(candidates)
        self.assertEqual(candidates[0].strategy, TransitionStrategy.BEAT_ALIGNED_CROSSFADE)

    def test_no_bpm_produces_fixed_crossfade_fallback(self) -> None:
        outgoing = _analysis("a", None, duration=180.0)
        incoming = _analysis("b", 128.0, duration=180.0)
        candidates = self._generate(outgoing, incoming)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].strategy, TransitionStrategy.FIXED_CROSSFADE)

    def test_unsafe_tempo_gap_produces_fixed_crossfade_fallback(self) -> None:
        outgoing = _analysis("a", 90.0, duration=180.0)
        incoming = _analysis("b", 140.0, duration=180.0)
        candidates = self._generate(outgoing, incoming)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].strategy, TransitionStrategy.FIXED_CROSSFADE)

    def test_low_bpm_confidence_falls_back_even_when_tempo_matches(self) -> None:
        outgoing = _analysis("a", 128.0, duration=180.0, bpm_confidence=0.1)
        incoming = _analysis("b", 128.0, duration=180.0, bpm_confidence=0.1)
        candidates = self._generate(outgoing, incoming)
        self.assertEqual(len(candidates), 1)
        self.assertIn(candidates[0].strategy, (TransitionStrategy.FIXED_CROSSFADE, TransitionStrategy.CUT))

    def test_short_outgoing_track_disables_beat_candidates_needing_more_room(self) -> None:
        outgoing = _analysis("a", 128.0, duration=5.0)
        incoming = _analysis("b", 128.0, duration=200.0)
        candidates = self._generate(outgoing, incoming)
        for candidate in candidates:
            if candidate.strategy is TransitionStrategy.BEAT_MATCH:
                self.assertLessEqual(candidate.duration_seconds, outgoing.duration_seconds)

    def test_short_incoming_track_limits_bar_choices(self) -> None:
        outgoing = _analysis("a", 128.0, duration=200.0)
        incoming = _analysis("b", 128.0, duration=6.0)
        candidates = self._generate(outgoing, incoming)
        for candidate in candidates:
            self.assertLessEqual(candidate.duration_seconds, incoming.duration_seconds)

    def test_extremely_short_tracks_fall_back_to_cut(self) -> None:
        outgoing = _analysis("a", 128.0, duration=1.0)
        incoming = _analysis("b", 128.0, duration=1.0)
        candidates = self._generate(outgoing, incoming)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].strategy, TransitionStrategy.CUT)
        self.assertEqual(candidates[0].duration_seconds, 0.0)

    def test_bar_lengths_4_8_16_all_considered_when_they_fit(self) -> None:
        outgoing = _analysis("a", 120.0, duration=600.0)
        incoming = _analysis("b", 120.0, duration=600.0)
        settings = AutoMixTransitionSettings(max_transition_seconds=60.0)
        candidates = self._generate(outgoing, incoming, settings)
        self.assertEqual({c.bars for c in candidates}, set(BAR_LENGTHS))

    def test_scores_are_deterministic_across_repeated_calls(self) -> None:
        outgoing = _analysis("a", 128.0, duration=200.0)
        incoming = _analysis("b", 130.0, duration=200.0)
        first = self._generate(outgoing, incoming)
        second = self._generate(outgoing, incoming)
        self.assertEqual([c.score for c in first], [c.score for c in second])
        self.assertEqual([c.bars for c in first], [c.bars for c in second])

    def test_select_best_candidate_prefers_preferred_bars(self) -> None:
        outgoing = _analysis("a", 120.0, duration=600.0)
        incoming = _analysis("b", 120.0, duration=600.0)
        settings = AutoMixTransitionSettings(preferred_bars=8, max_transition_seconds=60.0)
        candidates = self._generate(outgoing, incoming, settings)
        best = select_best_candidate(candidates)
        self.assertIsNotNone(best)
        self.assertEqual(best.bars, 8)

    def test_select_best_candidate_on_empty_list_is_none(self) -> None:
        self.assertIsNone(select_best_candidate([]))

    def test_reasons_are_never_empty(self) -> None:
        outgoing = _analysis("a", 128.0, duration=200.0)
        incoming = _analysis("b", 128.0, duration=200.0)
        for candidate in self._generate(outgoing, incoming):
            self.assertTrue(candidate.reasons)


class AdvancedScoringTests(unittest.TestCase):
    """Phase 7: key/energy/vocal awareness as additive score modifiers."""

    def _best_score(self, outgoing: TrackAnalysis, incoming: TrackAnalysis) -> float:
        settings = AutoMixTransitionSettings(enabled=True)
        compatibility = evaluate_compatibility(outgoing, incoming, settings)
        best = select_best_candidate(generate_candidates(outgoing, incoming, compatibility, settings))
        assert best is not None
        return best.score

    def test_missing_advanced_data_matches_the_base_phase3_score(self) -> None:
        outgoing = _analysis("a", 128.0, duration=200.0)
        incoming = _analysis("b", 128.0, duration=200.0)
        with_key_absent = self._best_score(outgoing, incoming)
        # Explicitly setting key on only one side must not change anything --
        # both must be known for the bonus to apply.
        one_sided = replace(outgoing, key="C major", key_confidence=0.8)
        self.assertEqual(self._best_score(one_sided, incoming), with_key_absent)

    def test_compatible_key_increases_score(self) -> None:
        outgoing = _analysis("a", 128.0, duration=200.0)
        incoming = _analysis("b", 128.0, duration=200.0)
        baseline = self._best_score(outgoing, incoming)
        compatible = self._best_score(
            replace(outgoing, key="C major", key_confidence=0.8),
            replace(incoming, key="G major", key_confidence=0.8),  # 8B/9B: Camelot-adjacent
        )
        self.assertGreater(compatible, baseline)

    def test_incompatible_key_is_not_penalized(self) -> None:
        outgoing = _analysis("a", 128.0, duration=200.0)
        incoming = _analysis("b", 128.0, duration=200.0)
        baseline = self._best_score(outgoing, incoming)
        incompatible = self._best_score(
            replace(outgoing, key="C major", key_confidence=0.8),
            replace(incoming, key="F# major", key_confidence=0.8),  # 8B vs 2B: not compatible
        )
        self.assertEqual(incompatible, baseline)

    def test_similar_energy_increases_score(self) -> None:
        outgoing = _analysis("a", 128.0, duration=200.0)
        incoming = _analysis("b", 128.0, duration=200.0)
        baseline = self._best_score(outgoing, incoming)
        similar = self._best_score(replace(outgoing, energy=0.7), replace(incoming, energy=0.72))
        self.assertGreater(similar, baseline)

    def test_vocal_overlap_on_both_sides_decreases_score(self) -> None:
        outgoing = _analysis("a", 128.0, duration=200.0)
        incoming = _analysis("b", 128.0, duration=200.0)
        baseline = self._best_score(outgoing, incoming)
        # Vocals near the very end of "a" and the very start of "b" so they
        # land inside whatever overlap window gets chosen.
        clashing = self._best_score(
            replace(outgoing, vocal_activity=((190.0, 200.0),)),
            replace(incoming, vocal_activity=((0.0, 10.0),)),
        )
        self.assertLess(clashing, baseline)

    def test_vocal_activity_on_only_one_side_does_not_penalize(self) -> None:
        outgoing = _analysis("a", 128.0, duration=200.0)
        incoming = _analysis("b", 128.0, duration=200.0)
        baseline = self._best_score(outgoing, incoming)
        one_sided = self._best_score(replace(outgoing, vocal_activity=((190.0, 200.0),)), incoming)
        self.assertEqual(one_sided, baseline)


if __name__ == "__main__":
    unittest.main()
