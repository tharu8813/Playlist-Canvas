from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
import zipfile

from PySide6.QtGui import QColor, QImage

from app import __version__
from app.models.playlist import PlaylistTrack
from app.models.project import ProjectDocument, ProjectSettings
from app.models.source import Source, SourceType
from app.services.project_service import ProjectService
from app.services.project_media_service import ProjectMediaService


class ProjectServiceTests(unittest.TestCase):
    def test_missing_custom_track_cover_can_be_relinked_or_cleared(self) -> None:
        with TemporaryDirectory(prefix="pvs-cover-relink-") as raw_directory:
            directory = Path(raw_directory)
            audio = directory / "audio.wav"
            audio.write_bytes(b"audio")
            missing_cover = directory / "missing-cover.png"
            document = ProjectDocument(playlist=[PlaylistTrack(
                str(audio), "Track", cover_path=str(missing_cover),
            )])
            missing = ProjectMediaService.validate(
                document, directory / "project.pvsproj",
            )
            self.assertEqual([entry.kind for entry in missing], ["cover"])

            replacement = directory / "replacement.png"
            image = QImage(32, 32, QImage.Format.Format_ARGB32)
            image.fill(QColor("#2563EB"))
            self.assertTrue(image.save(str(replacement)))
            missing[0].replacement_path = str(replacement)
            ProjectMediaService.apply_replacements(document, missing)
            self.assertEqual(
                document.playlist[0].cover_path, str(replacement.resolve()),
            )

            missing[0].replacement_path = ""
            ProjectMediaService.apply_replacements(document, missing)
            self.assertEqual(document.playlist[0].cover_path, "")

    def test_portable_package_embeds_and_reloads_content(self) -> None:
        with TemporaryDirectory(prefix="pvs-project-test-") as raw_directory:
            directory = Path(raw_directory)
            cover = directory / "cover.png"
            image = QImage(64, 64, QImage.Format.Format_ARGB32)
            image.fill(QColor("#243B55"))
            self.assertTrue(image.save(str(cover), "PNG"))
            audio = directory / "audio.wav"
            audio.write_bytes(b"test-audio-fixture")
            track_cover = directory / "track-cover.png"
            self.assertTrue(image.save(str(track_cover), "PNG"))
            document = ProjectDocument(
                sources=[Source(
                    SourceType.BACKGROUND, "Cover", content_path=str(cover),
                    background_mode="image",
                )],
                playlist=[PlaylistTrack(
                    str(audio), "Track", cover_path=str(track_cover),
                )],
                settings=ProjectSettings(title="Round trip", content_mode="embed"),
            )
            package = ProjectService.save(directory / "round-trip.pvsproj", document, image)
            restored = ProjectService.load(package)
            summary = ProjectService.inspect(package)
            self.assertTrue(Path(restored.sources[0].content_path).is_file())
            self.assertTrue(Path(restored.playlist[0].file_path).is_file())
            self.assertTrue(Path(restored.playlist[0].cover_path).is_file())
            self.assertEqual(
                Path(restored.playlist[0].cover_path).read_bytes(),
                track_cover.read_bytes(),
            )
            self.assertEqual(summary.title, "Round trip")
            self.assertTrue(summary.thumbnail)
            with zipfile.ZipFile(package) as archive:
                manifest = json.loads(archive.read(ProjectService.MANIFEST_NAME))
            self.assertEqual(manifest["app_version"], __version__)

    def test_repeated_package_saves_do_not_accumulate_hash_prefixes(self) -> None:
        with TemporaryDirectory(prefix="pvs-project-resave-") as raw_directory:
            directory = Path(raw_directory)
            audio = directory / "01. A Tribe Called Jazzyfact.m4a"
            audio.write_bytes(b"audio")
            package = directory / "repeated.pvsproj"
            document = ProjectDocument(
                playlist=[PlaylistTrack(str(audio), "Track")],
                settings=ProjectSettings(content_mode="embed"),
            )

            for _ in range(24):
                ProjectService.save(package, document)
                document = ProjectService.load(package)

            with zipfile.ZipFile(package) as archive:
                asset_names = [
                    Path(name).name for name in archive.namelist()
                    if name.startswith("assets/")
                ]
            self.assertEqual(len(asset_names), 1)
            self.assertLessEqual(
                len(asset_names[0]),
                ProjectService._MAX_ASSET_BASENAME_LENGTH + 13,
            )

    def test_legacy_package_with_overlong_asset_name_is_recovered(self) -> None:
        with TemporaryDirectory(prefix="pvs-project-long-name-") as raw_directory:
            directory = Path(raw_directory)
            package = directory / "legacy.pvsproj"
            long_name = f"{'1eebca161124_' * 22}01. Track.m4a"
            archive_path = f"assets/{long_name}"
            document = ProjectDocument(
                playlist=[PlaylistTrack(archive_path, "Track")],
                settings=ProjectSettings(content_mode="embed"),
            )
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr(
                    ProjectService.MANIFEST_NAME,
                    json.dumps(document.to_dict()).encode("utf-8"),
                )
                archive.writestr(archive_path, b"legacy-audio")

            restored = ProjectService.load(package)
            restored_audio = Path(restored.playlist[0].file_path)
            self.assertTrue(restored_audio.is_file())
            self.assertEqual(restored_audio.read_bytes(), b"legacy-audio")
            self.assertLessEqual(
                len(restored_audio.name),
                ProjectService._MAX_ASSET_BASENAME_LENGTH + 13,
            )


if __name__ == "__main__":
    unittest.main()
