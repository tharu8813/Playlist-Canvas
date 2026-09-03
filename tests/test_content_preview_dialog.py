from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.dialogs.content_preview_dialog import ContentPreviewDialog


class ContentPreviewDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def _media_path(self, directory: str, suffix: str) -> Path:
        path = Path(directory) / f"preview{suffix}"
        path.write_bytes(b"preview-test")
        return path

    def test_audio_preview_exposes_complete_player_controls(self) -> None:
        with TemporaryDirectory() as directory, patch(
            "app.dialogs.content_preview_dialog.preview_volume", return_value=64,
        ):
            dialog = ContentPreviewDialog(
                str(self._media_path(directory, ".mp3")), "audio", True,
            )
            try:
                self.assertIsNotNone(dialog.player)
                self.assertIsNotNone(dialog.audio_output)
                self.assertEqual(dialog.volume_slider.value(), 64)
                self.assertEqual(dialog.volume_value_label.text(), "64%")
                self.assertTrue(dialog.play_button.text())
                self.assertTrue(dialog.stop_button.text())
                self.assertTrue(dialog.backward_button.text())
                self.assertTrue(dialog.forward_button.text())

                dialog._duration_changed(125_000)
                dialog._position_changed(65_000)
                self.assertEqual(dialog.position_slider.maximum(), 125_000)
                self.assertEqual(dialog.elapsed_label.text(), "01:05")
                self.assertEqual(dialog.duration_label.text(), "02:05")
            finally:
                dialog.done(0)
                self.application.processEvents()

    def test_video_preview_uses_the_same_seek_and_volume_controls(self) -> None:
        with TemporaryDirectory() as directory, patch(
            "app.dialogs.content_preview_dialog.preview_volume", return_value=42,
        ):
            dialog = ContentPreviewDialog(
                str(self._media_path(directory, ".mp4")), "video", False,
            )
            try:
                self.assertIsNotNone(dialog.player.videoOutput())
                self.assertEqual(dialog.volume_slider.value(), 42)
                dialog._duration_changed(3_661_000)
                self.assertEqual(dialog.duration_label.text(), "01:01:01")
            finally:
                dialog.done(0)
                self.application.processEvents()

    def test_volume_changes_are_shared_with_other_preview_players(self) -> None:
        with TemporaryDirectory() as directory, patch(
            "app.dialogs.content_preview_dialog.preview_volume", return_value=80,
        ), patch(
            "app.dialogs.content_preview_dialog.save_preview_volume",
            side_effect=lambda value: value,
        ) as save_volume:
            dialog = ContentPreviewDialog(
                str(self._media_path(directory, ".mp3")), "audio", False,
            )
            try:
                dialog.volume_slider.setValue(37)
                save_volume.assert_called_once_with(37)
                self.assertAlmostEqual(dialog.audio_output.volume(), 0.37, places=2)
                self.assertEqual(dialog.volume_value_label.text(), "37%")
            finally:
                dialog.done(0)
                self.application.processEvents()


if __name__ == "__main__":
    unittest.main()
