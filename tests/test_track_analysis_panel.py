"""Track information's Analysis tab: step-by-step progress and the analysis read-out."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.automix.analysis.service import STEP_CACHED
from app.automix.models import TrackAnalysis
from app.automix.structure.models import TrackSection, TrackStructureAnalysis
from app.widgets.track_analysis_panel import (
    STEPS, TrackAnalysisPanel, analysis_facts, clock, summary_cards,
)


def _analysis(**changes) -> TrackAnalysis:
    fields = dict(
        track_id="t", source_path="song.flac", duration_seconds=200.0, bpm=128.0, bpm_confidence=0.9,
        beats=tuple(i * 0.5 for i in range(1, 390)), downbeats=tuple(i * 2.0 for i in range(1, 98)),
        meter_numerator=4, meter_denominator=4, meter_confidence=0.8, key="A minor", key_confidence=0.6,
        energy=0.7, vocal_activity=((12.0, 40.0),), vocal_coverage=((0.0, 45.0), (155.0, 200.0)),
        audible_start_seconds=0.5, audible_end_seconds=197.0, decay_start_seconds=190.0,
        analyzer_id="beat_this_onnx",
    )
    fields.update(changes)
    return TrackAnalysis(**fields)


class TrackAnalysisPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_cards_and_facts_describe_the_analysis(self) -> None:
        cards = {caption: (value, detail) for caption, value, detail in summary_cards(_analysis(), True)}
        self.assertEqual(cards["템포"], ("128.0 BPM", "신뢰도 90%"))
        self.assertEqual(cards["키"][0], "A minor · 8A")
        self.assertEqual(cards["박자"][0], "4/4")
        self.assertEqual(cards["에너지"], ("0.70", "높음"))
        self.assertEqual(cards["보컬"], ("0:12부터", "마지막 보컬 0:40"))
        self.assertEqual(cards["믹스 준비도"][0], "좋음")
        unmeasured = {caption: value for caption, value, _ in summary_cards(
            _analysis(vocal_activity=(), vocal_coverage=None, bpm=None), False)}
        self.assertEqual(unmeasured["Vocals"], "Unknown")
        self.assertEqual(unmeasured["Tempo"], "—")

        structure = TrackStructureAnalysis(
            "t", "song.flac", 200.0, intro_end_seconds=15.0, outro_start_seconds=180.0,
            sections=(TrackSection(0.0, 15.0), TrackSection(15.0, 180.0), TrackSection(180.0, 200.0)),
            analyzer_id="sonara",
        )
        facts = dict(analysis_facts(_analysis(), structure, True))
        self.assertEqual(facts["비트 / 마디"], "비트 389개 · 97마디")
        self.assertEqual(facts["소리 끝"], "3:17 (끝 무음 3.0초)")
        self.assertEqual(facts["여운 시작"], "3:10 (여운 7.0초)")
        self.assertEqual(facts["인트로 끝"], "0:15")
        self.assertEqual(facts["구간"], "3개 · 0:00 · 0:15 · 3:00")
        self.assertEqual(facts["분석기"], "beat_this_onnx · sonara")
        self.assertEqual(clock(3725), "1:02:05")

    def test_steps_advance_skip_and_finish(self) -> None:
        panel = TrackAnalysisPanel()
        self.assertEqual(panel.analyze_button.text(), "이 곡 분석")
        self.assertTrue(panel.results.isHidden())
        panel.begin_analysis()
        self.assertFalse(panel.analyze_button.isEnabled())
        panel.set_step("decode", 0.0)
        panel.set_step("bars", 0.3)  # a step the analyzer went past without reporting is skipped
        self.assertEqual(panel.step_states["decode"], "done")
        self.assertEqual(panel.step_states["rhythm"], "skipped")
        self.assertEqual(panel.step_states["bars"], "running")
        self.assertGreater(panel.progress_bar.value(), 0)
        panel.set_results(_analysis())
        panel.finish_analysis()
        self.assertEqual(panel.step_states["bars"], "done")
        self.assertEqual(panel.step_states[STEPS[-1]], "skipped")  # no structure pass reported
        self.assertEqual(panel.progress_bar.value(), 1000)
        self.assertFalse(panel.results.isHidden())
        self.assertEqual(panel.analyze_button.text(), "다시 분석")
        self.assertEqual(panel.cards[0][1].text(), "128.0 BPM")

    def test_a_cached_result_marks_the_rhythm_steps_as_stored(self) -> None:
        panel = TrackAnalysisPanel()
        panel.retranslate(False)
        panel.begin_analysis()
        panel.set_step(STEP_CACHED, 1.0)
        panel.set_step(STEPS[-1], 1.0)
        panel.finish_analysis("boom")
        self.assertTrue(all(panel.step_states[step] == "cached" for step in STEPS[:-1]))
        self.assertEqual(panel.step_states[STEPS[-1]], "failed")
        self.assertIn("boom", panel.status_label.text())


if __name__ == "__main__":
    unittest.main()
