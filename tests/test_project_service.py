from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PySide6.QtGui import QColor, QImage

from app.models.playlist import PlaylistTrack
from app.models.project import ProjectDocument, ProjectSettings
from app.models.source import Source, SourceType
from app.services.project_service import ProjectService


class ProjectServiceTests(unittest.TestCase):
    def test_portable_package_embeds_and_reloads_content(self) -> None:
        with TemporaryDirectory(prefix="pvs-project-test-") as raw_directory:
            directory = Path(raw_directory)
            cover = directory / "cover.png"
            image = QImage(64, 64, QImage.Format.Format_ARGB32)
            image.fill(QColor("#243B55"))
            self.assertTrue(image.save(str(cover), "PNG"))
            audio = directory / "audio.wav"
            audio.write_bytes(b"test-audio-fixture")
            document = ProjectDocument(
                sources=[Source(
                    SourceType.BACKGROUND, "Cover", content_path=str(cover),
                    background_mode="image",
                )],
                playlist=[PlaylistTrack(str(audio), "Track")],
                settings=ProjectSettings(title="Round trip", content_mode="embed"),
            )
            package = ProjectService.save(directory / "round-trip.pvsproj", document, image)
            restored = ProjectService.load(package)
            summary = ProjectService.inspect(package)
            self.assertTrue(Path(restored.sources[0].content_path).is_file())
            self.assertTrue(Path(restored.playlist[0].file_path).is_file())
            self.assertEqual(summary.title, "Round trip")
            self.assertTrue(summary.thumbnail)


if __name__ == "__main__":
    unittest.main()
