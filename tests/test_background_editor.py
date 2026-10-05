from __future__ import annotations

import unittest

from app.inspector.editors import background_editor
from app.inspector.editors.base import TYPE_SPECIFIC_FIELD_KEYS
from app.models.source import Source, SourceType


class _Inspector:
    def __init__(self) -> None:
        self.visible: dict[str, bool] = {}

    def _set_field_visible(self, key: str, visible: bool) -> None:
        self.visible[key] = visible

    @staticmethod
    def _uses_primary_text_color(_source: Source) -> bool:
        return False

    @staticmethod
    def _hide_inactive_dependent_fields(_source: Source) -> None:
        return None

    @staticmethod
    def _refresh_property_tabs(_sources: list[Source]) -> None:
        return None


class BackgroundEditorTests(unittest.TestCase):
    def test_ambient_toggle_controls_all_detail_rows(self) -> None:
        inspector = _Inspector()
        source = Source(
            SourceType.BACKGROUND, "Background",
            background_mode="album_art", background_ambient=True,
            background_bass_reactive=True,
        )

        background_editor.edit(inspector, source)
        for key in (
            "background_ambient_blur", "background_ambient_motion",
            "background_bass_reactive", "background_bass_strength",
        ):
            self.assertTrue(inspector.visible[key], key)
            self.assertIn(key, TYPE_SPECIFIC_FIELD_KEYS)

        source.background_ambient = False
        background_editor.edit(inspector, source)
        self.assertFalse(inspector.visible["background_ambient_blur"])
        self.assertFalse(inspector.visible["background_bass_strength"])


if __name__ == "__main__":
    unittest.main()
