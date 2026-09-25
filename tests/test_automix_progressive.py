from __future__ import annotations

import math
import unittest

from app.automix.planner import compile_automix
from app.automix.progressive import (
    RENDER_DEBOUNCE_SECONDS,
    Action,
    ProgressiveAnalysis,
    RenderScheduler,
    divergence_seconds,
    partial_plan,
    preview_gain,
    render_prefix,
    swap_playhead,
)
from tests.test_automix_planner import ENABLED, _analysis, _track

TRACK_SECONDS = 240.0


def _playlist(count: int):
    tracks = [_track(f"t{index}", TRACK_SECONDS) for index in range(count)]
    analyses = {track.id: _analysis(track.id, 120.0, TRACK_SECONDS) for track in tracks}
    return tracks, analyses


def _state(tracks, analyses, analyzed: int) -> ProgressiveAnalysis:
    state = ProgressiveAnalysis(tracks, structure_enabled=False)
    for track in tracks[:analyzed]:
        state.record_rhythm(track.id, analyses[track.id])
    return state


class PartialPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tracks, self.analyses = _playlist(5)

    def plan(self, analyzed: int):
        return partial_plan(self.tracks, _state(self.tracks, self.analyses, analyzed), ENABLED)

    def test_two_analyzed_tracks_mix_only_the_first_pair(self) -> None:
        plan = self.plan(2)
        self.assertEqual([(t.clip_a, t.clip_b) for t in plan.audio.transitions], [("automix:t0", "automix:t1")])
        # The unanalyzed tail is plain sequential adjacency after t1's natural end.
        clips = plan.audio.clips
        for previous, clip in zip(clips[1:], clips[2:]):
            self.assertAlmostEqual(clip.timeline_start, previous.timeline_end)
            self.assertEqual((clip.source_in, clip.playback_rate), (0.0, 1.0))

    def test_each_new_track_appends_a_transition_without_moving_earlier_ones(self) -> None:
        previous = self.plan(2)
        for analyzed in (3, 4, 5):
            plan = self.plan(analyzed)
            self.assertEqual(plan.audio.transitions[:len(previous.audio.transitions)], previous.audio.transitions)
            self.assertEqual(len(plan.audio.transitions), analyzed - 1)
            previous = plan

    def test_fully_analyzed_partial_plan_is_exactly_the_full_compile(self) -> None:
        self.assertEqual(self.plan(5), compile_automix(self.tracks, self.analyses, ENABLED))

    def test_frontier_waits_for_structure_when_it_is_enabled(self) -> None:
        state = ProgressiveAnalysis(self.tracks, structure_enabled=True)
        state.record_rhythm("t0", self.analyses["t0"])
        state.record_rhythm("t1", self.analyses["t1"])
        state.record_structure("t0", None)  # a failed structure still counts as done
        self.assertEqual(state.frontier(), 1)
        state.record_structure("t1", None)
        self.assertEqual(state.frontier(), 2)

    def test_a_gap_in_analysis_stops_the_frontier(self) -> None:
        state = _state(self.tracks, self.analyses, 1)
        state.record_rhythm("t2", self.analyses["t2"])  # t1 still running
        self.assertEqual(state.frontier(), 1)
        self.assertEqual(state.completed_count(), 2)


class DivergenceAndSwapSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        tracks, analyses = _playlist(4)
        self.two = partial_plan(tracks, _state(tracks, analyses, 2), ENABLED)
        self.three = partial_plan(tracks, _state(tracks, analyses, 3), ENABLED)
        self.new_transition = self.three.audio.transitions[1]

    def test_divergence_is_the_new_transition_start(self) -> None:
        self.assertAlmostEqual(divergence_seconds(self.two, self.three), self.new_transition.timeline_start)
        self.assertEqual(divergence_seconds(self.three, self.three), math.inf)

    def test_swap_keeps_the_playhead_before_the_change_point(self) -> None:
        change = self.new_transition.timeline_start
        self.assertEqual(swap_playhead(self.two, self.three, change - 10.0, playing=True), change - 10.0)
        self.assertIsNone(swap_playhead(self.two, self.three, change - 1.0, playing=True))   # inside the margin
        self.assertIsNone(swap_playhead(self.two, self.three, change + 5.0, playing=True))   # t1 tail now overlaps t2

    def test_no_swap_while_a_transition_is_sounding(self) -> None:
        first = self.two.audio.transitions[0]
        inside = first.timeline_start + first.duration / 2
        self.assertIsNone(swap_playhead(self.two, self.three, inside, playing=True))
        self.assertIsNone(swap_playhead(self.two, self.three, first.timeline_start - 1.0, playing=True))
        after = first.timeline_start + first.duration + 1.0
        self.assertEqual(swap_playhead(self.two, self.three, after, playing=True), after)
        # Paused, nothing is sounding.
        self.assertEqual(swap_playhead(self.two, self.three, inside, playing=False), inside)

    def test_a_listener_past_the_change_point_keeps_the_same_music_on_the_new_timeline(self) -> None:
        # Listener outran analysis: 30 s into t3, sequential in ``two``.
        t3_old = self.two.audio.clips[3]
        playhead = t3_old.timeline_start + 30.0
        target = swap_playhead(self.two, self.three, playhead, playing=True)
        t3_new = self.three.audio.clips[3]
        self.assertIsNotNone(target)
        self.assertLess(target, playhead)  # AutoMix overlaps pull t3 earlier
        self.assertAlmostEqual(t3_new.source_in + (target - t3_new.timeline_start) * t3_new.playback_rate,
                               t3_old.source_in + 30.0 * t3_old.playback_rate)  # same source second of t3


class RenderPrefixAndGainTests(unittest.TestCase):
    def test_render_prefix_covers_the_analyzed_clips_at_the_preview_gain(self) -> None:
        tracks, analyses = _playlist(4)
        plan = partial_plan(tracks, _state(tracks, analyses, 2), ENABLED)
        render, covered_until = render_prefix(plan, 2, 0.5)
        self.assertEqual(len(render.clips), 2)
        self.assertEqual(render.transitions, plan.audio.transitions)
        self.assertTrue(all(clip.gain == 0.5 for clip in render.clips))
        self.assertAlmostEqual(covered_until, plan.audio.clips[1].timeline_end)
        self.assertAlmostEqual(covered_until, plan.audio.clips[2].timeline_start)  # per-track takes over here

    def test_preview_gain_targets_export_loudness_and_peak_ceiling(self) -> None:
        durations = {"a": 100.0, "b": 100.0}
        self.assertAlmostEqual(20 * math.log10(preview_gain({"a": (-10.0, -8.0), "b": (-10.0, -8.0)}, durations)), -6.0)
        # A hot peak caps the gain like linear loudnorm's true-peak limit would.
        self.assertAlmostEqual(20 * math.log10(preview_gain({"a": (-20.0, -1.0)}, {"a": 100.0})), -0.5)
        self.assertEqual(preview_gain({"a": (-math.inf, -math.inf)}, {"a": 1.0}), 1.0)


def _unrendered_scheduler(plan, frontier=2, now=0.0) -> RenderScheduler:
    scheduler = RenderScheduler()
    scheduler.playhead_changed(0.0, True)
    scheduler.plan_updated(plan, frontier, now)
    return scheduler


class RenderSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tracks, self.analyses = _playlist(6)
        self.plans = {n: partial_plan(self.tracks, _state(self.tracks, self.analyses, n), ENABLED) for n in range(7)}

    def test_first_upcoming_transition_renders_after_the_debounce(self) -> None:
        scheduler = _unrendered_scheduler(self.plans[2])
        self.assertIs(scheduler.decide(0.1), Action.NONE)
        self.assertAlmostEqual(scheduler.wake_after(0.1), RENDER_DEBOUNCE_SECONDS - 0.1)
        self.assertIs(scheduler.decide(RENDER_DEBOUNCE_SECONDS), Action.RENDER)

    def test_burst_of_analysis_events_coalesces_into_one_render_of_the_latest_plan(self) -> None:
        scheduler = _unrendered_scheduler(self.plans[2])
        for index, analyzed in enumerate((3, 4, 5), start=1):
            scheduler.plan_updated(self.plans[analyzed], analyzed, 0.1 * index)
            self.assertIs(scheduler.decide(0.1 * index), Action.NONE)
        self.assertIs(scheduler.decide(0.3 + RENDER_DEBOUNCE_SECONDS), Action.RENDER)
        scheduler.render_started()
        self.assertEqual(scheduler.rendered_frontier, 5)
        scheduler.plan_updated(self.plans[6], 6, 1.0)
        self.assertIs(scheduler.decide(5.0), Action.NONE)  # one render at a time

    def test_nothing_renders_before_preview_attaches(self) -> None:
        scheduler = RenderScheduler()
        scheduler.plan_updated(self.plans[3], 3, 0.0)
        self.assertIs(scheduler.decide(10.0), Action.NONE)

    def test_rendered_audio_ahead_of_the_listener_defers_the_next_render(self) -> None:
        scheduler = _unrendered_scheduler(self.plans[3])
        scheduler.render_started()
        scheduler.render_finished()
        scheduler.plan_updated(self.plans[4], 4, 1.0)
        self.assertIs(scheduler.decide(10.0), Action.NONE)  # t0->t1 and t1->t2 still ahead, rendered
        change = divergence_seconds(self.plans[3], self.plans[4])
        scheduler.playhead_changed(change - 30.0, True)
        self.assertIs(scheduler.decide(10.0), Action.RENDER)

    def test_a_listener_past_the_change_point_triggers_no_useless_render(self) -> None:
        scheduler = _unrendered_scheduler(self.plans[3])
        scheduler.render_started()
        scheduler.render_finished()
        scheduler.plan_updated(self.plans[4], 4, 1.0)
        scheduler.playhead_changed(divergence_seconds(self.plans[3], self.plans[4]) + 1.0, True)
        self.assertIs(scheduler.decide(10.0), Action.NONE)

    def test_a_listener_past_one_change_still_gets_the_next_junction_rendered(self) -> None:
        scheduler = _unrendered_scheduler(self.plans[3])
        scheduler.render_started()
        scheduler.render_finished()
        scheduler.playhead_changed(self.plans[3].audio.clips[3].timeline_start + 30.0, True)  # inside t3
        scheduler.plan_updated(self.plans[5], 5, 1.0)  # t2->t3 is behind, t3->t4 ahead
        self.assertIs(scheduler.decide(10.0), Action.RENDER)

    def test_complete_analysis_goes_straight_to_the_final_render(self) -> None:
        scheduler = _unrendered_scheduler(self.plans[6], 6)
        scheduler.analysis_complete = True  # e.g. a fully cached playlist
        self.assertIs(scheduler.decide(0.0), Action.FINAL)
        scheduler.render_started(final=True)
        self.assertIs(scheduler.decide(1.0), Action.NONE)

    def test_final_waits_for_a_running_partial_render(self) -> None:
        scheduler = _unrendered_scheduler(self.plans[2])
        scheduler.render_started()
        scheduler.analysis_complete = True
        self.assertIs(scheduler.decide(1.0), Action.NONE)
        scheduler.render_finished()
        self.assertIs(scheduler.decide(1.0), Action.FINAL)


def simulate(count: int, *, analysis_seconds: float = 15.0, workers: int = 4,
             render_speed: float = 150.0) -> dict[str, int]:
    """Real planner + scheduler + swap rule against a simulated clock.

    Analysis: ``workers`` tracks in parallel, ``analysis_seconds`` each (a
    cache-miss Beat This + Sonara pass). Rendering: a partial mix renders at
    ``render_speed`` x realtime of the audio it covers. Playback starts at
    0 and runs in real time; swaps follow ``swap_playhead``.
    """
    tracks, analyses = _playlist(count)
    state = ProgressiveAnalysis(tracks, structure_enabled=False)
    scheduler = RenderScheduler()
    completions = sorted((analysis_seconds * (index // workers + 1), index) for index in range(count))
    committed = partial_plan(tracks, state, ENABLED)
    pending = None
    render_done_at = None
    render_plan = None
    counts = {"analysis_events": 0, "plan_updates": 0, "renders": 0, "swaps": 0}
    frontier = 0
    now, step = 0.0, 0.25
    playhead = 0.0
    scheduler.playhead_changed(0.0, True)
    while True:
        while completions and completions[0][0] <= now:
            _, index = completions.pop(0)
            state.record_rhythm(tracks[index].id, analyses[tracks[index].id])
            counts["analysis_events"] += 1
            if state.frontier() != frontier:
                frontier = state.frontier()
                counts["plan_updates"] += 1
                scheduler.plan_updated(partial_plan(tracks, state, ENABLED), frontier, now)
            scheduler.analysis_complete = state.complete()
        if render_done_at is not None and now >= render_done_at:
            scheduler.render_finished()
            render_done_at = None
            if render_plan is None:  # the final export mix
                break
            pending = render_plan
        if pending is not None and (target := swap_playhead(committed, pending, playhead, True)) is not None:
            committed, pending, playhead = pending, None, target
            counts["swaps"] += 1
        scheduler.playhead_changed(playhead, True)
        action = scheduler.decide(now)
        if action is Action.RENDER:
            scheduler.render_started()
            counts["renders"] += 1
            render_plan = scheduler.rendered
            covered = render_prefix(render_plan, scheduler.rendered_frontier, 1.0)[1]
            render_done_at = now + covered / render_speed
        elif action is Action.FINAL:
            scheduler.render_started(final=True)
            counts["renders"] += 1
            render_plan = None
            render_done_at = now + committed.duration_seconds / render_speed * 3  # mix + 2-pass loudnorm
        now += step
        playhead += step
    counts["swaps"] += 1  # the final mix
    return counts


class ProgressiveChurnTests(unittest.TestCase):
    def test_renders_do_not_scale_with_analysis_events(self) -> None:
        for count in (10, 20, 50):
            with self.subTest(count=count):
                counts = simulate(count)
                self.assertEqual(counts["analysis_events"], count)
                self.assertLessEqual(counts["plan_updates"], count)  # planning is cheap; only renders are gated
                self.assertLessEqual(counts["renders"], 4)
                self.assertLessEqual(counts["swaps"], counts["renders"])

    def test_fully_cached_playlist_renders_only_the_final_mix(self) -> None:
        counts = simulate(20, analysis_seconds=0.0)
        self.assertEqual(counts, {"analysis_events": 20, "plan_updates": 20, "renders": 1, "swaps": 1})


if __name__ == "__main__":
    unittest.main()
