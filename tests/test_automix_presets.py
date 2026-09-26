"""AutoMix's style is always automatic: one settings set, and saved presets are dropped on load."""

from __future__ import annotations

import unittest

from app.automix.settings import AUTOMIX_SETTINGS, AutoMixTransitionSettings
from app.models.project import ProjectDocument, ProjectSettings


class FixedAutoMixStyleTests(unittest.TestCase):
    def test_the_one_style_is_the_tuned_automatic_one(self) -> None:
        self.assertEqual(AUTOMIX_SETTINGS, AutoMixTransitionSettings(enabled=True))

    def test_a_project_saved_with_a_listening_preset_loads_with_the_automatic_style(self) -> None:
        data = ProjectDocument(settings=ProjectSettings(transition_mode="automix")).to_dict()
        self.assertNotIn("automix_preset", data["settings"])
        data["settings"]["automix_preset"] = "dj"  # saved by an older version
        loaded = ProjectDocument.from_dict(data)
        self.assertEqual(loaded.settings.transition_mode, "automix")
        self.assertFalse(hasattr(loaded.settings, "automix_preset"))


if __name__ == "__main__":
    unittest.main()
