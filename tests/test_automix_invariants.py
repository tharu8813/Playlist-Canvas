"""Generated AutoMix planner/override cases: invariants that must hold for any input.

Deterministic (fixed seeds), so a failure names its seed; a crash found here is
pinned as its own regression test, not left to the random corpus.
"""
from __future__ import annotations

import json
import math
import random
import unittest

from app.automix.overrides import (
    EQ_BANDS, MANUAL_STYLES, MIN_EQ_WINDOW, STYLE_EQ,
    TransitionOverride, pair_key, parse_overrides, serialize_overrides,
)
from app.automix.planner import compile_automix
from app.timeline.render_plan import TransitionDsp
from tests.test_automix_planner import ENABLED, _analysis, _track

CASES = 300
EPSILON = 1e-6
# "a>b" but no "b>c": pair_key("a>b", "c") == pair_key("a", "b>c"). Real ids are
# uuid4 and never contain ">", so that collision is a documented limit, not a case.
_ODD_IDS = ("a>b", "a", "곡 ♪", " spaced id ", "x" * 300, ">lead", "trail>", "c")


def _window(rng: random.Random) -> tuple[float, float]:
    start = rng.uniform(0.0, 1.0 - MIN_EQ_WINDOW)
    return start, rng.uniform(start + MIN_EQ_WINDOW, 1.0)


def _override(rng: random.Random, out_duration: float, in_duration: float) -> TransitionOverride:
    style = rng.choice(MANUAL_STYLES)
    return TransitionOverride(
        outgoing_cue=rng.uniform(0.0, max(out_duration, 1.0) * 1.2),  # may pass the track's end
        incoming_cue=rng.uniform(0.0, max(in_duration, 1.0) * 1.2),
        duration=rng.choice((0.0, rng.uniform(0.0, 60.0), 60.0)),
        style=style,
        tempo_match=rng.random() < 0.5,
        vocal_handoff=rng.choice((None, rng.uniform(0.1, 0.9))),
        eq_bands=tuple((_window(rng), _window(rng)) for _ in EQ_BANDS) if style == STYLE_EQ else None,
    )


def _case(seed: int):
    rng = random.Random(seed)
    ids = rng.sample(_ODD_IDS, rng.randint(2, 6))
    tracks = [
        _track(
            track_id,
            rng.choice((0.0, 0.4, 3.0)) if rng.random() < 0.15 else rng.uniform(20.0, 400.0),
            enabled=rng.random() > 0.15,
        )
        for track_id in ids
    ]
    tempo = rng.uniform(80.0, 170.0)  # mostly near one tempo, so beat matching and ramps happen
    analyses = {
        track.id: _analysis(track.id, rng.choice((
            None, 30.0, 300.0, rng.uniform(60.0, 200.0), tempo, tempo * rng.uniform(0.95, 1.05),
        )), track.duration_seconds)
        for track in tracks if track.duration_seconds >= 1.0 and rng.random() > 0.1
    }
    enabled = [track for track in tracks if track.enabled]
    overrides = {
        pair_key(outgoing.id, incoming.id): _override(rng, outgoing.duration_seconds, incoming.duration_seconds)
        for outgoing, incoming in zip(enabled, enabled[1:]) if rng.random() < 0.5
    }
    return tracks, analyses, overrides


class PlannerInvariantTests(unittest.TestCase):
    def assert_plan_invariants(self, seed: int, tracks, plan) -> None:
        enabled = [track for track in tracks if track.enabled and track.duration_seconds > 0.0]
        clips = plan.audio.clips
        self.assertEqual([clip.track_id for clip in clips], [track.id for track in enabled], seed)
        durations = {track.id: track.duration_seconds for track in enabled}
        for previous, clip in zip(clips, clips[1:]):
            self.assertGreaterEqual(clip.timeline_start, previous.timeline_start - EPSILON, seed)
        # An echo out may ring on past its file's end (the renderer pads that clip with silence).
        echoes = {transition.clip_a for transition in plan.audio.transitions
                  if transition.dsp is TransitionDsp.ECHO_OUT}
        for clip in clips:
            self.assertGreaterEqual(clip.source_in, -EPSILON, seed)
            if clip.clip_id not in echoes:
                self.assertLessEqual(clip.source_out, durations[clip.track_id] + EPSILON, seed)
        index = {clip.clip_id: position for position, clip in enumerate(clips)}
        for transition in plan.audio.transitions:
            a, b = index[transition.clip_a], index[transition.clip_b]
            self.assertEqual(b, a + 1, seed)  # only neighbours mix
            end = transition.timeline_start + transition.duration
            self.assertTrue(math.isfinite(end), seed)
            self.assertGreaterEqual(transition.timeline_start, clips[b].timeline_start - EPSILON, seed)
            self.assertLessEqual(end, clips[a].timeline_end + EPSILON, seed)

    def test_generated_playlists_always_compile_to_a_valid_plan(self) -> None:
        for seed in range(CASES):
            tracks, analyses, overrides = _case(seed)
            settings = ENABLED.with_overrides(overrides)
            with self.subTest(seed=seed):
                plan = compile_automix(tracks, analyses, settings, log_diagnostics=False)
                self.assert_plan_invariants(seed, tracks, plan)
                # Preview and Export plan separately; the same input must give the same plan.
                self.assertEqual(compile_automix(tracks, analyses, settings, log_diagnostics=False), plan)

    def test_overrides_for_pairs_that_are_not_neighbours_change_nothing(self) -> None:
        for seed in range(0, CASES, 3):
            tracks, analyses, _overrides = _case(seed)
            enabled = [track for track in tracks if track.enabled]
            stale = {
                pair_key(incoming.id, outgoing.id): TransitionOverride(1.0, style="cut")
                for outgoing, incoming in zip(enabled, enabled[1:])
            }
            with self.subTest(seed=seed):
                self.assertEqual(
                    compile_automix(tracks, analyses, ENABLED.with_overrides(stale), log_diagnostics=False),
                    compile_automix(tracks, analyses, ENABLED, log_diagnostics=False),
                )


    def test_zero_length_track_is_skipped_not_a_validation_error(self) -> None:
        # Found by the generated cases (seed 0): two presentation windows shared
        # one start, so partial Preview plans raised inside a Qt slot.
        tracks = [_track("a", 120.0), _track("b", 0.0), _track("c", 120.0)]
        analyses = {track_id: _analysis(track_id, 120.0, 120.0) for track_id in ("a", "c")}
        for settings in (ENABLED, ENABLED.with_overrides({pair_key("a", "c"): TransitionOverride(100.0)})):
            plan = compile_automix(tracks, analyses, settings, log_diagnostics=False)
            self.assertEqual([clip.track_id for clip in plan.audio.clips], ["a", "c"])
            self.assertEqual(len(plan.audio.transitions), 1)


class OverrideSerializationInvariantTests(unittest.TestCase):
    def test_serialize_parse_round_trip_is_canonical(self) -> None:
        for seed in range(CASES):
            _tracks, _analyses, overrides = _case(seed)
            with self.subTest(seed=seed):
                saved = json.loads(json.dumps(serialize_overrides(overrides)))
                parsed = parse_overrides(saved)
                self.assertEqual(parsed, overrides)
                self.assertEqual(serialize_overrides(parsed), saved)

    def test_hostile_values_are_dropped_never_raised(self) -> None:
        rng = random.Random(7)
        hostile = (float("nan"), float("inf"), -float("inf"), 10 ** 400, -1, True, False, None,
                   "12", [], {}, [1, 2], {"out": [0, 1]}, 1e308, -0.0)
        fields = ("outgoing_cue", "incoming_cue", "duration", "style", "tempo_match", "vocal_handoff", "eq")
        for _ in range(500):
            entry = TransitionOverride(10.0).to_dict()
            for field in rng.sample(fields, rng.randint(1, 3)):
                entry[field] = rng.choice(hostile)
            key = rng.choice((None, 1, 1.5, True, "", ">", "a>", ">b", "a>b>c"))
            parsed = parse_overrides({"a>b": entry, key: entry})
            for override in parsed.values():  # whatever survives is fully valid
                self.assertEqual(TransitionOverride.from_dict(override.to_dict()), override)


if __name__ == "__main__":
    unittest.main()
