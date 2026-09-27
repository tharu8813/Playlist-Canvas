"""Manual AutoMix junctions: the saved override, the planner's manual path, and the editor."""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.automix.overrides import (  # noqa: E402
    TransitionOverride, pair_key, parse_overrides, serialize_overrides,
)
from app.automix.planner import compile_automix, plan_manual_junction  # noqa: E402
from app.automix.settings import automix_settings_for  # noqa: E402
from app.models.project import ProjectDocument, ProjectSettings  # noqa: E402
from app.services.playlist_service import PlaylistService  # noqa: E402
from app.timeline.models import TransitionType  # noqa: E402
from app.timeline.render_plan import TransitionDsp, validate_compiled_render_plan  # noqa: E402
from app.utils.i18n import Translator  # noqa: E402
from app.widgets.automix_details_panel import AutoMixDetailsPanel  # noqa: E402
from app.widgets.playlist_editor import PlaylistEditor, TransitionChip  # noqa: E402
from app.widgets.transition_editor import (  # noqa: E402
    EditContext, draft_junction, override_from_junction, snap, snap_length,
)
from app.widgets.transition_inspector import TransitionInspectorWindow, plan_junctions  # noqa: E402
from tests.test_automix_planner import ENABLED, _analysis, _track  # noqa: E402


def _tracks():
    return [_track("a", 200.0), _track("b", 180.0), _track("c", 210.0)]


def _analyses(tracks, bpms=(120.0, 120.0, 120.0)):
    return {track.id: _analysis(track.id, bpm, track.duration_seconds) for track, bpm in zip(tracks, bpms)}


def _manual(settings, **overrides):
    return settings.with_overrides({key: value for key, value in overrides.items()})


class OverrideStorageTests(unittest.TestCase):
    def test_round_trip_and_broken_entries_are_dropped(self) -> None:
        good = TransitionOverride(150.0, 4.0, 12.0, "filter_sweep", False, None)
        parsed = parse_overrides({
            "a>b": good.to_dict(),
            "no-separator": good.to_dict(),
            "b>c": {"outgoing_cue": -3},
            "c>d": {"outgoing_cue": 10.0, "style": "reverb"},
        })
        self.assertEqual(parsed, {"a>b": good})
        self.assertEqual(parse_overrides(serialize_overrides(parsed)), parsed)

    def test_project_file_keeps_manual_junctions(self) -> None:
        document = ProjectDocument(settings=ProjectSettings(
            transition_mode="automix",
            automix_overrides={"a>b": TransitionOverride(150.0).to_dict(), "x": {"bad": 1}},
        ))
        self.assertEqual(list(document.settings.automix_overrides), ["a>b"])
        restored = ProjectDocument.from_dict(document.to_dict())
        self.assertEqual(restored.settings.automix_overrides, document.settings.automix_overrides)
        settings = automix_settings_for(restored.settings)
        self.assertEqual(settings.override_for("a", "b"), TransitionOverride(150.0))
        self.assertIsNone(settings.override_for("b", "a"))

    def test_old_projects_load_with_every_junction_automatic(self) -> None:
        restored = ProjectDocument.from_dict({"settings": {"transition_mode": "automix"}})
        self.assertEqual(restored.settings.automix_overrides, {})
        self.assertEqual(automix_settings_for(restored.settings).overrides, ())


class ManualPlanTests(unittest.TestCase):
    def test_manual_junction_uses_the_users_cues_length_and_style(self) -> None:
        tracks = _tracks()
        override = TransitionOverride(150.0, 4.0, 12.0, "filter_sweep", tempo_match=False)
        plan = compile_automix(tracks, _analyses(tracks), _manual(ENABLED, **{"a>b": override}))
        validate_compiled_render_plan(plan)
        first, second, _third = plan.audio.clips
        transition = plan.audio.transitions[0]
        self.assertAlmostEqual(first.source_out, 162.0)
        self.assertAlmostEqual(transition.timeline_start, first.timeline_at(150.0))
        self.assertAlmostEqual(transition.duration, 12.0)
        self.assertEqual(transition.dsp, TransitionDsp.FILTER_SWEEP)
        self.assertEqual(dict(transition.details)["mode"], "manual")
        self.assertEqual((second.source_in, second.timeline_start), (4.0, transition.timeline_start))
        # The next junction is still automatic.
        self.assertEqual(dict(plan.audio.transitions[1].details)["mode"], "auto")

    def test_cut_stops_at_the_cue_and_starts_the_next_song_at_its_cue(self) -> None:
        tracks = _tracks()
        override = TransitionOverride(120.0, 10.0, style="cut")
        plan = compile_automix(tracks, _analyses(tracks), _manual(ENABLED, **{"a>b": override}))
        first, second, _third = plan.audio.clips
        self.assertAlmostEqual(first.source_out, 120.0)
        self.assertAlmostEqual(second.timeline_start, first.timeline_end)
        self.assertEqual(second.source_in, 10.0)
        self.assertNotIn(first.clip_id, {t.clip_a for t in plan.audio.transitions})

    def test_tempo_match_ramps_the_outgoing_song_onto_the_incoming_tempo(self) -> None:
        tracks = _tracks()
        override = TransitionOverride(150.0, 0.0, 10.0, "legacy", tempo_match=True)
        plan = compile_automix(tracks, _analyses(tracks, (120.0, 125.0, 120.0)),
                               _manual(ENABLED, **{"a>b": override}))
        first = plan.audio.clips[0]
        transition = plan.audio.transitions[0]
        self.assertIsNotNone(first.tempo_ramp)
        self.assertAlmostEqual(first.tempo_ramp.end_rate, 125.0 / 120.0)
        # A plain crossfade must not become BEAT_MATCH's bass swap.
        self.assertEqual(transition.type, TransitionType.EQUAL_POWER)
        self.assertIsNone(transition.dsp)

    def test_out_of_range_values_are_clamped_to_what_the_songs_have(self) -> None:
        tracks = _tracks()
        override = TransitionOverride(195.0, 0.0, 30.0, "short_fade", tempo_match=False)
        plan = compile_automix(tracks, _analyses(tracks), _manual(ENABLED, **{"a>b": override}))
        transition = plan.audio.transitions[0]
        self.assertAlmostEqual(transition.duration, 5.0)  # only 5 s of "a" left after 195 s
        validate_compiled_render_plan(plan)

    def test_a_cue_never_starts_before_the_previous_transition_ends(self) -> None:
        tracks = _tracks()
        auto = compile_automix(tracks, _analyses(tracks), ENABLED)
        into_b = auto.audio.transitions[0]
        override = TransitionOverride(0.0, 0.0, 8.0, "short_fade", tempo_match=False)
        plan = compile_automix(tracks, _analyses(tracks), _manual(ENABLED, **{"b>c": override}))
        self.assertGreaterEqual(plan.audio.transitions[1].timeline_start,
                                into_b.timeline_start + into_b.duration - 1e-6)
        validate_compiled_render_plan(plan)

    def test_works_without_analysis(self) -> None:
        tracks = _tracks()
        override = TransitionOverride(150.0, 0.0, 6.0)
        plan = compile_automix(tracks, {}, _manual(ENABLED, **{"a>b": override}))
        (transition,) = plan.audio.transitions
        self.assertAlmostEqual(transition.duration, 6.0)
        self.assertEqual(dict(transition.details)["mode"], "manual")

    def test_reordered_tracks_fall_back_to_automatic(self) -> None:
        tracks = _tracks()
        override = TransitionOverride(150.0, 0.0, 6.0, "cut")
        settings = _manual(ENABLED, **{"a>b": override})
        reordered = [tracks[1], tracks[0], tracks[2]]
        plan = compile_automix(reordered, _analyses(reordered), settings)
        self.assertTrue(all(dict(t.details)["mode"] == "auto" for t in plan.audio.transitions))

    def test_the_editor_draft_matches_the_compiled_plan(self) -> None:
        tracks = _tracks()
        analyses = _analyses(tracks, (120.0, 124.0, 120.0))
        override = TransitionOverride(140.0, 2.0, 10.0, "vocal_safe_eq", True, 0.4)
        compiled = compile_automix(tracks, analyses, _manual(ENABLED, **{"b>c": override}))
        auto = plan_junctions(compile_automix(tracks, analyses, ENABLED))
        draft = draft_junction(auto, 1, override, EditContext({t.id: t for t in tracks}, analyses))
        actual = plan_junctions(compiled)[1]
        self.assertAlmostEqual(draft.start, actual.start, places=6)
        self.assertAlmostEqual(draft.end, actual.end, places=6)
        self.assertEqual(draft.transition.dsp, actual.transition.dsp)
        self.assertEqual(draft.transition.vocal_handoff, 0.4)
        outgoing, incoming, transition = plan_manual_junction(
            auto[1].outgoing, tracks[1], tracks[2], override, analyses)
        self.assertAlmostEqual(transition.timeline_start, actual.start, places=6)


class EditorHelperTests(unittest.TestCase):
    def test_prefill_reproduces_the_automatic_window(self) -> None:
        tracks = _tracks()
        analyses = _analyses(tracks)
        junction = plan_junctions(compile_automix(tracks, analyses, ENABLED))[0]
        override = override_from_junction(junction)
        manual = compile_automix(tracks, analyses, _manual(ENABLED, **{"a>b": override}))
        again = plan_junctions(manual)[0]
        self.assertAlmostEqual(again.start, junction.start, places=4)
        self.assertAlmostEqual(again.end, junction.end, places=4)

    def test_snapping(self) -> None:
        self.assertEqual(snap(10.3, (8.0, 10.0, 12.0)), 10.0)
        self.assertEqual(snap(10.3, ()), 10.3)
        self.assertEqual(snap(10.9, (8.0, 12.0), tolerance=0.5), 10.9)
        self.assertAlmostEqual(snap_length(7.8, _analysis("x", 120.0, 60.0)), 8.0)


class EditorWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def _window(self):
        tracks = _tracks()
        analyses = _analyses(tracks)
        plan = compile_automix(tracks, analyses, ENABLED)
        panel = AutoMixDetailsPanel(Translator(), automix=True)
        panel.set_plan(plan, tracks, state="final")
        window = TransitionInspectorWindow(panel)
        self.addCleanup(window.deleteLater)
        self.addCleanup(panel.deleteLater)
        window.enable_editing(lambda: EditContext({t.id: t for t in tracks}, analyses), {})
        changes = []
        window.override_changed.connect(lambda key, override: changes.append((key, override)))
        return window, changes

    def test_switching_to_manual_starts_from_the_automatic_result(self) -> None:
        window, changes = self._window()
        self.assertTrue(window.select_pair("b", "c"))
        self.assertFalse(window.mode_box.isHidden())
        self.assertTrue(window.editor.isHidden())
        window.manual_button.click()
        key, override = changes[-1]
        self.assertEqual(key, pair_key("b", "c"))
        self.assertEqual(override, override_from_junction(window.junctions[1]))
        self.assertFalse(window.editor.isHidden())
        self.assertTrue(window.diagram.editable)
        self.assertTrue(window.heading_label.text().startswith("✎"))
        window.editor.style_buttons["cut"].click()
        self.assertEqual(changes[-1][1].style, "cut")
        window.editor.revert_button.click()
        self.assertEqual(changes[-1], (pair_key("b", "c"), None))
        self.assertTrue(window.editor.isHidden())

    def test_dragging_the_overlap_moves_the_cue_on_the_beat(self) -> None:
        window, changes = self._window()
        window.select_pair("a", "b")
        window.manual_button.click()
        before = changes[-1][1]
        window._drag_started("move")
        window._drag_moved("move", -4.2, False)
        self.assertEqual(len(changes), 1)  # nothing is committed mid-drag
        self.assertTrue(window.diagram.draft)
        window._drag_finished()
        after = changes[-1][1]
        self.assertLess(after.outgoing_cue, before.outgoing_cue)
        grid = _analyses(_tracks())["a"].downbeats
        self.assertIn(after.outgoing_cue, grid)
        window._drag_started("end")
        window._drag_moved("end", 3.0, True)
        window._drag_finished()
        self.assertAlmostEqual(changes[-1][1].duration, after.duration + 3.0)

    def test_a_new_plan_replaces_the_draft(self) -> None:
        window, changes = self._window()
        window.select_pair("a", "b")
        window.manual_button.click()
        self.assertTrue(window._drafts)
        tracks = _tracks()
        settings = ENABLED.with_overrides({changes[-1][0]: changes[-1][1]})
        window.panel.set_plan(compile_automix(tracks, _analyses(tracks), settings), tracks, state="final")
        self.assertFalse(window._drafts)
        self.assertFalse(window.diagram.draft)


class PreviewRemixTests(unittest.TestCase):
    def test_an_edit_re_mixes_into_a_fresh_directory_with_the_new_settings(self) -> None:
        from types import SimpleNamespace

        from app.dialogs.export_preview_dialog import ExportPreviewDialog

        starts = []
        controller = SimpleNamespace(
            progressive_ready=object(),
            start=lambda tracks, directory, mode, crossfade, automix_settings=None: starts.append(
                (directory.name, mode, automix_settings)),
        )
        emitted = []
        preview = SimpleNamespace(
            _closing=False, _transition_mode="automix", tracks=_tracks(), _blended_audio_controller=controller,
            _blended_audio_temp_dir=None, _remix_count=0, _pending_swap=("stale",), _crossfade_seconds=3.0,
            _automix_settings=ENABLED, _report_playhead=lambda force=False: None, _preview_proxy_ffmpeg=None,
            automix_override_changed=SimpleNamespace(emit=lambda *args: emitted.append(args)),
        )
        preview._automix_overrides = lambda: ExportPreviewDialog._automix_overrides(preview)
        preview.remix_automix = lambda: ExportPreviewDialog.remix_automix(preview)
        override = TransitionOverride(120.0, style="cut")
        ExportPreviewDialog._on_automix_override_changed(preview, "a>b", override)
        ExportPreviewDialog._on_automix_override_changed(preview, "a>b", None)
        self.addCleanup(preview._blended_audio_temp_dir.cleanup)
        self.assertEqual(emitted, [("a>b", override), ("a>b", None)])
        self.assertEqual([(name, mode) for name, mode, _settings in starts],
                         [("remix-1", "automix"), ("remix-2", "automix")])
        self.assertEqual(starts[0][2].override_for("a", "b"), override)
        self.assertIsNone(starts[1][2].override_for("a", "b"))
        self.assertIsNone(preview._pending_swap)


class PlaylistChipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_chips_sit_between_enabled_tracks_and_show_manual_ones(self) -> None:
        service = PlaylistService()
        service.replace([_track("a", 100.0), _track("b", 100.0, enabled=False), _track("c", 100.0)])
        editor = PlaylistEditor(service, Translator())
        self.addCleanup(editor.deleteLater)

        def chips():
            rows = [editor.list_widget.itemWidget(editor.list_widget.item(i))
                    for i in range(editor.list_widget.count())]
            return [row.transition_chip for row in rows]

        self.assertEqual(chips(), [None, None, None])  # not AutoMix: no chips
        editor.set_transitions(True, {"a>c": TransitionOverride(80.0, style="cut").to_dict()})
        first, excluded, third = chips()
        self.assertIsNone(first)
        self.assertIsNone(excluded)
        self.assertIsInstance(third, TransitionChip)
        self.assertTrue(third.property("manual"))
        requested = []
        editor.transition_edit_requested.connect(lambda *pair: requested.append(pair))
        third.click()
        self.assertEqual(requested, [("a", "c")])


if __name__ == "__main__":
    unittest.main()
