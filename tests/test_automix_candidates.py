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
from app.automix.structure.models import TrackSection, TrackStructureAnalysis


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


def _structure(
    track_id: str, duration: float, *,
    outro_start: float | None = None, intro_end: float | None = None,
    sections: tuple[TrackSection, ...] = (),
    energy_curve: tuple[float, ...] = (), hop: float | None = None,
) -> TrackStructureAnalysis:
    return TrackStructureAnalysis(
        track_id=track_id, source_path=f"{track_id}.wav", duration_seconds=duration,
        outro_start_seconds=outro_start, intro_end_seconds=intro_end, sections=sections,
        energy_curve=energy_curve, energy_curve_hop_seconds=hop,
        analyzer_id="sonara_structure", analyzer_version="1",
    )


class StructureAwareCandidateTests(unittest.TestCase):
    """Commit C: structure-anchored candidates and scoring, all optional/additive."""

    def _generate(self, outgoing, incoming, settings=None, *, outgoing_structure=None, incoming_structure=None):
        settings = settings or AutoMixTransitionSettings()
        compatibility = evaluate_compatibility(outgoing, incoming, settings)
        return generate_candidates(
            outgoing, incoming, compatibility, settings,
            outgoing_structure=outgoing_structure, incoming_structure=incoming_structure,
        )

    def test_no_structure_data_matches_the_pre_commit_c_result(self) -> None:
        outgoing = _analysis("a", 128.0, duration=200.0)
        incoming = _analysis("b", 128.0, duration=200.0)
        with_kwargs_omitted = self._generate(outgoing, incoming)
        with_kwargs_none = self._generate(outgoing, incoming, outgoing_structure=None, incoming_structure=None)
        self.assertEqual(with_kwargs_omitted, with_kwargs_none)

    def test_structure_anchor_adds_a_candidate_near_the_outro(self) -> None:
        outgoing = _analysis("a", 128.0, duration=200.0)
        incoming = _analysis("b", 128.0, duration=200.0)
        # An outro starting well before the plain tail-based candidates would land.
        outgoing_structure = _structure("a", 200.0, outro_start=150.0)
        candidates = self._generate(outgoing, incoming, outgoing_structure=outgoing_structure)
        near_outro = [c for c in candidates if abs(c.outgoing_source_time - 150.0) < 2.0]
        self.assertTrue(near_outro, "expected at least one candidate anchored near the structure outro")
        self.assertTrue(any("structure anchor" in reason for reason in near_outro[0].reasons))

    def test_structure_anchor_adds_a_candidate_near_the_intro_end(self) -> None:
        outgoing = _analysis("a", 128.0, duration=200.0)
        incoming = _analysis("b", 128.0, duration=200.0)
        incoming_structure = _structure("b", 200.0, intro_end=20.0)
        candidates = self._generate(outgoing, incoming, incoming_structure=incoming_structure)
        near_intro_end = [c for c in candidates if abs(c.incoming_source_time - 20.0) < 2.0]
        self.assertTrue(near_intro_end, "expected at least one candidate anchored near intro_end")

    def test_one_sided_structure_data_is_safe(self) -> None:
        """Only the outgoing side has structure data -- must not crash and
        must still produce candidates."""
        outgoing = _analysis("a", 128.0, duration=200.0)
        incoming = _analysis("b", 128.0, duration=200.0)
        outgoing_structure = _structure("a", 200.0, outro_start=150.0)
        candidates = self._generate(outgoing, incoming, outgoing_structure=outgoing_structure)
        self.assertTrue(candidates)
        best = select_best_candidate(candidates)
        self.assertIsNotNone(best)

    def test_falls_back_to_the_regular_bar_candidate_when_the_anchor_is_implausible(self) -> None:
        """An outro anchor far enough before the track's own end (a likely
        structure-analysis error, not a real long outro) is still
        generated as a candidate, but must lose to the plain tail-based
        candidate once the tail-trim penalty applies -- structure is a
        hint, never an override."""
        outgoing = _analysis("a", 128.0, duration=400.0)
        incoming = _analysis("b", 128.0, duration=400.0)
        implausible_structure = _structure("a", 400.0, outro_start=100.0)  # 300s of "unused" tail
        candidates = self._generate(outgoing, incoming, outgoing_structure=implausible_structure)
        best = select_best_candidate(candidates)
        self.assertIsNotNone(best)
        # The winning candidate must be near the natural tail, not the
        # implausible early anchor.
        self.assertGreater(best.outgoing_source_time, 350.0)

    def test_incoming_trim_penalty_reduces_score_for_a_large_intro_skip(self) -> None:
        outgoing = _analysis("a", 128.0, duration=200.0)
        incoming = _analysis("b", 128.0, duration=200.0)
        baseline = select_best_candidate(self._generate(outgoing, incoming)).score
        trimmed_structure = _structure("b", 200.0, intro_end=60.0)  # well past the soft limit
        trimmed_candidates = self._generate(outgoing, incoming, incoming_structure=trimmed_structure)
        anchored = [c for c in trimmed_candidates if abs(c.incoming_source_time - 60.0) < 2.0]
        self.assertTrue(anchored)
        self.assertLess(anchored[0].score, baseline)

    def test_local_energy_continuity_bonus_rewards_similar_local_energy(self) -> None:
        outgoing = _analysis("a", 128.0, duration=200.0)
        incoming = _analysis("b", 128.0, duration=200.0)
        settings = AutoMixTransitionSettings()
        compatibility = evaluate_compatibility(outgoing, incoming, settings)

        similar = _score_with_local_energy(outgoing, incoming, compatibility, settings, 0.5, 0.52)
        dissimilar = _score_with_local_energy(outgoing, incoming, compatibility, settings, 0.9, 0.1)
        self.assertGreater(similar, dissimilar)

    def test_vocal_overlap_only_checks_the_actual_transition_window(self) -> None:
        """Regression: the outgoing side previously checked vocal activity
        all the way to the track's own end, wider than the real transition
        window whenever the cue landed before the natural tail. Vocals
        placed outside the real window (but inside the track's tail) must
        no longer trigger the penalty. Tested directly against
        _score_beat_candidate with fixed cue positions -- candidate
        selection itself can shift bars/anchors between runs, which would
        make an end-to-end best-candidate comparison flaky."""
        from app.automix.candidates import _score_beat_candidate

        outgoing = _analysis("a", 128.0, duration=200.0)
        incoming = _analysis("b", 128.0, duration=200.0)
        settings = AutoMixTransitionSettings()
        compatibility = evaluate_compatibility(outgoing, incoming, settings)
        outgoing_source_time, incoming_source_time, duration_seconds = 180.0, 0.0, 10.0
        incoming_with_vocals = replace(incoming, vocal_activity=((0.0, 5.0),))

        def score(outgoing_analysis) -> float:
            result, _reasons = _score_beat_candidate(
                outgoing_analysis, incoming_with_vocals, compatibility, 8, TransitionStrategy.BEAT_MATCH,
                settings, 0.0, 0.0, outgoing_source_time, incoming_source_time, duration_seconds,
            )
            return result

        baseline = score(outgoing)  # no vocal_activity at all
        # Vocals after the real window (180-190) ends, but still before the
        # track's own natural end -- the old, wider check would have flagged
        # this; the tightened window must not.
        outside_window_score = score(replace(outgoing, vocal_activity=((191.0, 200.0),)))
        self.assertAlmostEqual(outside_window_score, baseline)
        # Sanity: vocals actually inside the real window must still penalize.
        inside_window_score = score(replace(outgoing, vocal_activity=((185.0, 195.0),)))
        self.assertLess(inside_window_score, baseline)


def _score_with_local_energy(outgoing, incoming, compatibility, settings, outgoing_energy, incoming_energy):
    from app.automix.candidates import _score_beat_candidate

    outgoing_structure = _structure(
        outgoing.track_id, outgoing.duration_seconds,
        energy_curve=(outgoing_energy,), hop=outgoing.duration_seconds,
    )
    incoming_structure = _structure(
        incoming.track_id, incoming.duration_seconds,
        energy_curve=(incoming_energy,), hop=incoming.duration_seconds,
    )
    score, _reasons = _score_beat_candidate(
        outgoing, incoming, compatibility, 8, TransitionStrategy.BEAT_MATCH, settings,
        0.0, 0.0, 0.0, 0.0, 10.0,
        outgoing_structure=outgoing_structure, incoming_structure=incoming_structure,
    )
    return score


if __name__ == "__main__":
    unittest.main()
