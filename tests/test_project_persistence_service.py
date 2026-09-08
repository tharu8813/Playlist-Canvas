import unittest
from pathlib import Path

from app.services.project_persistence_service import (
    default_project_path,
    is_legacy_project_path,
)


class ProjectPersistenceServiceTests(unittest.TestCase):
    def test_default_path_sanitizes_title_and_uses_requested_directory(self) -> None:
        path = default_project_path("  My / Playlist?  ", Path("exports"))
        self.assertEqual(path, Path("exports/My _ Playlist_.pvsproj"))

    def test_empty_title_has_stable_fallback(self) -> None:
        self.assertEqual(default_project_path("!!!", Path("exports")).name, "___.pvsproj")

    def test_legacy_format_detection_is_path_only(self) -> None:
        self.assertTrue(is_legacy_project_path(Path("old.project.json")))
        self.assertTrue(is_legacy_project_path(Path("old.JSON")))
        self.assertFalse(is_legacy_project_path(Path("new.pvsproj")))
        self.assertFalse(is_legacy_project_path(None))


if __name__ == "__main__":
    unittest.main()
