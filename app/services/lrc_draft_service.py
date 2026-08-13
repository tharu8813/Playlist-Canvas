"""Durable, audio-scoped autosave drafts for the LRC timing editor."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


class LrcDraftError(RuntimeError):
    """Raised when an LRC draft cannot be saved, loaded, or removed."""


@dataclass(frozen=True, slots=True)
class LrcDraft:
    path: Path
    audio_path: str
    saved_at: datetime
    data: dict[str, Any]


class LrcDraftService:
    """Atomically maintain one recovery draft for each audio file."""

    schema_version = 1

    def __init__(self, data_directory: Path) -> None:
        self.directory = data_directory.resolve() / "lrc-drafts"

    def save(self, audio_path: str, data: dict[str, Any]) -> LrcDraft:
        resolved_audio = str(Path(audio_path).expanduser().resolve())
        saved_at = datetime.now(UTC)
        target = self._path_for(resolved_audio)
        payload = {
            "schema_version": self.schema_version,
            "saved_at": saved_at.isoformat(),
            "audio_path": resolved_audio,
            "data": data,
        }
        temporary_path: Path | None = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".tmp",
                dir=self.directory, delete=False,
            ) as temporary:
                json.dump(payload, temporary, ensure_ascii=False, indent=2)
                temporary.flush()
                temporary_path = Path(temporary.name)
            temporary_path.replace(target)
            return LrcDraft(target, resolved_audio, saved_at, dict(data))
        except (OSError, TypeError, ValueError) as error:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise LrcDraftError(f"Could not save LRC recovery draft: {error}") from error

    def load(self, audio_path: str) -> LrcDraft | None:
        target = self._path_for(audio_path)
        if not target.is_file():
            return None
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
            if payload.get("schema_version") != self.schema_version:
                raise ValueError("Unsupported LRC draft schema")
            resolved_audio = str(Path(payload["audio_path"]).expanduser().resolve())
            expected_audio = str(Path(audio_path).expanduser().resolve())
            if resolved_audio != expected_audio:
                raise ValueError("LRC draft belongs to another audio file")
            data = payload["data"]
            if not isinstance(data, dict):
                raise ValueError("Invalid LRC draft data")
            return LrcDraft(
                target,
                resolved_audio,
                datetime.fromisoformat(payload["saved_at"]),
                data,
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise LrcDraftError(f"Could not open LRC recovery draft: {error}") from error

    def clear(self, audio_path: str) -> None:
        try:
            self._path_for(audio_path).unlink(missing_ok=True)
        except OSError as error:
            raise LrcDraftError(f"Could not remove LRC recovery draft: {error}") from error

    def _path_for(self, audio_path: str) -> Path:
        identity = str(Path(audio_path).expanduser().resolve())
        digest = sha256(identity.casefold().encode("utf-8")).hexdigest()[:24]
        return self.directory / f"{digest}.lrc-draft.json"
