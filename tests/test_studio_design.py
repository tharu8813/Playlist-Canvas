"""Contracts for the dark-only skin and packaged stylesheet resources."""

import unittest
from pathlib import Path

from app.ui.design_system import studio_stylesheet


class StudioDesignTests(unittest.TestCase):
    def test_stylesheet_resolves_tokens_and_icon_assets(self) -> None:
        import re
        sheet = studio_stylesheet()
        self.assertNotRegex(sheet, r"@[a-z_]+")
        for path in re.findall(r'url\("([^"]+)"\)', sheet):
            self.assertTrue(Path(path).is_file(), path)
        self.assertIn("QPushButton:focus", sheet)
        self.assertIn("QPlainTextEdit:focus", sheet)
        self.assertIn("QPushButton:default {", sheet)
        self.assertNotIn("QPushButton:default, QPushButton[primary=\"true\"]", sheet)

    def test_distribution_includes_shared_stylesheet(self) -> None:
        spec = (Path(__file__).resolve().parents[1] / "playlist_canvas.spec").read_text(encoding="utf-8")
        self.assertIn('"studio.qss"', spec)
        self.assertIn('"check.svg"', spec)
