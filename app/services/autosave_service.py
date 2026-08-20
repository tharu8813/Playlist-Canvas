"""Project-scoped automatic backup and crash-recovery management."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.models.project import ProjectDocument
from app.services.project_service import ProjectError


@dataclass(frozen=True, slots=True)
class RecoverySnapshot:
    """One durable recovery document plus the project it belongs to."""

    path: Path
    document: ProjectDocument
    project_path: Path | None
    saved_at: datetime


class AutosaveService:
    """Maintain separate, atomic crash-recovery snapshots for each project."""

    schema_version = 1

    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve() / "recoveries"

    def save(self, document: ProjectDocument, project_path: Path | None) -> RecoverySnapshot:
        """Atomically store a snapshot without changing the user's project file."""
        target = self._path_for(project_path)
        saved_at = datetime.now(UTC)
        payload = {
            "schema_version": self.schema_version,
            "saved_at": saved_at.isoformat(),
            "project_path": str(project_path.resolve()) if project_path else None,
            "document": document.to_dict(),
        }
        temporary_path: Path | None = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".tmp", dir=self.directory,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(payload, temporary, ensure_ascii=False, indent=2)
                temporary.flush()
            temporary_path.replace(target)
            return RecoverySnapshot(target, document, project_path, saved_at)
        except (OSError, TypeError, ValueError) as error:
            raise ProjectError(f"Could not save recovery snapshot: {error}") from error
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def latest_recovery(self) -> RecoverySnapshot | None:
        """Return the newest valid recovery snapshot, leaving it intact for retry."""
        return next(iter(self.recoveries()), None)

    def recoveries(self) -> list[RecoverySnapshot]:
        """Return every valid recovery snapshot from newest to oldest."""
        if not self.directory.is_dir():
            return []
        snapshots: list[RecoverySnapshot] = []
        for path in self.directory.glob("*.recovery.json"):
            try:
                snapshots.append(self._load(path))
            except ProjectError:
                # A damaged recovery must not block valid snapshots from other projects.
                continue
        return sorted(snapshots, key=lambda snapshot: snapshot.saved_at, reverse=True)

    def clear(self, project_path: Path | None) -> None:
        """Discard only the snapshot belonging to the specified workspace."""
        target = self._path_for(project_path)
        try:
            if target.is_file():
                target.unlink()
        except OSError as error:
            raise ProjectError(f"Could not remove recovery snapshot: {error}") from error

    def clear_snapshot(self, snapshot: RecoverySnapshot) -> None:
        """Discard an explicitly selected snapshot after the user confirms it."""
        try:
            if snapshot.path.is_file():
                snapshot.path.unlink()
        except OSError as error:
            raise ProjectError(f"Could not remove recovery snapshot: {error}") from error

    def _path_for(self, project_path: Path | None) -> Path:
        identity = str(project_path.resolve()) if project_path else "untitled-workspace"
        digest = sha256(identity.encode("utf-8")).hexdigest()[:20]
        return self.directory / f"{digest}.recovery.json"

    @staticmethod
    def _load(path: Path) -> RecoverySnapshot:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("schema_version") != AutosaveService.schema_version:
                raise ValueError("Unsupported recovery schema")
            saved_at = datetime.fromisoformat(data["saved_at"])
            project_value = data.get("project_path")
            project_path = Path(project_value) if project_value else None
            return RecoverySnapshot(
                path=path,
                document=ProjectDocument.from_dict(data["document"]),
                project_path=project_path,
                saved_at=saved_at,
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ProjectError(f"Could not open recovery snapshot: {error}") from error
