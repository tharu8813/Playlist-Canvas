"""Genre/mood changes real mix decisions, without overriding uncertainty or vocal safety."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import threading
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

import numpy as np

from app.automix.analysis.provider import AnalysisCancelled
from app.automix.candidates import generate_candidates, select_best_candidate
from app.automix.compatibility import evaluate_compatibility
from app.automix.models import MusicTagRegion, TrackAnalysis
from app.automix.music import analyze_music, character, model_directory, music_tags_at
from app.automix.planner import compile_automix
from app.automix.settings import AutoMixTransitionSettings
from app.automix.transition_style import select_transition_dsp
from app.timeline.models import TransitionType
from app.timeline.render_plan import TransitionDsp, validate_compiled_render_plan
from tests.test_automix_candidates import _analysis
from tests.test_automix_planner import _track
from tests.test_automix_transition_style import _candidate, _compatibility, _quiet

SETTINGS = AutoMixTransitionSettings(enabled=True)


def _tag(analysis, genres=(), moods=()):
    return replace(analysis, music_tags=(MusicTagRegion(0.0, analysis.duration_seconds, genres, moods),))


def _best(a, b):
    return select_best_candidate(generate_candidates(a, b, evaluate_compatibility(a, b, SETTINGS), SETTINGS))


class MusicAwareMixTests(unittest.TestCase):
    def test_rap_and_contrasting_moods_choose_a_shorter_overlap(self):
        a, b = _analysis("a", 120.0, 192.0), _analysis("b", 120.0)
        baseline = _best(a, b)
        rap = _best(_tag(a, (("Hip hop music", 0.95),)), b)
        contrast = _best(_tag(a, moods=(("Tender music", 0.95),)),
                         _tag(b, moods=(("Exciting music", 0.95),)))
        self.assertLess(rap.duration_seconds, baseline.duration_seconds)
        self.assertLess(contrast.duration_seconds, baseline.duration_seconds)
        self.assertTrue(any("hip hop" in r for r in rap.reasons))
        emotions = _best(_tag(a, moods=(("Happy music", 0.95),)),
                         _tag(b, moods=(("Sad music", 0.95),)))
        self.assertLess(emotions.duration_seconds, baseline.duration_seconds)

    def test_shared_dance_character_allows_a_sustained_blend(self):
        a, b = _analysis("a", 120.0, 200.0), _analysis("b", 120.0)
        baseline = _best(a, b)
        a, b = [_tag(track, (("Electronic dance music", 0.95),)) for track in (a, b)]
        driven = _best(a, b)
        self.assertGreater(driven.duration_seconds, baseline.duration_seconds)

    def test_gentle_music_avoids_a_tempo_bridge_and_turntable_effects(self):
        a = _tag(_analysis("a", 120.0, 192.0), (("Classical music", 0.9),))
        b = _tag(_analysis("b", 128.0, 180.0), (("Classical music", 0.9),))
        plan = compile_automix([_track("a", 192.0), _track("b", 180.0)], {"a": a, "b": b}, SETTINGS)
        validate_compiled_render_plan(plan)
        self.assertIsNone(plan.audio.clips[0].tempo_ramp)
        self.assertTrue(all(t.type is not TransitionType.BEAT_MATCH for t in plan.audio.transitions))
        self.assertTrue(all(t.dsp is TransitionDsp.SHORT_FADE for t in plan.audio.transitions))
        self.assertIn("Classical music", dict(plan.audio.transitions[0].details)["outgoing_genres"])

    def test_gentle_blend_protects_unknown_or_conflicting_vocals(self):
        gentle = (("Tender music", 0.9),)
        for measured in (False, True):
            a = _tag(_quiet("a"), moods=gentle)
            b = _tag(_quiet("b"), moods=gentle)
            if measured:
                a = replace(a, vocal_activity=((100.0, 115.0),))
                b = replace(b, vocal_activity=((0.0, 15.0),))
            else:
                a = replace(a, vocal_activity=())
                b = replace(b, vocal_activity=())
            with self.subTest(measured=measured):
                self.assertIs(select_transition_dsp(_candidate(), _compatibility(), a, b).dsp,
                              TransitionDsp.VOCAL_SAFE_EQ)
        a, b = [_tag(_quiet(name), moods=gentle) for name in ("a", "b")]
        self.assertIs(select_transition_dsp(_candidate(), _compatibility(), a, b).dsp, TransitionDsp.SHORT_FADE)

    def test_actual_tail_can_differ_from_the_songs_gentle_head(self):
        a = replace(_quiet("a"), music_tags=(
            MusicTagRegion(0.0, 60.0, (("Classical music", 0.95),)),
            MusicTagRegion(60.0, 120.0, (("Electronic dance music", 0.95),)),
        ))
        b = _tag(_quiet("b"), (("Electronic dance music", 0.95),))
        decision = select_transition_dsp(_candidate(), _compatibility(), a, b)
        self.assertIs(decision.dsp, TransitionDsp.BASS_SWAP)
        self.assertIn("Electronic dance music", dict(decision.metrics)["outgoing_genres"])

    def test_uncertain_tags_do_not_change_the_plan(self):
        a, b = _analysis("a", 120.0, 192.0), _analysis("b", 128.0)
        tracks = [_track("a", 192.0), _track("b", 180.0)]
        original = compile_automix(tracks, {"a": a, "b": b}, SETTINGS)
        tagged = compile_automix(tracks, {"a": _tag(a, (("Classical music", 0.2),)),
                                          "b": _tag(b, moods=(("Tender music", 0.2),))}, SETTINGS)
        self.assertEqual(original, tagged)

    def test_unmeasured_windows_stay_unknown_and_mixed_scores_are_weighted(self):
        a = replace(_analysis("a", 120.0), music_tags=(
            MusicTagRegion(0.0, 8.0, (("Jazz", 0.9),)),
            MusicTagRegion(8.0, 16.0, (("Jazz", 0.1),)),
        ))
        self.assertAlmostEqual(dict(music_tags_at(a, 4.0, 16.0).genres)["Jazz"], (4 * 0.9 + 8 * 0.1) / 12)
        self.assertIsNone(music_tags_at(a, 10.0, 30.0))
        self.assertEqual(character(music_tags_at(a, 100.0, 120.0)), (0.0, 0.0, 0.0))

    def test_tags_round_trip_through_json_cache_and_legacy_cache_stays_valid(self):
        a = _tag(_analysis("a", 120.0), (("Jazz", 0.8),), (("Happy music", 0.7),))
        fields = json.loads(json.dumps(a.to_cache_fields()))
        self.assertEqual(TrackAnalysis.from_cache_fields(a.track_id, a.source_path, fields), a)
        del fields["music_tags"]
        self.assertEqual(TrackAnalysis.from_cache_fields(a.track_id, a.source_path, fields).music_tags, ())
        with self.assertRaises(ValueError):
            MusicTagRegion(0.0, 5.0, (("Jazz", float("nan")),))
        with self.assertRaises(ValueError):
            replace(a, music_tags=(MusicTagRegion(170.0, 190.0),))

    def test_classifier_output_maps_genres_and_moods_and_preserves_sample_rate(self):
        session = Mock()
        scores = np.zeros((3, 521), dtype=np.float32)
        scores[:, 240], scores[:, 274] = 0.9, 0.8
        session.run.return_value = [scores]
        with patch("app.automix.music._session", return_value=session):
            tags = analyze_music(np.ones(22050 * 10, dtype=np.float32) * 0.2, 22050, 10.0, threading.Event())
        self.assertEqual([(r.start_seconds, r.end_seconds) for r in tags], [(0.0, 8.0), (8.0, 10.0)])
        self.assertEqual(tags[0].genres[0], ("Electronic dance music", 0.9))
        self.assertEqual(tags[0].moods[0], ("Exciting music", 0.8))
        self.assertEqual(session.run.call_args_list[0].args[1]["waveform"].shape, (16000 * 8,))

    def test_classifier_failure_and_cancellation_are_safe(self):
        signal = np.ones(16000 * 2, dtype=np.float32) * 0.2
        with patch("app.automix.music._session", side_effect=RuntimeError("bad model")):
            self.assertEqual(analyze_music(signal, 16000, 2.0, threading.Event()), ())
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(AnalysisCancelled):
            analyze_music(signal, 16000, 2.0, cancelled)

    @unittest.skipUnless(importlib.util.find_spec("onnxruntime"), "requires ONNX Runtime")
    def test_real_bundled_model_checksum_and_silence_inference(self):
        path = model_directory() / "yamnet.onnx"
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                         "d3835ffbbd4a1bb3e777f0ca217b5007907f5171dd5d17c4236b95b2af8f908e")
        tags = analyze_music(np.zeros(16000 * 3, dtype=np.float32), 16000, 3.0, threading.Event())
        self.assertTrue(tags)
        self.assertTrue(all(not r.genres and not r.moods for r in tags))


if __name__ == "__main__":
    unittest.main()
