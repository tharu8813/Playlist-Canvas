"""The status-bar progress line and its live details popup."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.widgets.activity_progress import ActivityProgressWidget, ActivityStep  # noqa: E402


class ActivityProgressWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.widget = ActivityProgressWidget(korean=False)
        self.addCleanup(self.widget.deleteLater)

    def click(self) -> None:
        for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            QApplication.sendEvent(self.widget, QMouseEvent(
                kind, QPointF(5, 5), QPointF(5, 5), Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
            ))

    def test_one_operation_shows_its_own_title_and_progress(self) -> None:
        self.widget.begin("export", "Exporting video", 0.25)
        self.assertEqual(self.widget.label.text(), "Exporting video")
        self.assertEqual(self.widget.progress_bar.value(), 250)
        self.assertEqual(self.widget.progress_bar.format(), "%p%")

    def test_several_operations_show_working_and_their_combined_progress(self) -> None:
        self.widget.begin("export", "Exporting video", 0.2)
        self.widget.begin("analysis", "AutoMix track analysis", 0.6)
        self.widget.begin("save", "Saving project")  # not measurable: left out of the mean
        self.assertEqual(self.widget.label.text(), "Working...")
        self.assertEqual(self.widget.progress_bar.value(), 400)
        self.assertEqual(self.widget.progress_bar.format(), "%p% · 3")
        self.widget.finish("export")
        self.widget.finish("analysis")
        self.assertEqual(self.widget.label.text(), "Saving project")
        self.assertEqual(self.widget.progress_bar.maximum(), 0)  # busy

    def test_popup_lists_steps_and_follows_updates_live(self) -> None:
        self.widget.begin("analysis", "AutoMix track analysis", 0.25, "Keep editing",
                          steps=[("Beats & vocals", 0.5, "2 / 4"), ActivityStep("Song structure", 0.0)])
        self.click()  # pin it open
        popup = self.widget.popup
        self.assertTrue(popup.isVisible())
        row = popup.rows["analysis"]
        self.assertEqual(row.percent.text(), "25%")
        self.assertEqual([name.text() for name, _bar, _pct in row._step_widgets],
                         ["Beats & vocals · 2 / 4", "Song structure"])

        self.widget.update("analysis", 0.75, steps=[("Beats & vocals", 1.0, "4 / 4"), ("Song structure", 0.5)])
        self.widget.begin("export", "Exporting video", 0.1)
        self.assertIs(popup.rows["analysis"], row)  # updated in place, not rebuilt
        self.assertEqual(row.percent.text(), "75%")
        self.assertEqual(row._step_widgets[1][1].value(), 500)
        self.assertEqual(list(popup.rows), ["analysis", "export"])  # start order, stable
        self.assertIn("2 active", popup.heading.text())

        self.widget.finish("export")
        self.assertEqual(list(popup.rows), ["analysis"])
        self.widget.finish("analysis")
        self.assertFalse(popup.isVisible())
        self.assertTrue(self.widget.isHidden())

    def test_details_text_and_accessibility_carry_everything_the_popup_shows(self) -> None:
        self.widget.begin("analysis", "AutoMix track analysis", 0.5, "Keep editing",
                          steps=[("Beats & vocals", None)])
        text = self.widget.details_text()
        self.assertIn("AutoMix track analysis — 50%", text)
        self.assertIn("Keep editing", text)
        self.assertIn("· Beats & vocals — In progress", text)
        self.assertEqual(self.widget.accessibleName(), "AutoMix track analysis 50%")
        self.assertIn("Beats & vocals", self.widget.accessibleDescription())
        self.widget.set_korean(True)
        self.assertIn("진행 중", self.widget.details_text())


if __name__ == "__main__":
    unittest.main()
