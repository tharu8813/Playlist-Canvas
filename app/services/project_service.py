"""Versioned JSON and portable single-file project persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import shutil
from tempfile import NamedTemporaryFile, gettempdir
import time
import zipfile

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage

from app.models.project import ProjectDocument


class ProjectError(Exception):
    """Raised when a project document cannot be safely saved or loaded."""


@dataclass(slots=True, frozen=True)
class ProjectSummary:
    """Cheap metadata used by the project chooser without loading media."""

    title: str
    description: str
    author: str
    modified_at: str
    thumbnail: bytes = b""


class ProjectService:
    """Read legacy JSON and portable ``.pvsproj`` project packages."""

    PACKAGE_SUFFIX = ".pvsproj"
    MANIFEST_NAME = "project.json"
    THUMBNAIL_NAME = "thumbnail.png"
    _ASSET_HASH_PREFIX = re.compile(r"^(?:[0-9a-fA-F]{12}_)+")
    _MAX_ASSET_BASENAME_LENGTH = 120

    @classmethod
    def _safe_asset_basename(cls, raw_name: str) -> str:
        """Return a stable Windows-safe basename without accumulated save hashes."""
        name = cls._ASSET_HASH_PREFIX.sub("", Path(raw_name).name)
        safe_name = "".join(
            char if char.isalnum() or char in "._- " else "_"
            for char in name
        ).rstrip(" .")
        if not safe_name:
            safe_name = "asset"
        suffix = Path(safe_name).suffix
        stem = safe_name[:-len(suffix)] if suffix else safe_name
        available = max(1, cls._MAX_ASSET_BASENAME_LENGTH - len(suffix))
        return f"{stem[:available].rstrip(' .') or 'asset'}{suffix}"

    @classmethod
    def _asset_archive_path(cls, identity: str, raw_name: str) -> str:
        digest = sha256(identity.casefold().encode("utf-8")).hexdigest()[:12]
        return f"assets/{digest}_{cls._safe_asset_basename(raw_name)}"

    @classmethod
    def save(
        cls, path: str | Path, document: ProjectDocument,
        thumbnail: QImage | None = None,
    ) -> Path:
        """Atomically save JSON or a portable package based on the file suffix."""
        target = Path(path).expanduser().resolve()
        if target.suffix.lower() not in {".json", cls.PACKAGE_SUFFIX}:
            target = target.with_suffix(cls.PACKAGE_SUFFIX)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.suffix.lower() == ".json":
                return cls._save_json(target, document)
            return cls._save_package(target, document, thumbnail)
        except (OSError, TypeError, ValueError, zipfile.BadZipFile) as error:
            raise ProjectError(f"Could not save project: {error}") from error

    @classmethod
    def load(cls, path: str | Path) -> ProjectDocument:
        """Load a package or a backwards-compatible UTF-8 JSON document."""
        target = Path(path).expanduser().resolve()
        try:
            if target.suffix.lower() == cls.PACKAGE_SUFFIX:
                return cls._load_package(target)
            with target.open("r", encoding="utf-8") as file:
                return ProjectDocument.from_dict(json.load(file))
        except (OSError, json.JSONDecodeError, TypeError, ValueError,
                KeyError, zipfile.BadZipFile) as error:
            raise ProjectError(f"Could not open project: {error}") from error

    @classmethod
    def inspect(cls, path: str | Path) -> ProjectSummary:
        """Read only project identity and thumbnail for the startup screen."""
        target = Path(path).expanduser().resolve()
        try:
            thumbnail = b""
            if target.suffix.lower() == cls.PACKAGE_SUFFIX:
                with zipfile.ZipFile(target, "r") as archive:
                    if archive.getinfo(cls.MANIFEST_NAME).file_size > 16 * 1024 * 1024:
                        raise ValueError("Project manifest is unexpectedly large.")
                    data = json.loads(archive.read(cls.MANIFEST_NAME).decode("utf-8"))
                    if cls.THUMBNAIL_NAME in archive.namelist():
                        if archive.getinfo(cls.THUMBNAIL_NAME).file_size > 32 * 1024 * 1024:
                            raise ValueError("Project thumbnail is unexpectedly large.")
                        thumbnail = archive.read(cls.THUMBNAIL_NAME)
            else:
                with target.open("r", encoding="utf-8") as file:
                    data = json.load(file)
            settings = data.get("settings", {}) if isinstance(data, dict) else {}
            return ProjectSummary(
                title=str(settings.get("title") or target.stem.removesuffix(".project")),
                description=str(settings.get("description", "")),
                author=str(settings.get("author", "")),
                modified_at=str(settings.get("modified_at", "")),
                thumbnail=thumbnail,
            )
        except (OSError, ValueError, KeyError, json.JSONDecodeError,
                zipfile.BadZipFile) as error:
            raise ProjectError(f"Could not inspect project: {error}") from error

    @staticmethod
    def _save_json(target: Path, document: ProjectDocument) -> Path:
        payload = json.dumps(document.to_dict(), ensure_ascii=False, indent=2)
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".tmp", dir=target.parent,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(payload)
                temporary.flush()
            temporary_path.replace(target)
        finally:
            # Antivirus, cloud sync, or a destination lock can make the atomic
            # replace fail. Never leave an accumulating project-sized .tmp file.
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        return target

    @classmethod
    def _save_package(
        cls, target: Path, document: ProjectDocument, thumbnail: QImage | None,
    ) -> Path:
        packaged = ProjectDocument.from_dict(document.to_dict())
        packaged.settings.modified_at = datetime.now(timezone.utc).isoformat()
        assets: dict[Path, str] = {}

        def register(raw_path: str) -> str:
            if not raw_path:
                return ""
            source_path = Path(raw_path).expanduser()
            if not source_path.is_file():
                return raw_path
            resolved = source_path.resolve()
            if resolved not in assets:
                assets[resolved] = cls._asset_archive_path(
                    str(resolved), resolved.name,
                )
            return assets[resolved]

        if packaged.settings.content_mode == "embed":
            for source in packaged.sources:
                source.content_path = register(source.content_path)
                source.font_path = register(source.font_path)
            for track in packaged.playlist:
                track.file_path = register(track.file_path)
                track.lyrics_path = register(track.lyrics_path)
                track.cover_path = register(track.cover_path)
            for content in packaged.content_library:
                content.path = register(content.path)

        payload = json.dumps(packaged.to_dict(), ensure_ascii=False, indent=2)
        required_bytes = sum(path.stat().st_size for path in assets)
        available_bytes = shutil.disk_usage(target.parent).free
        if required_bytes + len(payload.encode("utf-8")) > max(0, available_bytes - 512 * 1024 * 1024):
            raise OSError("Not enough free disk space to package project content safely.")
        with NamedTemporaryFile(suffix=".tmp", dir=target.parent, delete=False) as temp:
            temporary_path = Path(temp.name)
        try:
            with zipfile.ZipFile(
                temporary_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6,
            ) as archive:
                archive.writestr(cls.MANIFEST_NAME, payload.encode("utf-8"))
                for source_path, archive_path in assets.items():
                    # Most media is already compressed. Storing it directly avoids a
                    # long UI-blocking recompression pass and preserves byte quality.
                    archive.write(
                        source_path, archive_path, compress_type=zipfile.ZIP_STORED
                    )
                thumbnail_bytes = cls._thumbnail_bytes(thumbnail)
                if thumbnail_bytes:
                    archive.writestr(cls.THUMBNAIL_NAME, thumbnail_bytes)
            temporary_path.replace(target)
        finally:
            temporary_path.unlink(missing_ok=True)
        return target

    @classmethod
    def _load_package(cls, target: Path) -> ProjectDocument:
        fingerprint = sha256(
            f"{target}:{target.stat().st_mtime_ns}:{target.stat().st_size}".encode("utf-8")
        ).hexdigest()[:20]
        cache_root = Path(gettempdir()) / "PlaylistCanvas" / "project-cache" / fingerprint
        cls.cleanup_cache(keep={fingerprint})
        cache_root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, "r") as archive:
            manifest_info = archive.getinfo(cls.MANIFEST_NAME)
            if manifest_info.file_size > 16 * 1024 * 1024:
                raise ValueError("Project manifest is unexpectedly large.")
            asset_infos = [
                info for info in archive.infolist()
                if not info.is_dir() and PurePosixPath(info.filename).parts[:1] == ("assets",)
            ]
            if len(asset_infos) > 20_000:
                raise ValueError("Project package contains too many assets.")
            required_bytes = sum(info.file_size for info in asset_infos)
            if required_bytes > max(0, shutil.disk_usage(cache_root).free - 512 * 1024 * 1024):
                raise OSError("Not enough temporary disk space to open this project safely.")
            data = json.loads(archive.read(cls.MANIFEST_NAME).decode("utf-8"))
            extracted_assets: dict[str, Path] = {}
            for info in archive.infolist():
                parts = PurePosixPath(info.filename).parts
                if info.is_dir() or not parts or parts[0] != "assets":
                    continue
                if any(part in {"", ".", ".."} for part in parts):
                    raise ValueError("Unsafe asset path in project package.")
                archive_path = PurePosixPath(info.filename).as_posix()
                local_path = cls._asset_archive_path(archive_path, parts[-1])
                destination = cache_root.joinpath(*PurePosixPath(local_path).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)
                extracted_assets[archive_path] = destination
            thumbnail_cache = cache_root / cls.THUMBNAIL_NAME
            if cls.THUMBNAIL_NAME in archive.namelist():
                if archive.getinfo(cls.THUMBNAIL_NAME).file_size > 32 * 1024 * 1024:
                    raise ValueError("Project thumbnail is unexpectedly large.")
                thumbnail_cache.write_bytes(archive.read(cls.THUMBNAIL_NAME))
        document = ProjectDocument.from_dict(data)

        def resolved(raw_path: str) -> str:
            if not raw_path:
                return ""
            parts = PurePosixPath(raw_path).parts
            if parts and parts[0] == "assets":
                archive_path = PurePosixPath(raw_path).as_posix()
                candidate = extracted_assets.get(
                    archive_path, cache_root.joinpath(*parts),
                )
                return str(candidate.resolve())
            return raw_path

        for source in document.sources:
            source.content_path = resolved(source.content_path)
            source.font_path = resolved(source.font_path)
        for track in document.playlist:
            track.file_path = resolved(track.file_path)
            track.lyrics_path = resolved(track.lyrics_path)
            track.cover_path = resolved(track.cover_path)
        for content in document.content_library:
            content.path = resolved(content.path)
        if (document.settings.thumbnail_mode == "custom"
                and thumbnail_cache.is_file()):
            document.settings.thumbnail_path = str(thumbnail_cache.resolve())
        return document

    @classmethod
    def cleanup_cache(cls, max_age_days: int = 30,
                      keep: set[str] | None = None) -> None:
        """Remove stale extracted project packages from the application temp cache."""
        cutoff = time.time() - max(1, max_age_days) * 86_400
        retained = keep or set()
        roots = (
            Path(gettempdir()) / "PlaylistCanvas" / "project-cache",
            Path(gettempdir()) / "PlaylistVideoStudio" / "project-cache",
        )
        for root in roots:
            if not root.is_dir():
                continue
            for entry in root.iterdir():
                if entry.name in retained or not entry.is_dir():
                    continue
                try:
                    if entry.stat().st_mtime < cutoff:
                        shutil.rmtree(entry)
                except OSError:
                    # A cache may still be in use by another running editor window.
                    continue

    @staticmethod
    def _thumbnail_bytes(image: QImage | None) -> bytes:
        if image is None or image.isNull():
            return b""
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        buffer.close()
        return bytes(data)
