from __future__ import annotations

import json
import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.automix.planner import compile_automix  # noqa: E402
from app.automix.progressive import ProgressiveAnalysis, partial_plan  # noqa: E402
from app.timeline.compiler import compile_playlist  # noqa: E402
from app.timeline.models import TransitionType  # noqa: E402
from app.timeline.render_plan import AudioRenderTransition  # noqa: E402
from app.utils.i18n import Language  # noqa: E402
from app.widgets.automix_details_panel import AutoMixDetailsPanel, ready_through  # noqa: E402
from tests.test_automix_planner import ENABLED, _analysis, _track  # noqa: E402


class AutoMixDetailsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.translator = SimpleNamespace(language=Language.ENGLISH)  # never touches saved settings
        self.panel = AutoMixDetailsPanel(self.translator)
        self.tracks = [_track(name, 90.0) for name in "abc"]
        self.analyses = {track.id: _analysis(track.id, 120.0, 90.0) for track in self.tracks}
        self.plan = compile_automix(self.tracks, self.analyses, ENABLED)

    def test_final_plan_lists_every_transition_with_its_style_and_reasons(self) -> None:
        self.panel.set_plan(self.plan, self.tracks, state="final")
        self.assertEqual(self.panel.list.count(), 2)
        self.assertIn("Vocal-safe EQ", self.panel.list.item(0).text())  # vocals unknown
        self.assertIn("Final plan · 2 transition(s)", self.panel.status_label.text())
        details = self.panel.details.toPlainText()
        self.assertIn("a → b", details)
        self.assertIn("Outgoing BPM: 120.00", details)
        self.assertIn("* vocal_safe_eq: vocal activity unknown", details)

    def test_empty_and_sequential_plans_say_so(self) -> None:
        self.panel.set_plan(compile_playlist(self.tracks[:1]), self.tracks[:1], state="waiting")
        self.assertEqual(self.panel.list.count(), 0)
        self.assertIn("no transitions", self.panel.details.toPlainText())
        self.panel.set_plan(compile_playlist(self.tracks), self.tracks, state="waiting")
        self.assertIn("Back to back", self.panel.list.item(0).text())
        self.assertIn("Preparing the mix", self.panel.status_label.text())

    def test_a_transition_without_reasons_falls_back_gracefully(self) -> None:
        clips = compile_playlist(self.tracks[:2]).audio.clips
        legacy = AudioRenderTransition(clips[0].clip_id, clips[1].clip_id, clips[1].timeline_start - 3.0, 3.0,
                                       TransitionType.CROSSFADE)
        plan = compile_playlist(self.tracks[:2])
        plan = type(plan)(audio=type(plan.audio)(clips=plan.audio.clips, transitions=(legacy,)),
                          presentation=plan.presentation, metadata=plan.metadata,
                          duration_seconds=plan.duration_seconds)
        self.panel.set_plan(plan, self.tracks[:2], state="final")
        self.assertIn("Plain crossfade", self.panel.list.item(0).text())
        self.assertIn("No selection reasons recorded", self.panel.details.toPlainText())

    def test_provisional_state_reports_how_far_automix_is_ready(self) -> None:
        state = ProgressiveAnalysis(self.tracks, structure_enabled=False)
        for track in self.tracks[:2]:
            state.record_rhythm(track.id, self.analyses[track.id])
        partial = partial_plan(self.tracks, state, ENABLED)
        covered = partial.audio.clips[1].timeline_end
        self.assertEqual(ready_through(partial, covered), 2)
        self.panel.set_plan(partial, self.tracks, state="provisional", ready_through=2)
        self.assertIn("AutoMix through track 2", self.panel.status_label.text())
        self.assertIn("Back to back", self.panel.list.item(1).text())  # the unanalyzed tail

    def test_status_shows_progress_fallbacks_and_failure_with_symbol_and_text(self) -> None:
        panel = AutoMixDetailsPanel(self.translator, automix=True)
        panel.set_plan(compile_playlist(self.tracks), self.tracks, state="waiting")
        panel.set_progress_message("Analyzing 1 / 3 · Planning transitions 0 / 2")
        self.assertEqual(panel.status_label.text(), "● Analyzing 1 / 3 · Planning transitions 0 / 2")
        self.assertIn("Analyzing 1 / 3", panel.status_label.accessibleName())

        clips = compile_playlist(self.tracks[:2]).audio.clips
        legacy = AudioRenderTransition(clips[0].clip_id, clips[1].clip_id, clips[1].timeline_start - 3.0, 3.0,
                                       TransitionType.CROSSFADE)
        plan = compile_playlist(self.tracks[:2])
        plan = type(plan)(audio=type(plan.audio)(clips=plan.audio.clips, transitions=(legacy,)),
                          presentation=plan.presentation, metadata=plan.metadata,
                          duration_seconds=plan.duration_seconds)
        panel.set_plan(plan, self.tracks[:2], state="final")
        self.assertEqual(panel.status_label.text(), "✓ Final plan · 1 transition(s) · 1 as plain crossfade")
        self.assertIn("Plain crossfades replace AutoMix", panel.status_label.toolTip())
        # In crossfade mode a plain crossfade is the plan itself, not a fallback.
        self.panel.set_plan(plan, self.tracks[:2], state="final")
        self.assertNotIn("plain crossfade", self.panel.status_label.text())

        failed = AutoMixDetailsPanel(self.translator, automix=True)
        failed.set_plan(compile_playlist(self.tracks), self.tracks, state="waiting")
        failed.set_failed()
        self.assertTrue(failed.status_label.text().startswith("! Could not prepare the mix"))
        self.assertIn("Preview keeps playing", failed.status_label.accessibleDescription())

    def test_details_name_the_analyzer_without_alarming_the_status(self) -> None:
        from dataclasses import replace

        panel = AutoMixDetailsPanel(self.translator, automix=True)
        analyses = {key: replace(value, analyzer_id="basic") for key, value in self.analyses.items()}
        panel.set_plan(compile_automix(self.tracks, analyses, ENABLED), self.tracks, state="final")
        self.assertNotIn("!", panel.status_label.text())  # the light analyzer is the normal case
        self.assertIn("Outgoing analyzer: basic", panel.details.toPlainText())

    def test_playhead_marks_the_sounding_transition_only(self) -> None:
        self.panel.set_plan(self.plan, self.tracks, state="final")
        first = self.plan.audio.transitions[0]
        self.panel.set_playhead(first.timeline_start + 0.5)
        self.assertEqual(self.panel.current_index, 0)
        self.assertTrue(self.panel.list.item(0).font().bold())
        self.panel.set_playhead(first.timeline_start - 1.0)
        self.assertEqual(self.panel.current_index, -1)
        self.assertFalse(self.panel.list.item(0).font().bold())

    def test_copy_json_and_text_and_reopen_keep_working(self) -> None:
        self.panel.set_plan(self.plan, self.tracks, state="final")
        self.panel.copy_json_button.click()
        self.assertEqual(json.loads(QGuiApplication.clipboard().text())[0]["dsp"], "vocal_safe_eq")
        self.panel.copy_text_button.click()
        self.assertIn("b → c", QGuiApplication.clipboard().text())
        self.panel.toggle_button.setChecked(True)
        self.assertFalse(self.panel.body.isHidden())
        self.panel.toggle_button.setChecked(False)
        self.panel.toggle_button.setChecked(True)  # collapse and reopen
        self.panel.set_plan(self.plan, self.tracks, state="final")
        self.assertEqual(self.panel.list.count(), 2)

    def test_language_switch_relabels_in_place(self) -> None:
        self.panel.set_plan(self.plan, self.tracks, state="final")
        self.translator.language = Language.KOREAN
        self.panel.retranslate()
        self.assertIn("보컬 보호 EQ", self.panel.list.item(0).text())
        self.assertIn("최종 계획", self.panel.status_label.text())


class PreviewPreparationDialogTests(unittest.TestCase):
    def test_title_names_the_projects_transition_mode(self) -> None:
        from app.dialogs.preview_preparation_dialog import PreviewPreparationDialog

        QApplication.instance() or QApplication([])
        translator = SimpleNamespace(language=Language.KOREAN)
        crossfade = PreviewPreparationDialog(translator, transition_mode="crossfade")
        self.assertEqual(crossfade.title_label.text(), "크로스페이드 미리보기 준비 중")
        automix = PreviewPreparationDialog(translator)
        self.assertEqual(automix.title_label.text(), "AutoMix 미리보기 준비 중")


if __name__ == "__main__":
    unittest.main()
