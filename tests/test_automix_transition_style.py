from __future__ import annotations

import unittest
from dataclasses import replace

from app.automix.candidates import TransitionCandidate, TransitionStrategy
from app.automix.compatibility import TransitionCompatibility
from app.automix.models import TrackAnalysis
from app.automix.structure.models import TrackStructureAnalysis
from app.automix.transition_style import describe_transition, select_transition_dsp
from app.timeline.render_plan import TransitionDsp


def _analysis(track_id: str, **fields) -> TrackAnalysis:
    return TrackAnalysis(track_id=track_id, source_path=f"{track_id}.wav", duration_seconds=120.0,
                         bpm=120.0, bpm_confidence=0.9, **fields)


def _candidate(strategy=TransitionStrategy.BEAT_MATCH, duration=8.0, **fields) -> TransitionCandidate:
    values = dict(
        from_track_id="a", to_track_id="b",
        outgoing_source_time=100.0, outgoing_source_out=100.0 + duration, incoming_source_time=0.0,
        bars=4, duration_seconds=duration, outgoing_bpm=120.0, incoming_bpm=120.0, target_bpm=120.0,
        outgoing_rate=1.0, incoming_rate=1.0, score=1.0, confidence=0.9, strategy=strategy, reasons=(),
    )
    values.update(fields)
    return TransitionCandidate(**values)


def _compatibility(shift_percent: float = 0.0) -> TransitionCompatibility:
    return TransitionCompatibility(
        from_track_id="a", to_track_id="b", compatible=True, tempo_shift_percent=shift_percent,
        used_half_double=False, outgoing_bpm=120.0, incoming_bpm=120.0, incoming_effective_bpm=120.0, reasons=(),
    )


def _structure(track_id: str, energy: float) -> TrackStructureAnalysis:
    return TrackStructureAnalysis(
        track_id=track_id, source_path=f"{track_id}.wav", duration_seconds=120.0,
        energy_curve=(energy,) * 120, energy_curve_hop_seconds=1.0,
    )


class SelectTransitionDspTests(unittest.TestCase):
    def select(self, candidate=None, compatibility=None, outgoing=None, incoming=None, **structures):
        return self.decide(candidate, compatibility, outgoing, incoming, **structures).dsp

    def decide(self, candidate=None, compatibility=None, outgoing=None, incoming=None, **structures):
        return select_transition_dsp(
            candidate or _candidate(), compatibility or _compatibility(),
            outgoing or _analysis("a"), incoming or _analysis("b"),
            structures.get("outgoing_structure"), structures.get("incoming_structure"),
        )

    def test_reliable_beat_match_without_conflicts_is_bass_swap(self) -> None:
        self.assertIs(self.select(), TransitionDsp.BASS_SWAP)

    def test_fixed_crossfade_and_cut_keep_the_legacy_mix(self) -> None:
        for strategy in (TransitionStrategy.FIXED_CROSSFADE, TransitionStrategy.CUT):
            with self.subTest(strategy=strategy):
                self.assertIsNone(self.select(_candidate(strategy, duration=2.0)))

    def test_short_window_is_short_fade_before_any_other_rule(self) -> None:
        vocal = ((0.0, 120.0),)
        result = self.select(_candidate(duration=3.5), outgoing=_analysis("a", vocal_activity=vocal),
                             incoming=_analysis("b", vocal_activity=vocal))
        self.assertIs(result, TransitionDsp.SHORT_FADE)
        self.assertIs(self.select(_candidate(duration=4.0)), TransitionDsp.BASS_SWAP)

    def test_vocals_on_both_sides_of_the_window_are_vocal_safe(self) -> None:
        outgoing = _analysis("a", vocal_activity=((102.0, 106.0),))
        incoming = _analysis("b", vocal_activity=((3.0, 20.0),))
        self.assertIs(self.select(outgoing=outgoing, incoming=incoming), TransitionDsp.VOCAL_SAFE_EQ)

    def test_vocals_outside_either_window_do_not_count(self) -> None:
        outgoing = _analysis("a", vocal_activity=((10.0, 90.0),))  # ends before the 100 s cue
        incoming = _analysis("b", vocal_activity=((3.0, 20.0),))
        self.assertIs(self.select(outgoing=outgoing, incoming=incoming), TransitionDsp.BASS_SWAP)

    def test_incoming_vocal_window_uses_the_incoming_source_span(self) -> None:
        # 8 s of timeline at 1.25x consumes 10 s of source: a vocal at 9 s is inside.
        outgoing = _analysis("a", vocal_activity=((101.0, 104.0),))
        incoming = _analysis("b", vocal_activity=((9.0, 12.0),))
        self.assertIs(self.select(_candidate(incoming_rate=1.25), outgoing=outgoing, incoming=incoming),
                      TransitionDsp.VOCAL_SAFE_EQ)
        self.assertIs(self.select(_candidate(incoming_rate=1.0), outgoing=outgoing, incoming=incoming),
                      TransitionDsp.BASS_SWAP)

    def test_clashing_keys_are_vocal_safe_but_compatible_or_unknown_keys_are_not(self) -> None:
        clash = self.select(outgoing=_analysis("a", key="C major"), incoming=_analysis("b", key="F# major"))
        self.assertIs(clash, TransitionDsp.VOCAL_SAFE_EQ)
        friendly = self.select(outgoing=_analysis("a", key="C major"), incoming=_analysis("b", key="G major"))
        self.assertIs(friendly, TransitionDsp.BASS_SWAP)
        unknown = self.select(outgoing=_analysis("a", key="C major"), incoming=_analysis("b", key="???"))
        self.assertIs(unknown, TransitionDsp.BASS_SWAP)

    def test_local_energy_jump_is_filter_blend_and_overrides_global_energy(self) -> None:
        calm = dict(outgoing=_analysis("a", energy=0.5), incoming=_analysis("b", energy=0.5))
        jump = self.select(**calm, outgoing_structure=_structure("a", 0.9), incoming_structure=_structure("b", 0.3))
        self.assertIs(jump, TransitionDsp.FILTER_BLEND)
        steady = self.select(outgoing=_analysis("a", energy=0.9), incoming=_analysis("b", energy=0.2),
                             outgoing_structure=_structure("a", 0.6), incoming_structure=_structure("b", 0.5))
        self.assertIs(steady, TransitionDsp.BASS_SWAP)

    def test_global_energy_is_the_fallback_without_structure(self) -> None:
        result = self.select(outgoing=_analysis("a", energy=0.9), incoming=_analysis("b", energy=0.4))
        self.assertIs(result, TransitionDsp.FILTER_BLEND)

    def test_beat_aligned_crossfade_is_legacy_unless_the_kicks_would_drift(self) -> None:
        aligned = _candidate(TransitionStrategy.BEAT_ALIGNED_CROSSFADE)
        self.assertIsNone(self.select(aligned, _compatibility(0.5)))  # 8 s * 0.5% = 0.04 s
        self.assertIs(self.select(aligned, _compatibility(2.0)), TransitionDsp.FILTER_BLEND)  # 0.16 s
        # A rate-matched BEAT_MATCH never drifts, whatever the raw BPM gap.
        self.assertIs(self.select(_candidate(), _compatibility(6.0)), TransitionDsp.BASS_SWAP)

    def test_selection_is_deterministic(self) -> None:
        outgoing = _analysis("a", vocal_activity=((102.0, 106.0),), key="C major", energy=0.4)
        incoming = _analysis("b", vocal_activity=((1.0, 2.0),), key="A minor", energy=0.5)
        results = {self.select(outgoing=replace(outgoing), incoming=replace(incoming)) for _ in range(20)}
        self.assertEqual(results, {TransitionDsp.VOCAL_SAFE_EQ})


_VOCALS_BOTH = dict(outgoing=_analysis("a", vocal_activity=((0.0, 120.0),)),
                    incoming=_analysis("b", vocal_activity=((0.0, 120.0),)))
_ENERGY_JUMP = dict(outgoing_structure=_structure("a", 0.9), incoming_structure=_structure("b", 0.3))


class SelectorPrecedenceTests(unittest.TestCase):
    """Rule order is policy: short -> vocal/key -> energy/drift -> bass swap."""

    select = SelectTransitionDspTests.select
    decide = SelectTransitionDspTests.decide

    def test_short_beats_vocal_overlap(self) -> None:
        self.assertIs(self.select(_candidate(duration=3.0), **_VOCALS_BOTH), TransitionDsp.SHORT_FADE)

    def test_long_vocal_overlap_is_vocal_safe(self) -> None:
        self.assertIs(self.select(_candidate(duration=8.0), **_VOCALS_BOTH), TransitionDsp.VOCAL_SAFE_EQ)

    def test_vocal_overlap_beats_energy_mismatch(self) -> None:
        self.assertIs(self.select(**_VOCALS_BOTH, **_ENERGY_JUMP), TransitionDsp.VOCAL_SAFE_EQ)

    def test_energy_mismatch_on_reliable_beat_match_is_filter_blend(self) -> None:
        self.assertIs(self.select(**_ENERGY_JUMP), TransitionDsp.FILTER_BLEND)

    def test_clean_reliable_beat_match_is_bass_swap(self) -> None:
        self.assertIs(self.select(), TransitionDsp.BASS_SWAP)


class UnknownDataTests(unittest.TestCase):
    """Missing analysis is never evidence for a style."""

    select = SelectTransitionDspTests.select
    decide = SelectTransitionDspTests.decide

    def test_no_optional_analysis_degrades_to_the_plain_styles(self) -> None:
        # _analysis() has no key, energy, vocals; no structure passed.
        self.assertIs(self.select(), TransitionDsp.BASS_SWAP)
        self.assertIsNone(self.select(_candidate(TransitionStrategy.BEAT_ALIGNED_CROSSFADE)))

    def test_one_sided_data_never_triggers_a_rule(self) -> None:
        cases = {
            "vocals": dict(outgoing=_analysis("a", vocal_activity=((0.0, 120.0),))),
            "key": dict(outgoing=_analysis("a", key="C major")),
            "energy": dict(outgoing=_analysis("a", energy=1.0), incoming=_analysis("b", energy=None)),
            "structure": dict(outgoing_structure=_structure("a", 1.0)),
        }
        for name, fields in cases.items():
            with self.subTest(name):
                self.assertIs(self.select(**fields), TransitionDsp.BASS_SWAP)

    def test_unknowns_are_reported_as_unknown(self) -> None:
        reasons = self.decide().reasons
        self.assertIn("? vocal activity unknown", reasons)
        self.assertIn("? key unknown", reasons)
        self.assertIn("? energy unknown", reasons)


class DecisionReasonTests(unittest.TestCase):
    decide = SelectTransitionDspTests.decide

    def test_first_reason_names_the_deciding_rule_with_its_numbers(self) -> None:
        cases = [
            (dict(candidate=_candidate(duration=2.8)), "* short_fade: transition only 2.8s (< 4.0s)"),
            (dict(**_VOCALS_BOTH), "* vocal_safe_eq: vocals overlap"),
            (dict(outgoing=_analysis("a", key="C major"), incoming=_analysis("b", key="F# major")),
             "* vocal_safe_eq: keys clash"),
            (dict(**_ENERGY_JUMP), "* filter_blend: local energy delta 0.60 (>= 0.3)"),
            (dict(candidate=_candidate(TransitionStrategy.BEAT_ALIGNED_CROSSFADE), compatibility=_compatibility(0.9125)),
             "* filter_blend: kicks would drift 73ms"),
            (dict(), "* bass_swap: clean reliable beat match"),
            (dict(candidate=_candidate(TransitionStrategy.FIXED_CROSSFADE)),
             "* legacy crossfade: fixed_crossfade has no reliable rhythm to style on"),
        ]
        for fields, expected in cases:
            with self.subTest(expected):
                self.assertEqual(self.decide(**fields).reasons[0], expected)

    def test_facts_list_every_signal_the_selector_read(self) -> None:
        decision = self.decide(
            _candidate(TransitionStrategy.BEAT_ALIGNED_CROSSFADE), _compatibility(1.6),
            _analysis("a", key="C major", energy=0.5, vocal_activity=((0.0, 120.0),)),
            _analysis("b", key="G major", energy=0.42, vocal_activity=((0.0, 50.0),)),
        )
        self.assertEqual(decision.reasons[1:], (
            "+ beat_aligned_crossfade (own tempo)",
            "- vocals active in both transition windows",
            "+ keys compatible (C major -> G major)",
            "+ global energy delta 0.08",
            "  tempo delta 1.6%",
            "- expected kick drift 128ms",
        ))

    def test_describe_transition_is_one_line_with_the_reasons(self) -> None:
        from app.timeline.models import TransitionType
        from app.timeline.render_plan import AudioRenderTransition
        decision = self.decide()
        transition = AudioRenderTransition("a", "b", 172.4, 8.0, TransitionType.BEAT_MATCH,
                                           decision.dsp, decision.reasons)
        line = describe_transition(transition, "Song A", "Song B")
        self.assertNotIn("\n", line)
        self.assertTrue(line.startswith("Song A -> Song B time=172.4s duration=8.0s type=beat_match dsp=bass_swap "))
        self.assertIn("reasons=[* bass_swap: clean reliable beat match; + beat_match (rate-matched); ", line)


if __name__ == "__main__":
    unittest.main()
