"""Verified per-user FFmpeg installer for supported Windows desktops."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen

from app import __version__
from app.utils.subprocess_utils import hidden_process_kwargs


class FFmpegInstallError(RuntimeError):
    """Raised when a managed FFmpeg installation cannot be completed safely."""


class FFmpegInstallCancelled(FFmpegInstallError):
    """Raised after a user cancels a managed installation."""


@dataclass(frozen=True, slots=True)
class ManagedFFmpegInstallation:
    """Verified executable and version returned by a completed installation."""

    executable: Path
    version: str


ProgressCallback = Callable[[str, float, str], None]


class ManagedFFmpegInstaller:
    """Downloads BtbN's GPL Windows build and verifies its release checksum."""

    release_api_url = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest"
    archive_name = "ffmpeg-master-latest-win64-gpl.zip"
    checksum_name = "checksums.sha256"
    max_text_bytes = 16 * 1024 * 1024
    max_archive_bytes = 2 * 1024 * 1024 * 1024
    max_extracted_bytes = 8 * 1024 * 1024 * 1024
    max_archive_members = 100_000

    def __init__(self, install_root: Path | None = None) -> None:
        self.install_root = install_root or self.default_install_root()

    @staticmethod
    def default_install_root() -> Path:
        """Return the app-only Windows installation folder without requiring admin rights."""
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / "PlaylistCanvas" / "tools" / "ffmpeg"

    def current_installation(self) -> ManagedFFmpegInstallation | None:
        """Read the latest completed managed installation, if it still validates."""
        manifest = self.install_root / "current.json"
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            executable = Path(str(data["executable"]))
            version = str(data["version"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        return ManagedFFmpegInstallation(executable, version) if self._is_runnable(executable) else None

    def install_latest(self, progress: ProgressCallback | None = None,
                       cancel_event: threading.Event | None = None) -> ManagedFFmpegInstallation:
        """Download, checksum-verify, extract, test, and atomically activate FFmpeg."""
        cancelled = cancel_event or threading.Event()
        self._report(progress, "Preparing download", 0.02, "Reading the verified release manifest")
        tag, archive_url, checksum_url = self._release_assets(cancelled, progress)

        self.install_root.mkdir(parents=True, exist_ok=True)
        target = self.install_root / "versions" / self._safe_version(tag)
        if target.exists():
            executable = self._find_executable(target)
            if executable and self._is_runnable(executable):
                installation = ManagedFFmpegInstallation(executable, tag)
                self._activate(installation)
                self._report(progress, "Complete", 1.0, "Using the existing verified FFmpeg version")
                return installation
            raise FFmpegInstallError(
                "An incomplete FFmpeg version folder already exists. Remove it manually before retrying."
            )

        with TemporaryDirectory(prefix="ffmpeg-download-", dir=self.install_root) as temporary:
            temporary_directory = Path(temporary)
            self._report(progress, "Preparing download", 0.05, "Downloading the checksum manifest")
            checksum_text = self._download_text(checksum_url, cancelled)
            expected_hash = self._checksum_for_archive(checksum_text, self.archive_name)
            if expected_hash is None:
                raise FFmpegInstallError("The release checksum manifest does not list the FFmpeg archive.")
            self._report(progress, "Preparing download", 0.07, "Checksum found; starting FFmpeg download")
            archive = temporary_directory / self.archive_name
            self._download_file(archive_url, archive, progress, cancelled)
            self._raise_if_cancelled(cancelled)
            actual_hash = self._sha256(archive)
            if actual_hash.lower() != expected_hash.lower():
                raise FFmpegInstallError("FFmpeg archive checksum verification failed; no files were installed.")
            self._report(progress, "Extracting", 0.86, "Checksum verified; extracting archive safely")
            extracted = temporary_directory / "extracted"
            self._safe_extract(archive, extracted)
            executable = self._find_executable(extracted)
            if executable is None or not self._is_runnable(executable):
                raise FFmpegInstallError("The verified archive did not contain a runnable ffmpeg.exe.")
            self._raise_if_cancelled(cancelled)
            target.parent.mkdir(parents=True, exist_ok=True)
            staging = target.parent / f".{target.name}-{uuid.uuid4().hex}.installing"
            shutil.move(str(executable.parent.parent), str(staging))
            try:
                staging.replace(target)
            except OSError as error:
                shutil.rmtree(staging, ignore_errors=True)
                raise FFmpegInstallError("Could not activate the verified FFmpeg installation.") from error
            final_executable = self._find_executable(target)
            if final_executable is None or not self._is_runnable(final_executable):
                raise FFmpegInstallError("FFmpeg failed its final execution check after installation.")
            installation = ManagedFFmpegInstallation(final_executable, tag)
            self._activate(installation)
        self._report(progress, "Complete", 1.0, "FFmpeg was installed and verified")
        return installation

    def _release_assets(
        self, cancelled: threading.Event, progress: ProgressCallback | None = None,
    ) -> tuple[str, str, str]:
        """Resolve release assets, falling back to BtbN's stable latest URLs."""
        try:
            release = self._read_json(self.release_api_url, cancelled)
            tag = str(release.get("tag_name", "")).strip()
            assets = {
                str(asset.get("name", "")): str(asset.get("browser_download_url", ""))
                for asset in release.get("assets", []) if isinstance(asset, dict)
            }
            archive_url = assets.get(self.archive_name, "")
            checksum_url = assets.get(self.checksum_name, "")
            if tag and archive_url and checksum_url:
                return tag, archive_url, checksum_url
        except FFmpegInstallCancelled:
            raise
        except FFmpegInstallError:
            pass

        # The floating `latest` release is maintained by the same upstream and
        # contains both the archive and checksum. This also avoids GitHub API
        # rate-limit failures while retaining end-to-end SHA-256 verification.
        self._report(
            progress, "Preparing download", 0.04,
            "GitHub API unavailable; using the official latest release links",
        )
        base = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest"
        return "latest", f"{base}/{self.archive_name}", f"{base}/{self.checksum_name}"

    def _read_json(self, url: str, cancelled: threading.Event) -> dict[str, object]:
        self._raise_if_cancelled(cancelled)
        try:
            return json.loads(self._download_text(url, cancelled))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise FFmpegInstallError("Could not retrieve the FFmpeg release manifest.") from error

    def _download_text(self, url: str, cancelled: threading.Event) -> str:
        self._raise_if_cancelled(cancelled)
        request = Request(url, headers={"User-Agent": f"PlaylistCanvas/{__version__}"})
        try:
            with urlopen(request, timeout=30) as response:
                declared_length = self._content_length(response.headers.get("Content-Length"))
                if declared_length > self.max_text_bytes:
                    raise FFmpegInstallError("The FFmpeg release manifest is unexpectedly large.")
                data = response.read(self.max_text_bytes + 1)
                if len(data) > self.max_text_bytes:
                    raise FFmpegInstallError("The FFmpeg release manifest is unexpectedly large.")
        except OSError as error:
            raise FFmpegInstallError("The FFmpeg download could not be reached.") from error
        self._raise_if_cancelled(cancelled)
        return data.decode("utf-8")

    def _download_file(self, url: str, destination: Path, progress: ProgressCallback | None,
                       cancelled: threading.Event) -> None:
        request = Request(url, headers={"User-Agent": f"PlaylistCanvas/{__version__}"})
        try:
            with urlopen(request, timeout=30) as response, destination.open("wb") as stream:
                length = self._content_length(response.headers.get("Content-Length"))
                if length > self.max_archive_bytes:
                    raise FFmpegInstallError("The FFmpeg archive is unexpectedly large.")
                downloaded = 0
                while chunk := response.read(1024 * 1024):
                    self._raise_if_cancelled(cancelled)
                    downloaded += len(chunk)
                    if downloaded > self.max_archive_bytes:
                        raise FFmpegInstallError("The FFmpeg archive exceeded the safe download limit.")
                    stream.write(chunk)
                    fraction = 0.08 + 0.72 * downloaded / length if length else 0.12
                    self._report(progress, "Downloading FFmpeg", min(0.80, fraction),
                                 f"Downloaded {downloaded / 1024 / 1024:.1f} MB")
                if length and downloaded != length:
                    raise FFmpegInstallError("The FFmpeg archive download ended before it was complete.")
        except OSError as error:
            raise FFmpegInstallError("The FFmpeg archive download failed.") from error

    @staticmethod
    def _content_length(value: object) -> int:
        try:
            return max(0, int(str(value))) if value is not None else 0
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _checksum_for_archive(manifest: str, archive_name: str) -> str | None:
        for line in manifest.splitlines():
            fields = line.strip().replace("*", "").split()
            if len(fields) >= 2 and fields[1] == archive_name and len(fields[0]) == 64:
                return fields[0]
        return None

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _safe_extract(self, archive: Path, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(archive) as package:
                root = destination.resolve()
                members = package.infolist()
                if len(members) > self.max_archive_members:
                    raise FFmpegInstallError("The FFmpeg archive contains too many files.")
                total_size = 0
                for member in members:
                    total_size += member.file_size
                    if total_size > self.max_extracted_bytes:
                        raise FFmpegInstallError("The FFmpeg archive expands beyond the safe size limit.")
                    resolved = (destination / member.filename).resolve()
                    if not resolved.is_relative_to(root):
                        raise FFmpegInstallError("Unsafe path found in FFmpeg archive.")
                package.extractall(destination)
        except (OSError, zipfile.BadZipFile) as error:
            raise FFmpegInstallError("The FFmpeg archive could not be extracted.") from error

    @staticmethod
    def _find_executable(directory: Path) -> Path | None:
        return next(directory.rglob("ffmpeg.exe"), None)

    @staticmethod
    def _is_runnable(executable: Path) -> bool:
        if not executable.is_file():
            return False
        try:
            return subprocess.run(
                [str(executable), "-version"], capture_output=True, timeout=10, check=False,
                **hidden_process_kwargs(),
            ).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _activate(self, installation: ManagedFFmpegInstallation) -> None:
        manifest = self.install_root / "current.json"
        temporary = manifest.with_suffix(".json.tmp")
        try:
            temporary.write_text(json.dumps({
                "version": installation.version,
                "executable": str(installation.executable),
                "source": "BtbN/FFmpeg-Builds",
                "license": "GPL-3.0-or-later",
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(manifest)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _safe_version(value: str) -> str:
        return "".join(character if character.isalnum() or character in "._-" else "_" for character in value)

    @staticmethod
    def _raise_if_cancelled(cancelled: threading.Event) -> None:
        if cancelled.is_set():
            raise FFmpegInstallCancelled("FFmpeg installation was cancelled.")

    @staticmethod
    def _report(callback: ProgressCallback | None, stage: str, fraction: float, message: str) -> None:
        if callback:
            callback(stage, fraction, message)
