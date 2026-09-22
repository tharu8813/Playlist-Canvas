from __future__ import annotations

import unittest

from app.automix.compatibility import evaluate_compatibility, resolve_target_bpm
from app.automix.models import TrackAnalysis
from app.automix.settings import AutoMixTransitionSettings


def _analysis(track_id: str, bpm: float | None, bpm_confidence: float = 0.9) -> TrackAnalysis:
    return TrackAnalysis(
        track_id=track_id, source_path=f"{track_id}.mp3", duration_seconds=180.0,
        bpm=bpm, bpm_confidence=bpm_confidence,
    )


class EvaluateCompatibilityTests(unittest.TestCase):
    def test_equal_bpm_is_compatible_with_no_shift(self) -> None:
        result = evaluate_compatibility(_analysis("a", 128.0), _analysis("b", 128.0), AutoMixTransitionSettings())
        self.assertTrue(result.compatible)
        self.assertEqual(result.tempo_shift_percent, 0.0)
        self.assertFalse(result.used_half_double)

    def test_close_bpm_is_compatible(self) -> None:
        result = evaluate_compatibility(_analysis("a", 128.0), _analysis("b", 132.0), AutoMixTransitionSettings())
        self.assertTrue(result.compatible)
        self.assertAlmostEqual(result.tempo_shift_percent, (132.0 - 128.0) / 128.0 * 100.0, places=4)

    def test_exact_double_tempo_is_compatible_via_half_double(self) -> None:
        result = evaluate_compatibility(_analysis("a", 128.0), _analysis("b", 64.0), AutoMixTransitionSettings())
        self.assertTrue(result.compatible)
        self.assertTrue(result.used_half_double)
        self.assertEqual(result.incoming_effective_bpm, 128.0)
        self.assertEqual(result.tempo_shift_percent, 0.0)

    def test_unsafe_tempo_gap_is_incompatible(self) -> None:
        # 90 vs 140: the raw gap and both octave folds (70, 280) are all too far.
        result = evaluate_compatibility(_analysis("a", 90.0), _analysis("b", 140.0), AutoMixTransitionSettings())
        self.assertFalse(result.compatible)

    def test_roadmap_example_90_vs_170_is_compatible_only_through_double_time(self) -> None:
        # roadmap section 5: "A=90, B=170 may be compatible only through
        # double-time interpretation" -- halving 170 to 85 is within 8% of 90.
        result = evaluate_compatibility(_analysis("a", 90.0), _analysis("b", 170.0), AutoMixTransitionSettings())
        self.assertTrue(result.compatible)
        self.assertTrue(result.used_half_double)

    def test_missing_bpm_is_incompatible(self) -> None:
        result = evaluate_compatibility(_analysis("a", None), _analysis("b", 128.0), AutoMixTransitionSettings())
        self.assertFalse(result.compatible)
        self.assertIsNone(result.incoming_effective_bpm)

    def test_half_double_disabled_rejects_double_tempo(self) -> None:
        settings = AutoMixTransitionSettings(allow_half_double_tempo=False)
        result = evaluate_compatibility(_analysis("a", 128.0), _analysis("b", 64.0), settings)
        self.assertFalse(result.compatible)
        self.assertFalse(result.used_half_double)

    def test_tempo_shift_at_exact_limit_is_compatible(self) -> None:
        settings = AutoMixTransitionSettings(max_tempo_change_percent=10.0)
        result = evaluate_compatibility(_analysis("a", 100.0), _analysis("b", 110.0), settings)
        self.assertTrue(result.compatible)

    def test_tempo_shift_just_over_limit_is_incompatible(self) -> None:
        settings = AutoMixTransitionSettings(max_tempo_change_percent=10.0)
        result = evaluate_compatibility(_analysis("a", 100.0), _analysis("b", 110.1), settings)
        self.assertFalse(result.compatible)


class ResolveTargetBpmTests(unittest.TestCase):
    """"Favor outgoing" policy (Commit C): the target is always the
    outgoing track's own (possibly chain-propagated effective) BPM --
    unified with the rate planner.py actually applies, see
    resolve_target_bpm's docstring. No confidence weighting is left to
    test; ``outgoing.bpm`` alone determines the result."""

    def test_target_is_always_the_outgoing_tracks_own_bpm(self) -> None:
        self.assertAlmostEqual(resolve_target_bpm(_analysis("a", 120.0)), 120.0)
        self.assertAlmostEqual(resolve_target_bpm(_analysis("a", 95.5)), 95.5)

    def test_incoming_bpm_and_confidence_have_no_effect(self) -> None:
        # The function only ever reads outgoing.bpm now -- there is nothing
        # else to vary; this documents that explicitly for a reader coming
        # from the pre-Commit-C confidence-weighted-midpoint policy.
        outgoing = _analysis("a", 120.0)
        self.assertEqual(resolve_target_bpm(outgoing), resolve_target_bpm(outgoing))


if __name__ == "__main__":
    unittest.main()
