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

    def test_reliable_beats_match_even_without_downbeats(self) -> None:
        outgoing = _analysis("a", 128.0, duration=200.0, with_downbeats=False)
        incoming = _analysis("b", 128.0, duration=200.0, with_downbeats=False)
        candidates = self._generate(outgoing, incoming)
        self.assertTrue(candidates)
        self.assertTrue(all(c.strategy is TransitionStrategy.BEAT_MATCH for c in candidates))
        self.assertTrue(all(abs(c.outgoing_rate - 1.0) < 1e-6 and c.incoming_rate == 1.0 for c in candidates))

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


class VocalAwareWindowTests(unittest.TestCase):
    """Two voices never share a blend: mix after the outgoing singer stops, or under an
    instrumental incoming intro; sung through on both sides, only a short handoff."""

    def _generate(self, outgoing, incoming):
        settings = AutoMixTransitionSettings(max_transition_seconds=60.0)
        return generate_candidates(outgoing, incoming, evaluate_compatibility(outgoing, incoming, settings), settings)

    def test_vocals_outside_the_windows_leave_beat_matching_untouched(self) -> None:
        outgoing = _analysis("a", 128.0, duration=200.0)
        incoming = _analysis("b", 128.0, duration=200.0)
        baseline = self._generate(outgoing, incoming)
        sung = self._generate(replace(outgoing, vocal_activity=((20.0, 120.0),)),
                              replace(incoming, vocal_activity=((60.0, 150.0),)))
        self.assertEqual([(c.bars, c.outgoing_source_time) for c in sung],
                         [(c.bars, c.outgoing_source_time) for c in baseline])

    def test_mixing_starts_on_the_first_downbeat_after_the_last_vocal(self) -> None:
        outgoing = replace(_analysis("a", 128.0, duration=200.0), vocal_activity=((10.0, 185.3),))
        candidates = self._generate(outgoing, _analysis("b", 128.0, duration=200.0))
        self.assertTrue(candidates)
        bar = 4 * 60.0 / 128.0
        for candidate in candidates:
            self.assertGreaterEqual(candidate.outgoing_source_time, 185.3)
        self.assertLess(min(c.outgoing_source_time for c in candidates) - 185.3, bar)  # right after, not bars later

    def test_the_incoming_track_may_already_sing_in_the_overlap(self) -> None:
        outgoing = _analysis("a", 128.0, duration=200.0)
        incoming = _analysis("b", 128.0, duration=200.0)
        baseline = self._generate(outgoing, incoming)
        singing = self._generate(outgoing, replace(incoming, vocal_activity=((1.0, 150.0),)))
        self.assertEqual([c.outgoing_source_time for c in singing], [c.outgoing_source_time for c in baseline])

    def test_a_short_instrumental_tail_still_blends_instead_of_cutting(self) -> None:
        outgoing = replace(_analysis("a", 128.0, duration=200.0), vocal_activity=((10.0, 198.4),))
        (best,) = [select_best_candidate(self._generate(outgoing, _analysis("b", 128.0, duration=200.0)))]
        self.assertIsNot(best.strategy, TransitionStrategy.CUT)
        self.assertGreaterEqual(best.outgoing_source_time, 198.4)
        self.assertGreaterEqual(best.duration_seconds, 1.0)

    def test_singing_through_the_junction_gets_a_short_handoff_blend(self) -> None:
        outgoing = replace(_analysis("a", 128.0, duration=200.0), vocal_activity=((10.0, 200.0),))
        for incoming_vocals in ((), ((0.0, 150.0),)):  # unknown, or singing from the first note
            with self.subTest(incoming_vocals=incoming_vocals):
                incoming = replace(_analysis("b", 128.0, duration=200.0), vocal_activity=incoming_vocals)
                (candidate,) = self._generate(outgoing, incoming)
                self.assertIs(candidate.strategy, TransitionStrategy.BEAT_MATCH)
                self.assertEqual(candidate.bars, 2)
                self.assertEqual(candidate.outgoing_source_out, 200.0)
                self.assertIn("- both tracks sing across the junction: a short 2-bar blend hands the voice over",
                              candidate.reasons)

    def test_a_slow_handoff_shrinks_to_one_bar_to_keep_the_overlap_brief(self) -> None:
        outgoing = replace(_analysis("a", 70.0, duration=200.0), vocal_activity=((10.0, 200.0),))
        (candidate,) = self._generate(outgoing, _analysis("b", 70.0, duration=200.0))
        self.assertEqual(candidate.bars, 1)  # two 70 BPM bars would be 6.9 s of both singing
        self.assertLess(candidate.duration_seconds, 2 * 4 * 60.0 / 70.0)  # one bar plus its downbeat snap

    def test_an_instrumental_intro_is_mixed_under_the_outgoing_singer(self) -> None:
        outgoing = replace(_analysis("a", 128.0, duration=200.0), vocal_activity=((10.0, 200.0),))
        incoming = replace(_analysis("b", 128.0, duration=200.0), vocal_activity=((30.0, 150.0),))
        best = select_best_candidate(self._generate(outgoing, incoming))
        self.assertEqual(best.bars, 8)  # the full preferred blend, not a handoff
        self.assertLessEqual(best.incoming_source_time + best.duration_seconds, 30.0)
        self.assertIn("+ blending under the outgoing vocals: the incoming intro is instrumental", best.reasons)
        # An intro shorter than every bar length still gets the handoff, never a blend into its singer.
        short_intro = replace(incoming, vocal_activity=((3.0, 150.0),))
        (handoff,) = self._generate(outgoing, short_intro)
        self.assertEqual(handoff.bars, 2)

    def test_fixed_crossfades_follow_the_same_vocal_rules(self) -> None:
        # 128 vs 90 BPM: no beat match, only the fixed crossfade.
        outgoing = replace(_analysis("a", 128.0, duration=200.0), vocal_activity=((10.0, 200.0),))
        for incoming_vocals, reason in (
            (((0.0, 150.0),), "- both tracks sing across the junction: a short fade hands the voice over"),
            (((30.0, 150.0),), "+ fading under the outgoing vocals: the incoming intro is instrumental"),
        ):
            with self.subTest(incoming_vocals=incoming_vocals):
                incoming = replace(_analysis("b", 90.0, duration=200.0), vocal_activity=incoming_vocals)
                (candidate,) = self._generate(outgoing, incoming)
                self.assertIs(candidate.strategy, TransitionStrategy.FIXED_CROSSFADE)
                self.assertEqual(candidate.duration_seconds, 3.0)
                self.assertIn(reason, candidate.reasons)

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

    def test_early_structure_anchor_cannot_cut_the_audible_ending(self) -> None:
        outgoing = _analysis("a", 128.0, duration=200.0)
        incoming = _analysis("b", 128.0, duration=200.0)
        # An outro starting well before the plain tail-based candidates would land.
        outgoing_structure = _structure("a", 200.0, outro_start=150.0)
        candidates = self._generate(outgoing, incoming, outgoing_structure=outgoing_structure)
        near_outro = [c for c in candidates if abs(c.outgoing_source_time - 150.0) < 2.0]
        self.assertFalse(near_outro)
        self.assertTrue(all(c.outgoing_source_out == 200.0 for c in candidates))

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

    def test_duplicate_candidate_from_a_structure_anchor_is_removed(self) -> None:
        """When a structure anchor snaps to the exact same downbeat as the
        plain tail-based candidate for the same bar length, the two are
        geometrically identical -- _deduplicate_candidates() must keep only
        one, and the result must stay deterministic across repeated calls."""
        outgoing = _analysis("a", 128.0, duration=200.0)
        incoming = _analysis("b", 128.0, duration=200.0)
        # 4 bars at 128 BPM / 4-4 meter = 7.5s -- the plain tail candidate
        # for bars=4 naturally lands at (200 - 7.5) snapped to a downbeat.
        # Anchor the outro right at that same natural tail position so the
        # structure-anchored bars=4 candidate snaps to the identical spot.
        outgoing_structure = _structure("a", 200.0, outro_start=192.5)
        first = self._generate(outgoing, incoming, outgoing_structure=outgoing_structure)
        second = self._generate(outgoing, incoming, outgoing_structure=outgoing_structure)
        self.assertEqual(first, second)
        keys = [(c.outgoing_source_time, c.incoming_source_time, c.bars, c.strategy) for c in first]
        self.assertEqual(len(keys), len(set(keys)), "duplicate candidate geometry was not deduplicated")

class ScoreSaturationTests(unittest.TestCase):
    """Commit C.1: score is an unclamped ranking signal, not a 0..1
    probability -- a perfect-confidence base candidate must not swallow the
    advanced bonuses on top of it (see _score_beat_candidate's removed
    upper clamp)."""

    def _perfect_score(self, outgoing, incoming, settings, **structure_kwargs) -> float:
        from app.automix.candidates import _score_beat_candidate

        compatibility = evaluate_compatibility(outgoing, incoming, settings)
        score, _reasons = _score_beat_candidate(
            outgoing, incoming, compatibility, 8, TransitionStrategy.BEAT_MATCH, settings,
            0.0, 0.0, 190.0, 0.0, 10.0,
            **structure_kwargs,
        )
        return score

    def _perfect_pair(self):
        outgoing = _analysis("a", 128.0, duration=200.0, bpm_confidence=1.0, meter_confidence=1.0)
        incoming = _analysis("b", 128.0, duration=200.0, bpm_confidence=1.0, meter_confidence=1.0)
        settings = AutoMixTransitionSettings(preferred_bars=8)
        return outgoing, incoming, settings

    def test_perfect_base_conditions_reach_exactly_one_before_any_bonus(self) -> None:
        outgoing, incoming, settings = self._perfect_pair()
        score = self._perfect_score(outgoing, incoming, settings)
        self.assertAlmostEqual(score, 1.0, places=6)

    def test_structure_anchor_bonus_still_beats_an_otherwise_identical_candidate(self) -> None:
        """A tie at a clamped 1.0 would make this test meaningless -- the
        whole point of removing the upper clamp is that a structure-aware
        candidate scores strictly higher than the same candidate without
        the anchor, even though the un-bonused score is already the
        theoretical base maximum."""
        outgoing, incoming, settings = self._perfect_pair()
        baseline = self._perfect_score(outgoing, incoming, settings)
        outgoing_structure = _structure("a", 200.0, outro_start=190.0)
        with_anchor = self._perfect_score(outgoing, incoming, settings, outgoing_structure=outgoing_structure)
        self.assertGreater(with_anchor, baseline)
        self.assertGreaterEqual(baseline, 1.0)

    def test_key_bonus_still_moves_ranking_at_perfect_base_confidence(self) -> None:
        outgoing, incoming, settings = self._perfect_pair()
        baseline = self._perfect_score(outgoing, incoming, settings)
        compatible_key = self._perfect_score(
            replace(outgoing, key="C major", key_confidence=0.8),
            replace(incoming, key="G major", key_confidence=0.8),
            settings,
        )
        self.assertGreater(compatible_key, baseline)

    def test_energy_bonus_still_moves_ranking_at_perfect_base_confidence(self) -> None:
        outgoing, incoming, settings = self._perfect_pair()
        baseline = self._perfect_score(outgoing, incoming, settings)
        similar_energy = self._perfect_score(
            replace(outgoing, energy=0.7), replace(incoming, energy=0.72), settings,
        )
        self.assertGreater(similar_energy, baseline)


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


class AudibleBoundsTests(unittest.TestCase):
    """Real masters end in seconds of digital silence (Phase 02 real-music check)."""

    def _best(self, outgoing: TrackAnalysis, incoming: TrackAnalysis):
        settings = AutoMixTransitionSettings(enabled=True)
        compatibility = evaluate_compatibility(outgoing, incoming, settings)
        return select_best_candidate(generate_candidates(outgoing, incoming, compatibility, settings))

    def test_fixed_crossfade_overlaps_the_audible_tail_and_head_not_the_silence(self) -> None:
        outgoing = replace(_analysis("a", 100.0, 200.0), audible_end_seconds=196.0)
        incoming = replace(_analysis("b", 130.0, 200.0), audible_start_seconds=1.5)
        best = self._best(outgoing, incoming)  # 30% apart: fixed crossfade
        self.assertIs(best.strategy, TransitionStrategy.FIXED_CROSSFADE)
        self.assertEqual((best.outgoing_source_time, best.outgoing_source_out), (193.0, 196.0))
        self.assertEqual(best.incoming_source_time, 1.5)

    def test_beat_match_window_ends_at_or_before_the_audible_end(self) -> None:
        outgoing = replace(_analysis("a", 120.0, 200.0), audible_end_seconds=195.0)
        best = self._best(outgoing, _analysis("b", 120.0, 200.0))
        self.assertIs(best.strategy, TransitionStrategy.BEAT_MATCH)
        self.assertLessEqual(best.outgoing_source_out, 195.0 + 1e-6)
        self.assertIn(best.outgoing_source_time, outgoing.downbeats)

    def test_unknown_bounds_keep_the_file_edges(self) -> None:
        best = self._best(_analysis("a", 100.0, 200.0), _analysis("b", 130.0, 200.0))
        self.assertEqual((best.outgoing_source_out, best.incoming_source_time), (200.0, 0.0))


if __name__ == "__main__":
    unittest.main()
