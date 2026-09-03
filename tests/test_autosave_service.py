from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.models.project import ProjectDocument
from app.models.playlist import PlaylistTrack
from app.services.autosave_service import AutosaveService
from app.services.project_service import ProjectError


def _document() -> ProjectDocument:
    return ProjectDocument(
        playlist=[PlaylistTrack("C:/music/a.mp3", "A", "Artist", "Album",
                                duration_seconds=120.0)],
    )


class AutosaveServiceTests(unittest.TestCase):
    def test_save_round_trips_through_recoveries(self) -> None:
        with TemporaryDirectory(prefix="pvs-autosave-") as directory:
            service = AutosaveService(Path(directory))
            snapshot = service.save(_document(), None)
            self.assertTrue(snapshot.path.is_file())
            loaded = service.latest_recovery()
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.document.playlist[0].title, "A")

    def test_write_document_data_matches_save_and_is_dict_only(self) -> None:
        with TemporaryDirectory(prefix="pvs-autosave-") as directory:
            service = AutosaveService(Path(directory))
            data = _document().to_dict()
            saved_at = datetime(2026, 9, 4, tzinfo=UTC)
            target = service.write_document_data(data, None, saved_at)
            self.assertTrue(target.is_file())
            recovered = service.latest_recovery()
            self.assertEqual(recovered.saved_at, saved_at)
            self.assertEqual(recovered.document.playlist[0].title, "A")

    def test_write_document_data_leaves_no_temp_file_on_failure(self) -> None:
        with TemporaryDirectory(prefix="pvs-autosave-") as directory:
            service = AutosaveService(Path(directory))
            with self.assertRaises(ProjectError):
                # object() is not JSON serializable -> json.dump raises TypeError
                service.write_document_data({"document": object()}, None)
            leftovers = list(service.directory.glob("*.tmp"))
            self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
