from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
import zipfile
from unittest.mock import patch

from PySide6.QtGui import QColor, QImage

from app import __version__
from app.models.playlist import PlaylistTrack
from app.models.project import ProjectDocument, ProjectSettings
from app.models.source import Source, SourceType
from app.services.project_service import ProjectError, ProjectLoadCancelled, ProjectService
from app.services.project_media_service import ProjectMediaService


class ProjectServiceTests(unittest.TestCase):
    def test_relative_media_prefers_project_folder_over_working_directory(self) -> None:
        with TemporaryDirectory(prefix="pvs-relative-") as directory:
            project_directory = Path(directory)
            media = project_directory / "track.wav"
            media.write_bytes(b"project audio")
            with patch.object(Path, "is_file", return_value=True):
                resolved = ProjectMediaService._resolve_existing_path(
                    "track.wav", project_directory,
                )
            self.assertEqual(resolved, media.resolve())

    def test_inspect_invalid_settings_reports_project_error(self) -> None:
        with TemporaryDirectory(prefix="pvs-inspect-") as directory:
            path = Path(directory) / "invalid.json"
            for settings in (None, [], "invalid", 42):
                with self.subTest(settings=settings):
                    path.write_text(json.dumps({"settings": settings}), encoding="utf-8")
                    with self.assertRaises(ProjectError):
                        ProjectService.inspect(path)

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

    def test_package_load_reports_byte_progress_and_can_be_cancelled(self) -> None:
        import threading

        with TemporaryDirectory(prefix="pvs-project-progress-") as raw_directory:
            directory = Path(raw_directory)
            audio = directory / "audio.wav"
            audio.write_bytes(b"x" * (3 * 1024 * 1024 + 7))  # several chunks
            package = ProjectService.save(
                directory / "progress.pvsproj",
                ProjectDocument(
                    playlist=[PlaylistTrack(str(audio), "Track")],
                    settings=ProjectSettings(content_mode="embed"),
                ),
            )
            reports: list[tuple[int, int]] = []
            ProjectService.load(package, progress=lambda done, total: reports.append((done, total)))
            total = audio.stat().st_size
            self.assertEqual(reports[0], (0, total))
            self.assertEqual(reports[-1], (total, total))
            self.assertEqual([done for done, _ in reports], sorted(done for done, _ in reports))

            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(ProjectLoadCancelled):
                ProjectService.load(package, cancel_event=cancel)
            self.assertTrue(issubclass(ProjectLoadCancelled, ProjectError))

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

    @staticmethod
    def _write_package(package: Path, track_path: str, entries: dict[str, bytes]) -> None:
        document = ProjectDocument(
            playlist=[PlaylistTrack(track_path, "Track")],
            settings=ProjectSettings(content_mode="embed"),
        )
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr(
                ProjectService.MANIFEST_NAME,
                json.dumps(document.to_dict()).encode("utf-8"),
            )
            for name, payload in entries.items():
                archive.writestr(name, payload)

    def test_package_rejects_internal_references_that_were_not_extracted(self) -> None:
        with TemporaryDirectory(prefix="pvs-project-unsafe-") as raw_directory:
            directory = Path(raw_directory)
            (directory / "secret.txt").write_text("secret", encoding="utf-8")
            real = {"assets/0123456789ab_track.mp3": b"audio"}
            for reference in (
                "assets/../../secret.txt",
                "assets/../secret.txt",
                "assets/missing.mp3",
                "assets\\..\\..\\secret.txt",
                "assets/%2e%2e/secret.txt",
                "assets//0123456789ab_track.mp3",
                "assets/./0123456789ab_track.mp3",
                "Assets/0123456789ab_track.mp3",
                "assets",
            ):
                with self.subTest(reference=reference):
                    package = directory / "unsafe.pvsproj"
                    self._write_package(package, reference, real)
                    with self.assertRaises(ProjectError):
                        ProjectService.load(package)

    def test_package_rejects_duplicate_and_case_colliding_asset_entries(self) -> None:
        with TemporaryDirectory(prefix="pvs-project-dupe-") as raw_directory:
            package = Path(raw_directory) / "dupe.pvsproj"
            self._write_package(package, "assets/a_track.mp3", {
                "assets/a_track.mp3": b"one", "assets/A_track.mp3": b"two",
            })
            with self.assertRaises(ProjectError):
                ProjectService.load(package)
            with patch("warnings.warn"):  # zipfile warns on duplicate names
                self._write_package(package, "assets/a_track.mp3", {})
                with zipfile.ZipFile(package, "a") as archive:
                    archive.writestr("assets/a_track.mp3", b"one")
                    archive.writestr("assets/a_track.mp3", b"two")
            with self.assertRaises(ProjectError):
                ProjectService.load(package)

    def test_package_keeps_valid_internal_and_external_references(self) -> None:
        with TemporaryDirectory(prefix="pvs-project-valid-") as raw_directory:
            directory = Path(raw_directory)
            package = directory / "valid.pvsproj"
            name = "assets/0123456789ab_곡 ♪.mp3"
            self._write_package(package, name, {name: b"audio"})
            restored = Path(ProjectService.load(package).playlist[0].file_path)
            self.assertEqual(restored.read_bytes(), b"audio")

            # A leading slash is an (external) absolute path, never an asset.
            external = str((directory / "outside.mp3").resolve())
            for reference in ("/assets/file.mp3", external):
                with self.subTest(reference=reference):
                    self._write_package(package, reference, {})
                    self.assertEqual(
                        ProjectService.load(package).playlist[0].file_path, reference,
                    )

    def test_legacy_json_keeps_external_absolute_paths(self) -> None:
        with TemporaryDirectory(prefix="pvs-project-json-") as raw_directory:
            directory = Path(raw_directory)
            external = str((directory / "elsewhere" / "track.mp3").resolve())
            project = directory / "legacy.json"
            project.write_text(json.dumps(ProjectDocument(
                playlist=[PlaylistTrack(external, "Track")],
            ).to_dict()), encoding="utf-8")
            self.assertEqual(ProjectService.load(project).playlist[0].file_path, external)


if __name__ == "__main__":
    unittest.main()
