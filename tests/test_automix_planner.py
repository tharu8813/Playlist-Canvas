from __future__ import annotations

import unittest

from dataclasses import replace

from app.automix.candidates import TransitionStrategy, generate_candidates, select_best_candidate
from app.automix.compatibility import evaluate_compatibility
from app.automix.models import TrackAnalysis
from app.automix.planner import compile_automix
from app.automix.settings import AutoMixTransitionSettings
from app.automix.structure.models import TrackSection, TrackStructureAnalysis
from app.models.playlist import PlaylistTrack
from app.timeline.compiler import compile_playlist
from app.timeline.models import TransitionType
from app.timeline.render_plan import validate_compiled_render_plan


def _beats(bpm: float, duration: float) -> tuple[float, ...]:
    interval = 60.0 / bpm
    beats: list[float] = []
    t = 0.0
    while t < duration:
        beats.append(round(t, 6))
        t += interval
    return tuple(beats)


def _track(track_id: str, duration: float, start: float | None = None, enabled: bool = True) -> PlaylistTrack:
    return PlaylistTrack(
        file_path=f"{track_id}.mp3", title=track_id, duration_seconds=duration,
        start_time_seconds=start, id=track_id, enabled=enabled,
    )


def _analysis(
    track_id: str, bpm: float | None, duration: float, *,
    bpm_confidence: float = 0.9, meter_confidence: float = 0.8,
) -> TrackAnalysis:
    beats = _beats(bpm, duration) if bpm else ()
    downbeats = beats[0::4] if beats else ()
    return TrackAnalysis(
        track_id=track_id, source_path=f"{track_id}.mp3", duration_seconds=duration,
        bpm=bpm, bpm_confidence=bpm_confidence if bpm else 0.0,
        beats=beats, downbeats=downbeats,
        meter_numerator=4 if downbeats else None, meter_denominator=4 if downbeats else None,
        meter_confidence=meter_confidence if downbeats else 0.0,
    )


ENABLED = AutoMixTransitionSettings(enabled=True, preferred_bars=8, max_transition_seconds=30.0)


class CompileAutomixTwoTrackTests(unittest.TestCase):
    def test_compatible_bpm_overlaps_and_shortens_total_duration(self) -> None:
        tracks = [_track("a", 60.0), _track("b", 60.0)]
        analyses = {
            "a": _analysis("a", 120.0, 60.0),
            "b": _analysis("b", 120.0, 60.0),
        }
        plan = compile_automix(tracks, analyses, ENABLED)
        validate_compiled_render_plan(plan)

        clip_a, clip_b = plan.audio.clips
        self.assertEqual(clip_a.timeline_start, 0.0)
        self.assertEqual(clip_a.timeline_end, 60.0)
        self.assertLess(clip_b.timeline_start, clip_a.timeline_end)
        self.assertGreater(clip_b.timeline_start, 0.0)
        self.assertEqual(len(plan.audio.transitions), 1)
        self.assertLess(plan.duration_seconds, 120.0)
        self.assertEqual(plan.duration_seconds, clip_b.timeline_end)

    def test_no_analysis_at_all_matches_sequential_compiler(self) -> None:
        tracks = [_track("a", 60.0), _track("b", 60.0)]
        plan = compile_automix(tracks, {}, ENABLED)
        sequential = compile_playlist(tracks)
        self.assertEqual(
            [(c.track_id, c.timeline_start, c.timeline_end) for c in plan.audio.clips],
            [(c.track_id, c.timeline_start, c.timeline_end) for c in sequential.audio.clips],
        )
        self.assertEqual(plan.audio.transitions, ())
        self.assertEqual(plan.duration_seconds, sequential.duration_seconds)

    def test_settings_disabled_never_overlaps(self) -> None:
        tracks = [_track("a", 60.0), _track("b", 60.0)]
        analyses = {"a": _analysis("a", 120.0, 60.0), "b": _analysis("b", 120.0, 60.0)}
        disabled = AutoMixTransitionSettings(enabled=False)
        plan = compile_automix(tracks, analyses, disabled)
        self.assertEqual(plan.duration_seconds, 120.0)
        self.assertEqual(plan.audio.transitions, ())

    def test_unsafe_tempo_gap_falls_back_without_overlap_or_rate(self) -> None:
        tracks = [_track("a", 60.0), _track("b", 60.0)]
        analyses = {"a": _analysis("a", 90.0, 60.0), "b": _analysis("b", 140.0, 60.0)}
        plan = compile_automix(tracks, analyses, ENABLED)
        clip_a, clip_b = plan.audio.clips
        self.assertEqual(clip_a.playback_rate, 1.0)
        self.assertEqual(clip_b.playback_rate, 1.0)
        for transition in plan.audio.transitions:
            self.assertNotEqual(transition.type, TransitionType.BEAT_MATCH)

    def test_beat_match_applies_rate_only_to_incoming_clip(self) -> None:
        tracks = [_track("a", 60.0), _track("b", 60.0)]
        analyses = {"a": _analysis("a", 120.0, 60.0), "b": _analysis("b", 124.0, 60.0)}
        plan = compile_automix(tracks, analyses, ENABLED)
        clip_a, clip_b = plan.audio.clips
        self.assertEqual(clip_a.playback_rate, 1.0)
        self.assertAlmostEqual(clip_b.playback_rate, 120.0 / 124.0)
        self.assertEqual(plan.audio.transitions[0].type, TransitionType.BEAT_MATCH)

    def test_missing_analysis_on_one_track_falls_back_to_sequential_adjacency(self) -> None:
        tracks = [_track("a", 60.0), _track("b", 60.0)]
        analyses = {"a": _analysis("a", 120.0, 60.0)}  # b has no analysis
        plan = compile_automix(tracks, analyses, ENABLED)
        clip_a, clip_b = plan.audio.clips
        self.assertEqual(clip_b.timeline_start, clip_a.timeline_end)
        self.assertEqual(plan.audio.transitions, ())

    def test_disabled_track_is_skipped_cleanly(self) -> None:
        tracks = [_track("a", 60.0), _track("b", 30.0, enabled=False), _track("c", 60.0)]
        analyses = {
            "a": _analysis("a", 120.0, 60.0), "b": _analysis("b", 120.0, 30.0),
            "c": _analysis("c", 120.0, 60.0),
        }
        plan = compile_automix(tracks, analyses, ENABLED)
        self.assertEqual({c.track_id for c in plan.audio.clips}, {"a", "c"})

    def test_explicit_gap_is_preserved_not_overlapped(self) -> None:
        tracks = [_track("a", 60.0), _track("b", 60.0, start=75.0)]
        analyses = {"a": _analysis("a", 120.0, 60.0), "b": _analysis("b", 120.0, 60.0)}
        plan = compile_automix(tracks, analyses, ENABLED)
        clip_a, clip_b = plan.audio.clips
        self.assertEqual(clip_a.timeline_end, 60.0)
        self.assertEqual(clip_b.timeline_start, 75.0)
        self.assertEqual(plan.audio.transitions, ())

    def test_determinism_same_input_produces_an_equal_plan(self) -> None:
        tracks = [_track("a", 60.0), _track("b", 60.0)]
        analyses = {"a": _analysis("a", 120.0, 60.0), "b": _analysis("b", 124.0, 60.0)}
        first = compile_automix(tracks, analyses, ENABLED)
        second = compile_automix(tracks, analyses, ENABLED)
        self.assertEqual(first, second)

    def test_presentation_switches_exactly_when_incoming_clip_starts(self) -> None:
        tracks = [_track("a", 60.0), _track("b", 60.0)]
        analyses = {"a": _analysis("a", 120.0, 60.0), "b": _analysis("b", 120.0, 60.0)}
        plan = compile_automix(tracks, analyses, ENABLED)
        clip_a, clip_b = plan.audio.clips
        # Just before B's clip starts, A still owns the Canvas; from that
        # instant on, B does -- even though A's audio is still playing.
        self.assertEqual(plan.presentation.track_at(clip_b.timeline_start - 0.01), "a")
        self.assertEqual(plan.presentation.track_at(clip_b.timeline_start), "b")
        self.assertEqual(plan.presentation.track_at(clip_a.timeline_end - 0.01), "b")

    def test_metadata_chapter_starts_equal_presentation_window_starts(self) -> None:
        tracks = [_track("a", 60.0), _track("b", 60.0)]
        analyses = {"a": _analysis("a", 120.0, 60.0), "b": _analysis("b", 124.0, 60.0)}
        plan = compile_automix(tracks, analyses, ENABLED)
        for chapter, window in zip(plan.metadata.chapters, plan.presentation.windows):
            self.assertEqual(chapter.track_id, window.track_id)
            self.assertEqual(chapter.start, window.timeline_start)

    def test_legacy_sequential_compiler_is_unaffected(self) -> None:
        tracks = [_track("a", 60.0), _track("b", 60.0)]
        before = compile_playlist(tracks)
        compile_automix(tracks, {"a": _analysis("a", 120.0, 60.0), "b": _analysis("b", 120.0, 60.0)}, ENABLED)
        after = compile_playlist(tracks)
        self.assertEqual(before, after)


class CompileAutomixThreeTrackTests(unittest.TestCase):
    def test_three_track_accumulation_positions_correctly(self) -> None:
        tracks = [_track("a", 60.0), _track("b", 60.0), _track("c", 60.0)]
        analyses = {
            "a": _analysis("a", 120.0, 60.0), "b": _analysis("b", 120.0, 60.0),
            "c": _analysis("c", 120.0, 60.0),
        }
        plan = compile_automix(tracks, analyses, ENABLED)
        validate_compiled_render_plan(plan)
        clip_a, clip_b, clip_c = plan.audio.clips
        self.assertEqual(len(plan.audio.transitions), 2)
        # B's placement already reflects the A->B overlap; C's placement
        # must be computed against B's *actual* (overlap-shifted) end, not
        # against the original sequential 120.0 mark.
        self.assertLess(clip_b.timeline_start, clip_a.timeline_end)
        self.assertLess(clip_c.timeline_start, clip_b.timeline_end)
        self.assertGreater(clip_c.timeline_start, clip_b.timeline_start)
        self.assertEqual(plan.duration_seconds, clip_c.timeline_end)
        self.assertLess(plan.duration_seconds, 180.0)

    def test_missing_analysis_on_middle_track_only_that_pair_falls_back(self) -> None:
        tracks = [_track("a", 60.0), _track("b", 60.0), _track("c", 60.0)]
        analyses = {
            "a": _analysis("a", 120.0, 60.0),
            # "b" has no analysis: neither A->B nor B->C can beat-match,
            # but this must not disturb any other pair's own eligibility.
            "c": _analysis("c", 120.0, 60.0),
        }
        plan = compile_automix(tracks, analyses, ENABLED)
        clip_a, clip_b, clip_c = plan.audio.clips
        self.assertEqual(clip_b.timeline_start, clip_a.timeline_end)
        self.assertEqual(clip_c.timeline_start, clip_b.timeline_end)
        self.assertEqual(plan.audio.transitions, ())

    def test_one_bad_pair_does_not_invalidate_a_good_neighboring_pair(self) -> None:
        tracks = [_track("a", 60.0), _track("b", 60.0), _track("c", 60.0)]
        analyses = {
            "a": _analysis("a", 90.0, 60.0),
            "b": _analysis("b", 140.0, 60.0),  # incompatible with both neighbors
            "c": _analysis("c", 140.0, 60.0),  # compatible with b
        }
        plan = compile_automix(tracks, analyses, ENABLED)
        clip_a, clip_b, clip_c = plan.audio.clips
        self.assertEqual(len(plan.audio.transitions), 2)
        # a->b is tempo-incompatible: it degrades to a small fixed
        # crossfade, never a beat-matched one.
        self.assertNotEqual(plan.audio.transitions[0].type, TransitionType.BEAT_MATCH)
        self.assertLessEqual(plan.audio.transitions[0].duration, ENABLED.fallback_crossfade_seconds)
        # b->c is fully compatible and still gets a real (larger) overlap.
        self.assertEqual(plan.audio.transitions[1].type, TransitionType.BEAT_MATCH)
        self.assertLess(clip_c.timeline_start, clip_b.timeline_end)


class EffectiveBpmPropagationTests(unittest.TestCase):
    """Commit C: fixes the v1 simplification -- a chained transition must
    plan against the outgoing clip's actual (rate-adjusted) tempo, not its
    raw analyzed BPM."""

    def test_second_transition_uses_the_first_transitions_actual_rate(self) -> None:
        tracks = [_track("a", 60.0), _track("b", 60.0), _track("c", 60.0)]
        analyses = {
            "a": _analysis("a", 120.0, 60.0),
            "b": _analysis("b", 124.0, 60.0),
            "c": _analysis("c", 128.0, 60.0),
        }
        plan = compile_automix(tracks, analyses, ENABLED)
        validate_compiled_render_plan(plan)
        clip_a, clip_b, clip_c = plan.audio.clips

        self.assertEqual(clip_a.playback_rate, 1.0)
        self.assertAlmostEqual(clip_b.playback_rate, 120.0 / 124.0)
        b_actual_bpm = 124.0 * clip_b.playback_rate  # what B is really sounding at: ~120.0
        self.assertAlmostEqual(b_actual_bpm, 120.0, places=6)

        # The bug this fixes: without propagation, clip_c's rate would be
        # computed against B's *raw* 124.0 BPM instead of the ~120.0 it is
        # actually playing at by the time B->C happens.
        self.assertAlmostEqual(clip_c.playback_rate, b_actual_bpm / 128.0)
        self.assertNotAlmostEqual(clip_c.playback_rate, 124.0 / 128.0, places=4)

    def test_first_clip_in_a_chain_is_unaffected(self) -> None:
        """No prior transition exists for the first clip -- its own
        analyzed BPM is already its effective BPM, propagation is a no-op."""
        tracks = [_track("a", 60.0), _track("b", 60.0)]
        analyses = {"a": _analysis("a", 120.0, 60.0), "b": _analysis("b", 124.0, 60.0)}
        plan = compile_automix(tracks, analyses, ENABLED)
        clip_a, clip_b = plan.audio.clips
        self.assertAlmostEqual(clip_b.playback_rate, 120.0 / 124.0)


class TargetBpmUnificationTests(unittest.TestCase):
    def test_applied_clip_rate_matches_the_winning_candidates_own_rate(self) -> None:
        """Regression: planner.py used to recompute an independent rate
        formula instead of using the candidate it had just selected --
        this could silently diverge from what candidates.py scored."""
        outgoing = _analysis("a", 120.0, 60.0)
        incoming = _analysis("b", 124.0, 60.0)
        compatibility = evaluate_compatibility(outgoing, incoming, ENABLED)
        best = select_best_candidate(generate_candidates(outgoing, incoming, compatibility, ENABLED))
        self.assertIsNotNone(best)

        tracks = [_track("a", 60.0), _track("b", 60.0)]
        plan = compile_automix(tracks, {"a": outgoing, "b": incoming}, ENABLED)
        clip_b = plan.audio.clips[1]
        self.assertAlmostEqual(clip_b.playback_rate, best.incoming_rate)


def _structure(
    track_id: str, duration: float, *,
    outro_start: float | None = None, intro_end: float | None = None,
) -> TrackStructureAnalysis:
    return TrackStructureAnalysis(
        track_id=track_id, source_path=f"{track_id}.wav", duration_seconds=duration,
        outro_start_seconds=outro_start, intro_end_seconds=intro_end,
        analyzer_id="sonara_structure", analyzer_version="1",
    )


class CompileAutomixStructureTests(unittest.TestCase):
    """Commit C: structures is optional and additive; missing/malformed/
    one-sided structure data must never break the existing fallback chain."""

    def test_structures_omitted_matches_the_pre_commit_c_result(self) -> None:
        tracks = [_track("a", 60.0), _track("b", 60.0)]
        analyses = {"a": _analysis("a", 120.0, 60.0), "b": _analysis("b", 124.0, 60.0)}
        without_param = compile_automix(tracks, analyses, ENABLED)
        with_empty = compile_automix(tracks, analyses, ENABLED, structures={})
        with_none = compile_automix(tracks, analyses, ENABLED, structures=None)
        self.assertEqual(without_param, with_empty)
        self.assertEqual(without_param, with_none)

    def test_structure_data_can_shift_the_transition_anchor(self) -> None:
        tracks = [_track("a", 200.0), _track("b", 200.0)]
        analyses = {"a": _analysis("a", 120.0, 200.0), "b": _analysis("b", 120.0, 200.0)}
        without_structure = compile_automix(tracks, analyses, ENABLED)
        structures = {"a": _structure("a", 200.0, outro_start=150.0)}
        with_structure = compile_automix(tracks, analyses, ENABLED, structures=structures)
        validate_compiled_render_plan(with_structure)
        # A real (if modest) shift -- the structure anchor is close enough
        # to the tail to plausibly win once snapped to a downbeat, but
        # this asserts the plan is genuinely different, not identical.
        self.assertNotEqual(
            without_structure.audio.transitions[0].timeline_start,
            with_structure.audio.transitions[0].timeline_start,
        )

    def test_one_sided_structure_data_does_not_break_the_pair(self) -> None:
        tracks = [_track("a", 60.0), _track("b", 60.0)]
        analyses = {"a": _analysis("a", 120.0, 60.0), "b": _analysis("b", 120.0, 60.0)}
        structures = {"a": _structure("a", 60.0, outro_start=45.0)}  # "b" has none
        plan = compile_automix(tracks, analyses, ENABLED, structures=structures)
        validate_compiled_render_plan(plan)
        self.assertEqual(len(plan.audio.transitions), 1)

    def test_structure_present_for_a_track_missing_from_analyses_is_harmless(self) -> None:
        """A structures entry with no matching rhythm analysis must not
        crash -- that pair simply falls back exactly as it already does
        when rhythm analysis alone is missing."""
        tracks = [_track("a", 60.0), _track("b", 60.0)]
        analyses = {"a": _analysis("a", 120.0, 60.0)}  # "b" has no rhythm analysis
        structures = {"b": _structure("b", 60.0, intro_end=5.0)}
        plan = compile_automix(tracks, analyses, ENABLED, structures=structures)
        clip_a, clip_b = plan.audio.clips
        self.assertEqual(clip_b.timeline_start, clip_a.timeline_end)
        self.assertEqual(plan.audio.transitions, ())

    def test_incompatible_tempo_still_falls_back_regardless_of_structure(self) -> None:
        """Structure-aware scoring must never resurrect a pair that
        compatibility already rejected -- the fallback chain (roadmap:
        Structure-aware Beat Match -> Beat Match -> Beat-aligned Crossfade
        -> Fixed Crossfade -> Sequential) still starts from the same
        compatibility gate as before."""
        tracks = [_track("a", 60.0), _track("b", 60.0)]
        analyses = {"a": _analysis("a", 90.0, 60.0), "b": _analysis("b", 140.0, 60.0)}
        structures = {
            "a": _structure("a", 60.0, outro_start=45.0),
            "b": _structure("b", 60.0, intro_end=5.0),
        }
        plan = compile_automix(tracks, analyses, ENABLED, structures=structures)
        clip_a, clip_b = plan.audio.clips
        self.assertEqual(clip_a.playback_rate, 1.0)
        self.assertEqual(clip_b.playback_rate, 1.0)
        for transition in plan.audio.transitions:
            self.assertNotEqual(transition.type, TransitionType.BEAT_MATCH)


class SingleTrackAndEdgeCaseTests(unittest.TestCase):
    def test_single_track_has_no_transitions(self) -> None:
        plan = compile_automix([_track("a", 60.0)], {"a": _analysis("a", 120.0, 60.0)}, ENABLED)
        self.assertEqual(len(plan.audio.clips), 1)
        self.assertEqual(plan.audio.transitions, ())
        self.assertEqual(plan.duration_seconds, 60.0)

    def test_empty_playlist_is_empty_plan(self) -> None:
        plan = compile_automix([], {}, ENABLED)
        self.assertEqual(plan.audio.clips, ())
        self.assertEqual(plan.duration_seconds, 0.0)

    def test_very_short_tracks_fall_back_without_crashing(self) -> None:
        tracks = [_track("a", 1.0), _track("b", 1.0)]
        analyses = {"a": _analysis("a", 120.0, 1.0), "b": _analysis("b", 120.0, 1.0)}
        plan = compile_automix(tracks, analyses, ENABLED)
        validate_compiled_render_plan(plan)


if __name__ == "__main__":
    unittest.main()
