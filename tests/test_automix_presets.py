from __future__ import annotations

import dataclasses
import unittest

from app.automix.planner import compile_automix
from app.automix.settings import AUTOMIX_PRESETS, AutoMixTransitionSettings, resolve_automix_settings
from app.models.project import ProjectDocument, ProjectSettings
from tests.test_automix_planner import _analysis, _track


class PresetResolutionTests(unittest.TestCase):
    def test_every_preset_resolves_to_enabled_immutable_settings(self) -> None:
        for preset in AUTOMIX_PRESETS:
            with self.subTest(preset=preset):
                settings = resolve_automix_settings(preset)
                self.assertTrue(settings.enabled)
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    settings.preferred_bars = 1  # type: ignore[misc]

    def test_auto_is_the_previous_default_and_the_fallback_for_unknown_names(self) -> None:
        self.assertEqual(resolve_automix_settings("auto"), AutoMixTransitionSettings(enabled=True))
        self.assertIs(resolve_automix_settings("nonsense"), resolve_automix_settings("auto"))
        self.assertIs(resolve_automix_settings(None), resolve_automix_settings("auto"))

    def test_presets_shape_the_same_planner_deterministically(self) -> None:
        tracks = [_track(name, 180.0) for name in "abc"]
        analyses = {track.id: _analysis(track.id, 120.0, 180.0) for track in tracks}
        durations = {}
        for preset in AUTOMIX_PRESETS:
            plan = compile_automix(tracks, analyses, resolve_automix_settings(preset))
            self.assertEqual(plan, compile_automix(tracks, analyses, resolve_automix_settings(preset)))
            durations[preset] = plan.audio.transitions[0].duration
        self.assertAlmostEqual(durations["energetic"], 8.0)   # 4 bars at 120 BPM
        self.assertAlmostEqual(durations["auto"], 16.0)       # 8 bars
        self.assertAlmostEqual(durations["smooth"], 32.0)     # 16 bars
        self.assertAlmostEqual(durations["dj"], 32.0)

    def test_dj_beat_matches_a_tempo_gap_that_auto_and_smooth_only_crossfade(self) -> None:
        tracks = [_track("a", 180.0), _track("b", 180.0)]
        analyses = {"a": _analysis("a", 120.0, 180.0), "b": _analysis("b", 132.0, 180.0)}  # 10 %
        types = {preset: compile_automix(tracks, analyses, resolve_automix_settings(preset)).audio.transitions[0].type.value
                 for preset in ("auto", "smooth", "dj")}
        self.assertEqual(types, {"auto": "crossfade", "smooth": "crossfade", "dj": "beat_match"})


class PresetPersistenceTests(unittest.TestCase):
    def test_default_and_invalid_presets_are_auto(self) -> None:
        self.assertEqual(ProjectSettings().automix_preset, "auto")
        self.assertEqual(ProjectSettings(automix_preset="loud").automix_preset, "auto")

    def test_preset_round_trips_and_old_projects_load_as_auto(self) -> None:
        document = ProjectDocument(settings=ProjectSettings(transition_mode="automix", automix_preset="dj"))
        data = document.to_dict()
        self.assertEqual(ProjectDocument.from_dict(data).settings.automix_preset, "dj")
        del data["settings"]["automix_preset"]  # saved before presets existed
        self.assertEqual(ProjectDocument.from_dict(data).settings.automix_preset, "auto")


if __name__ == "__main__":
    unittest.main()
