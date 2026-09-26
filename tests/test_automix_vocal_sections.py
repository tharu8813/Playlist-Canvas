"""Vocal-based intro/outro: measured head/tail regions, never guessed from gaps or failures."""

from __future__ import annotations

import unittest
from dataclasses import replace

from app.automix.analysis.vocals import measured_regions
from app.automix.candidates import (
    _instrumental,
    generate_candidates,
    select_best_candidate,
    vocal_intro_end,
    vocal_outro_start,
)
from app.automix.compatibility import evaluate_compatibility
from app.automix.models import TrackAnalysis
from app.automix.settings import AutoMixTransitionSettings
from app.automix.structure.models import TrackStructureAnalysis
from app.automix.transition_style import select_transition_dsp, vocal_map
from app.timeline.render_plan import TransitionDsp
from tests.test_automix_planner import _analysis

DURATION = 200.0
MEASURED = measured_regions(DURATION)  # (0, 45) and (155, 200), what the detector separates


def _sung(*spans: tuple[float, float], coverage=MEASURED, bpm: float = 128.0) -> TrackAnalysis:
    return replace(_analysis("t", bpm, DURATION), vocal_activity=tuple(spans), vocal_coverage=coverage)


class VocalSectionTests(unittest.TestCase):
    def test_singing_from_the_first_note_has_no_intro(self) -> None:
        self.assertEqual(vocal_intro_end(_sung((0.0, 30.0), (160.0, 190.0))), 0.0)

    def test_a_long_instrumental_intro_ends_at_the_first_vocal(self) -> None:
        self.assertEqual(vocal_intro_end(_sung((24.5, 40.0), (160.0, 190.0))), 24.5)

    def test_singing_to_the_end_has_no_outro(self) -> None:
        self.assertEqual(vocal_outro_start(_sung((5.0, 40.0), (156.0, 200.0))), 200.0)

    def test_a_long_instrumental_outro_starts_after_the_last_vocal(self) -> None:
        self.assertEqual(vocal_outro_start(_sung((5.0, 40.0), (156.0, 171.2))), 171.2)

    def test_a_quiet_ad_lib_near_the_end_still_ends_the_vocals(self) -> None:
        self.assertEqual(vocal_outro_start(_sung((5.0, 40.0), (156.0, 170.0), (186.0, 187.1))), 187.1)

    def test_an_interlude_before_the_vocals_return_is_not_an_outro(self) -> None:
        # Silent through the unmeasured middle, singing again in the tail region.
        self.assertEqual(vocal_outro_start(_sung((5.0, 40.0), (175.0, 180.0))), 180.0)

    def test_no_voice_in_the_tail_region_but_an_unmeasured_middle_is_unknown(self) -> None:
        # The last line is at 40 s; whether the song sings between 45 s and 155 s was never measured.
        self.assertIsNone(vocal_outro_start(_sung((5.0, 40.0))))
        self.assertIsNone(vocal_intro_end(_sung((160.0, 170.0))))

    def test_lyric_lines_only_ever_add_singing(self) -> None:
        analysis = replace(_sung((5.0, 40.0), (156.0, 170.0)), lyric_vocal_spans=((180.0, 188.0),))
        self.assertEqual(vocal_outro_start(analysis), 188.0)
        self.assertFalse(_instrumental(analysis, 180.0, 190.0))
        # Lyrics alone never prove silence.
        self.assertFalse(_instrumental(replace(_analysis("t", 128.0, DURATION), lyric_vocal_spans=((5.0, 8.0),)),
                                       20.0, 30.0))

    def test_a_failed_detection_is_unknown_not_instrumental(self) -> None:
        failed = _sung(coverage=())
        self.assertIsNone(vocal_intro_end(failed))
        self.assertIsNone(vocal_outro_start(failed))
        self.assertFalse(_instrumental(failed, 170.0, 190.0))

    def test_unmeasured_middle_is_never_instrumental(self) -> None:
        analysis = _sung((5.0, 40.0))
        self.assertTrue(_instrumental(analysis, 160.0, 190.0))
        self.assertFalse(_instrumental(analysis, 60.0, 90.0))
        self.assertFalse(_instrumental(analysis, 40.5, 50.0))  # starts measured, runs past the head region

    def test_a_fully_measured_instrumental_track(self) -> None:
        short = replace(_analysis("t", 128.0, 80.0), vocal_activity=(), vocal_coverage=measured_regions(80.0))
        self.assertEqual(vocal_intro_end(short), 80.0)
        self.assertEqual(vocal_outro_start(short), 0.0)
        self.assertTrue(_instrumental(short, 10.0, 70.0))

    def test_older_results_keep_their_whole_track_meaning(self) -> None:
        legacy = replace(_analysis("t", 128.0, DURATION), vocal_activity=((5.0, 40.0),))  # vocal_coverage=None
        self.assertTrue(_instrumental(legacy, 60.0, 90.0))
        self.assertFalse(_instrumental(_analysis("t", 128.0, DURATION), 60.0, 90.0))  # no spans: unknown


class InstrumentalTransitionTests(unittest.TestCase):
    settings = AutoMixTransitionSettings(enabled=True, preferred_bars=16, max_transition_seconds=40.0)

    def _best(self, outgoing, incoming, **kwargs):
        compatibility = evaluate_compatibility(outgoing, incoming, self.settings)
        return select_best_candidate(generate_candidates(outgoing, incoming, compatibility, self.settings, **kwargs))

    def test_the_overlap_prefers_the_length_that_ends_before_the_incoming_singer(self) -> None:
        outgoing = replace(_sung((5.0, 40.0), (156.0, 165.0)), track_id="a")  # 35 s instrumental outro
        incoming = replace(_sung((16.0, 40.0), (160.0, 190.0)), track_id="b")  # 16 s instrumental intro
        best = self._best(outgoing, incoming)
        self.assertLessEqual(best.incoming_source_time + best.duration_seconds, 16.0)
        self.assertIn("+ outro over intro: neither track sings in the overlap", best.reasons)

    def test_unknown_vocals_get_no_instrumental_bonus(self) -> None:
        best = self._best(replace(_analysis("a", 128.0, DURATION)), replace(_analysis("b", 128.0, DURATION)))
        self.assertNotIn("+ outro over intro: neither track sings in the overlap", best.reasons)

    def test_measured_instrumentals_are_not_mixed_as_unknown_vocals(self) -> None:
        outgoing = replace(_sung(), track_id="a")  # measured, no singing: an instrumental
        incoming = replace(_sung(), track_id="b")
        best = self._best(outgoing, incoming)
        self.assertIsNotNone(vocal_map(best, outgoing, incoming))
        decision = select_transition_dsp(best, evaluate_compatibility(outgoing, incoming, self.settings), outgoing, incoming)
        self.assertIsNot(decision.dsp, TransitionDsp.VOCAL_SAFE_EQ)
        failed = replace(outgoing, vocal_coverage=())
        self.assertIsNone(vocal_map(best, failed, incoming))  # a failed detection stays unknown

    def test_a_structure_cue_never_enters_the_incoming_track_mid_phrase(self) -> None:
        outgoing = replace(_sung((5.0, 40.0), (156.0, 165.0)), track_id="a")
        incoming = replace(_sung((10.0, 40.0), (160.0, 190.0)), track_id="b")
        structure = TrackStructureAnalysis(track_id="b", source_path="b.mp3", duration_seconds=DURATION,
                                           intro_end_seconds=20.0)
        compatibility = evaluate_compatibility(outgoing, incoming, self.settings)
        for candidate in generate_candidates(outgoing, incoming, compatibility, self.settings,
                                             incoming_structure=structure):
            inside = [(a, b) for a, b in incoming.vocal_activity if a + 0.5 < candidate.incoming_source_time < b]
            self.assertEqual(inside, [], candidate)


if __name__ == "__main__":
    unittest.main()
