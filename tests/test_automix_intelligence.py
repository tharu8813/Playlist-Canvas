"""Musical outcomes of measured cue/EQ search, including real rendering."""
from __future__ import annotations

import os
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np

from app.automix.analysis.basic import BasicAnalysisProvider, SAMPLE_RATE
from app.automix.candidates import generate_candidates, incoming_landmarks
from app.automix.compatibility import evaluate_compatibility
from app.automix.models import TrackAnalysis
from app.automix.overrides import TransitionOverride
from app.automix.planner import compile_automix
from app.automix.renderer import AutoMixAudioPipeline, band_windows_of, build_filter_graph
from app.timeline.render_plan import AudioRenderClip, AudioRenderPlan, TempoRamp, TransitionDsp
from app.timeline.models import TransitionType
from tests.test_automix_planner import ENABLED, _analysis, _track


def _profile(track_id: str, **fields) -> TrackAnalysis:
    return replace(_analysis(track_id, 120.0, 60.0), rms_curve=(0.2,) * 60,
                   bass_curve=(0.7,) * 60, brightness_curve=(0.1,) * 60,
                   percussive_curve=(0.9,) * 60, vocal_coverage=((0.0, 60.0),), **fields)


def _plan(outgoing, incoming, settings=ENABLED):
    return compile_automix([_track("a", 60.0), _track("b", 60.0)],
                           {"a": outgoing, "b": incoming}, settings, log_diagnostics=False)


class AcousticPlanningTests(unittest.TestCase):
    def test_trimming_into_a_ramp_keeps_the_original_rates_and_audition_cue(self):
        from tools.automix_listening_report import excerpt_plan
        from app.timeline.render_plan import CompiledRenderPlan, AudioRenderTransition, build_presentation_and_metadata

        original = AudioRenderClip("a", "a", 0.0, 0.0, 60.0, tempo_ramp=TempoRamp(30.0, 46.0, 1.08))
        cue_time = original.timeline_at(46.0)
        incoming = AudioRenderClip("b", "b", cue_time, 0.0, 60.0)
        transition = AudioRenderTransition("a", "b", cue_time, original.timeline_end - cue_time,
                                           TransitionType.BEAT_MATCH, TransitionDsp.BASS_SWAP)
        presentation, metadata, duration = build_presentation_and_metadata((original, incoming))
        plan = CompiledRenderPlan(audio=AudioRenderPlan((original, incoming), (transition,)),
                                  presentation=presentation, metadata=metadata, duration_seconds=duration)
        excerpt, lead = excerpt_plan(plan, transition, "bass_swap")
        clipped = excerpt.clips[0]
        self.assertAlmostEqual(lead, 8.0)
        self.assertAlmostEqual(clipped.source_at(lead), 46.0)
        for point in np.linspace(0.0, clipped.duration, 30):
            self.assertAlmostEqual(clipped.source_at(float(point)), original.source_at(cue_time - lead + float(point)))

    def test_excerpt_after_a_ramp_holds_rate_and_pitch_without_an_empty_command_filter(self):
        clip = AudioRenderClip("a", "a", 0.0, 20.0, 30.0,
                               tempo_ramp=TempoRamp(5.0, 15.0, 1.1, end_pitch=1.06))
        graph, _ = build_filter_graph((clip,), ())
        self.assertNotIn("asendcmd=c=''", graph)
        self.assertIn("tempo=1.100000:pitch=1.060000", graph)

    def test_measurements_are_extracted_without_structure_model_and_cached(self):
        t = np.arange(4 * SAMPLE_RATE) / SAMPLE_RATE
        signal = (0.3 * np.sin(2 * np.pi * 60 * t)).astype(np.float32)
        provider = BasicAnalysisProvider(Path("ffmpeg"))
        with patch("app.automix.analysis.basic.analyze_music", return_value=()):
            analysis = provider.analyze_signal(_track("a", 4.0), signal,
                                               cancel_event=threading.Event(), rhythm=False)
        self.assertEqual(len(analysis.rms_curve), 4)
        self.assertGreater(min(analysis.bass_curve), 0.9)
        self.assertLess(max(analysis.percussive_curve), 0.2)
        self.assertEqual(TrackAnalysis.from_cache_fields("a", "a.mp3", analysis.to_cache_fields()), analysis)
        for field, bad in (("rms_curve", float("nan")), ("bass_curve", 1.1), ("percussive_curve", -0.1)):
            with self.assertRaises(ValueError):
                replace(analysis, **{field: (bad,)})

    def test_blend_lands_on_the_first_vocal_without_skipping_its_words(self):
        incoming = _profile("b", vocal_activity=((12.0, 60.0),))
        plan = _plan(_profile("a"), incoming)
        transition = plan.audio.transitions[0]
        self.assertAlmostEqual(plan.audio.clips[1].source_in + transition.duration, 12.0)
        self.assertAlmostEqual(plan.audio.clips[1].source_in, 0.0)
        self.assertAlmostEqual(dict(transition.details)["obscured_vocal_seconds"], 0.0)
        self.assertEqual(plan.audio.clips[0].source_out, 60.0)

    def test_vocal_pickup_before_the_first_downbeat_can_choose_a_natural_tempo_handoff(self):
        incoming = _profile("b", vocal_activity=((0.0, 60.0),))
        incoming = replace(incoming, beats=tuple(b for b in incoming.beats if b >= 2.0),
                           downbeats=tuple(b for b in incoming.downbeats if b >= 2.0))
        plan = _plan(_profile("a", vocal_activity=((40.0, 60.0),)), incoming)
        self.assertAlmostEqual(plan.audio.clips[1].source_in, 0.0)
        self.assertEqual(dict(plan.audio.transitions[0].details)["skipped_vocal_seconds"], 0.0)

    def test_a_short_handoff_competes_even_when_a_long_blend_fits(self):
        outgoing, incoming = _profile("a"), _profile("b")
        candidates = generate_candidates(outgoing, incoming, evaluate_compatibility(outgoing, incoming, ENABLED), ENABLED)
        self.assertTrue(any(c.duration_seconds < 5.0 for c in candidates))
        self.assertTrue(any(c.duration_seconds > 10.0 for c in candidates))

    def test_incompatible_tempos_still_compare_multiple_mixing_lengths(self):
        a, b = replace(_profile("a"), bpm=100.0), replace(_profile("b"), bpm=140.0)
        candidates = generate_candidates(a, b, evaluate_compatibility(a, b, ENABLED), ENABLED)
        self.assertGreaterEqual(len({c.duration_seconds for c in candidates if c.duration_seconds > 0}), 4)

    def test_unknown_shared_tempo_does_not_echo_away_the_last_words(self):
        a = replace(_profile("a", vocal_activity=((40.0, 60.0),)), bpm=100.0)
        b = replace(_profile("b", vocal_activity=((0.0, 60.0),)), bpm=140.0)
        plan = _plan(a, b)
        self.assertNotIn(plan.audio.transitions[0].dsp, (TransitionDsp.ECHO_OUT, TransitionDsp.TAPE_STOP))
        self.assertEqual(plan.audio.clips[0].source_out, 60.0)

    def test_two_singers_get_a_brief_handoff_instead_of_a_long_overlay(self):
        transition = _plan(_profile("a", vocal_activity=((40.0, 60.0),)),
                           _profile("b", vocal_activity=((0.0, 60.0),))).audio.transitions[0]
        self.assertLessEqual(transition.duration, 4.0 + 1e-6)
        self.assertLess(dict(transition.details)["voice_collision_seconds"], 1.0)

    def test_band_handoffs_follow_the_actual_audio_instead_of_a_fixed_template(self):
        incoming = replace(_profile("b", vocal_activity=((2.0, 60.0),)), rms_curve=(0.1,) * 8 + (0.2,) * 52,
                           bass_curve=(0.02,) * 8 + (0.7,) * 52,
                           percussive_curve=(0.0,) * 8 + (0.9,) * 52)
        transition = _plan(_profile("a"), incoming).audio.transitions[0]
        windows = band_windows_of(transition)
        self.assertIsNotNone(transition.band_windows)
        self.assertNotEqual(windows[0], windows[1])
        self.assertGreater(windows[0][1][0] * transition.duration, 6.0)
        self.assertLess(dict(transition.details)["predicted_hole_db"], 1.0)
        self.assertIn(8.0, incoming_landmarks(incoming))

    def test_unknown_vocals_and_clashing_keys_keep_voice_protection(self):
        for fields in ({"vocal_coverage": ()}, {"key": "F# major", "key_confidence": 0.9}):
            outgoing = _profile("a", key="C major", key_confidence=0.9)
            if "vocal_coverage" in fields:
                outgoing = replace(outgoing, vocal_coverage=())
            transition = _plan(outgoing, replace(_profile("b"), **fields)).audio.transitions[0]
            if transition.duration >= 4.0:
                self.assertIs(transition.dsp, TransitionDsp.VOCAL_SAFE_EQ)
                self.assertLessEqual(transition.band_windows[1][0][1] - transition.band_windows[1][0][0], 0.25)

    def test_search_is_deterministic_and_manual_edits_stay_exact(self):
        a, b = _profile("a"), _profile("b")
        self.assertEqual(_plan(a, b), _plan(a, b))
        override = TransitionOverride(outgoing_cue=50.0, incoming_cue=2.0, duration=6.0,
                                      style="short_fade", tempo_match=False)
        manual = _plan(a, b, ENABLED.with_overrides({"a>b": override}))
        transition = manual.audio.transitions[0]
        self.assertEqual(transition.timeline_start, 50.0)
        self.assertEqual(transition.duration, 6.0)
        self.assertEqual(manual.audio.clips[1].source_in, 2.0)
        self.assertIsNone(transition.band_windows)
        self.assertNotIn("acoustic_cost", dict(transition.details))

    def test_measured_playlists_with_short_tracks_and_overrides_keep_plan_invariants(self):
        from tests.test_automix_invariants import _case, PlannerInvariantTests
        check = PlannerInvariantTests()
        for seed in range(20):
            tracks, analyses, overrides = _case(seed)
            profiles = {key: replace(a, rms_curve=(0.2,) * (int(a.duration_seconds) + 1),
                                    bass_curve=(0.5,) * (int(a.duration_seconds) + 1),
                                    brightness_curve=(0.2,) * (int(a.duration_seconds) + 1),
                                    percussive_curve=(0.6,) * (int(a.duration_seconds) + 1))
                        for key, a in analyses.items()}
            with self.subTest(seed=seed):
                plan = compile_automix(tracks, profiles, ENABLED.with_overrides(overrides), log_diagnostics=False)
                check.assert_plan_invariants(seed, tracks, plan)

    def test_partial_or_absent_profiles_use_existing_safe_rules(self):
        a, b = _analysis("a", 120.0, 60.0), _analysis("b", 120.0, 60.0)
        baseline = _plan(a, b)
        for incoming in (replace(b, rms_curve=(0.2,)), replace(_profile("b"), bass_curve=())):
            transition = _plan(a, incoming).audio.transitions[0]
            self.assertNotIn("acoustic_cost", dict(transition.details))
            self.assertIsNone(transition.band_windows)
        self.assertNotIn("acoustic_cost", dict(baseline.audio.transitions[0].details))


@unittest.skipUnless(os.environ.get("PLAYLIST_CANVAS_TEST_FFMPEG"), "Set PLAYLIST_CANVAS_TEST_FFMPEG")
class MeasuredRenderTests(unittest.TestCase):
    def test_custom_handoffs_reach_ffmpeg_without_changing_duration_or_clipping(self):
        import wave
        ffmpeg = Path(os.environ["PLAYLIST_CANVAS_TEST_FFMPEG"])
        chosen = _plan(_profile("a"), _profile("b", vocal_activity=((12.0, 60.0),))).audio.transitions[0]
        with TemporaryDirectory() as root:
            root = Path(root)
            paths = {}
            for i, track_id in enumerate(("a", "b")):
                t = np.arange(16 * 48000) / 48000
                signal = 0.3 * np.sin(2 * np.pi * (80 + i * 30) * t) + 0.1 * np.sin(2 * np.pi * (440 + i * 220) * t)
                path = root / f"{track_id}.wav"
                with wave.open(str(path), "wb") as file:
                    file.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
                    file.writeframes((signal * 32767).astype("<i2").tobytes())
                paths[track_id] = path
            duration = chosen.duration
            clips = (AudioRenderClip("a", "a", 0.0, 0.0, 16.0),
                     AudioRenderClip("b", "b", 16.0 - duration, 0.0, 16.0))
            transition = replace(chosen, clip_a="a", clip_b="b", timeline_start=16.0 - duration,
                                 type=TransitionType.BEAT_MATCH)
            rendered = AutoMixAudioPipeline(ffmpeg).render(AudioRenderPlan(clips, (transition,)), paths, root)
            self.assertAlmostEqual(rendered.duration_seconds, 32.0 - duration, delta=0.15)
            import subprocess
            raw = subprocess.run([str(ffmpeg), "-v", "error", "-i", str(rendered.path),
                                  "-f", "f32le", "pipe:1"], capture_output=True, check=True).stdout
            pcm = np.frombuffer(raw, dtype="<f4")
            self.assertTrue(np.all(np.isfinite(pcm)))
            self.assertLess(float(np.max(np.abs(pcm))), 0.98)


if __name__ == "__main__":
    unittest.main()
