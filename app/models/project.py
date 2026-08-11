"""Serializable document model for a Playlist Canvas project."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from math import isfinite
from typing import Any
from uuid import uuid4

from app.models.playlist import PlaylistTrack
from app.models.layer import LayerGroup
from app.models.source import Source


@dataclass(slots=True)
class CanvasSettings:
    """Persisted canvas state relevant to Phase 1 editing."""

    width: float = 1280.0
    height: float = 720.0
    show_grid: bool = True
    snap_enabled: bool = True
    zoom: float = 1.0


@dataclass(slots=True)
class ProjectSettings:
    """Project identity and portable-content policy."""

    title: str = "Untitled Project"
    description: str = ""
    author: str = ""
    content_mode: str = "embed"
    thumbnail_mode: str = "canvas"
    thumbnail_path: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    modified_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def __post_init__(self) -> None:
        if self.content_mode not in {"embed", "reference"}:
            self.content_mode = "embed"
        if self.thumbnail_mode not in {"canvas", "custom"}:
            self.thumbnail_mode = "canvas"


@dataclass(slots=True)
class ProjectContent:
    """Reusable content registered in one project's content library."""

    path: str
    media_type: str
    name: str = ""
    id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if not self.name:
            from pathlib import Path
            self.name = Path(self.path).stem


@dataclass(slots=True)
class ProjectDocument:
    """Complete Playlist Canvas project document."""

    sources: list[Source] = field(default_factory=list)
    groups: list[LayerGroup] = field(default_factory=list)
    playlist: list[PlaylistTrack] = field(default_factory=list)
    canvas: CanvasSettings = field(default_factory=CanvasSettings)
    theme: str = "dark"
    language: str = "ko"
    settings: ProjectSettings = field(default_factory=ProjectSettings)
    content_library: list[ProjectContent] = field(default_factory=list)
    version: int = 2

    def to_dict(self) -> dict[str, Any]:
        """Convert the document into JSON-compatible data."""
        return {
            "version": self.version,
            "canvas": asdict(self.canvas),
            "theme": self.theme,
            "language": self.language,
            "settings": asdict(self.settings),
            "content_library": [asdict(item) for item in self.content_library],
            "sources": [source.to_dict() for source in self.sources],
            "groups": [group.to_dict() for group in self.groups],
            "playlist": [track.to_dict() for track in self.playlist],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectDocument":
        """Create a validated document from parsed JSON data."""
        if not isinstance(data, dict):
            raise ValueError("Project root must be an object.")
        version = int(data.get("version", 1))
        if version not in {1, 2}:
            raise ValueError("This project version is not supported.")
        sources = data.get("sources", [])
        groups = data.get("groups", [])
        playlist = data.get("playlist", [])
        canvas = data.get("canvas", {})
        content_library = data.get("content_library", [])
        if not all(isinstance(entry, dict) for entry in sources):
            raise ValueError("Project sources must be objects.")
        if not all(isinstance(entry, dict) for entry in groups):
            raise ValueError("Project groups must be objects.")
        if not all(isinstance(entry, dict) for entry in playlist):
            raise ValueError("Project playlist tracks must be objects.")
        if not isinstance(canvas, dict):
            raise ValueError("Project canvas settings must be an object.")
        if not all(isinstance(entry, dict) for entry in content_library):
            raise ValueError("Project content entries must be objects.")
        if len(sources) > 20_000 or len(groups) > 20_000 or len(playlist) > 20_000:
            raise ValueError("The project contains too many sources, groups, or tracks.")
        settings_data = data.get("settings", {})
        if not isinstance(settings_data, dict):
            settings_data = {}
        canvas_model = CanvasSettings(**canvas)
        for name in ("width", "height", "zoom"):
            value = getattr(canvas_model, name)
            if (not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not isfinite(float(value))):
                raise ValueError(f"Canvas '{name}' must be a finite number.")
        if not 64 <= canvas_model.width <= 16_384 or not 64 <= canvas_model.height <= 16_384:
            raise ValueError("Canvas dimensions must be between 64 and 16384 pixels.")
        if not 0.05 <= canvas_model.zoom <= 20:
            raise ValueError("Canvas zoom must be between 0.05 and 20.")
        if not isinstance(canvas_model.show_grid, bool) or not isinstance(canvas_model.snap_enabled, bool):
            raise ValueError("Canvas grid and snap settings must be booleans.")

        if settings_data.get("content_mode", "embed") not in {"embed", "reference"}:
            raise ValueError("Project content mode must be 'embed' or 'reference'.")
        if settings_data.get("thumbnail_mode", "canvas") not in {"canvas", "custom"}:
            raise ValueError("Project thumbnail mode must be 'canvas' or 'custom'.")
        settings_model = ProjectSettings(**settings_data)
        if not all(isinstance(getattr(settings_model, name), str) for name in (
            "title", "description", "author", "thumbnail_path", "created_at", "modified_at",
        )):
            raise ValueError("Project identity and timestamp fields must be strings.")

        source_models = [Source.from_dict(entry) for entry in sources]
        group_models = [LayerGroup.from_dict(entry) for entry in groups]
        track_models = [PlaylistTrack.from_dict(entry) for entry in playlist]
        content_models = [ProjectContent(**entry) for entry in content_library]

        def require_unique_ids(items: list[object], label: str) -> set[str]:
            identifiers: list[str] = []
            for item in items:
                identifier = getattr(item, "id", None)
                if not isinstance(identifier, str) or not identifier.strip():
                    raise ValueError(f"Every {label} must have a non-empty string ID.")
                identifiers.append(identifier)
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"Project {label} IDs must be unique.")
            return set(identifiers)

        source_ids = require_unique_ids(source_models, "source")
        group_ids = require_unique_ids(group_models, "group")
        require_unique_ids(track_models, "playlist track")
        require_unique_ids(content_models, "content item")
        if len(source_ids) != len(source_models):
            raise ValueError("Project source IDs must be unique.")
        for source in source_models:
            if source.group_id is not None and source.group_id not in group_ids:
                raise ValueError(f"Source '{source.name}' references an unknown group.")
        for content in content_models:
            if not isinstance(content.path, str) or not isinstance(content.name, str):
                raise ValueError("Project content paths and names must be strings.")
            if content.media_type not in {"audio", "image", "font", "lyrics"}:
                raise ValueError("Project content media type is not supported.")

        theme = data.get("theme", "dark")
        language = data.get("language", "ko")
        if theme not in {"light", "dark", "auto"}:
            raise ValueError("Project theme is not supported.")
        if language not in {"ko", "en"}:
            raise ValueError("Project language is not supported.")

        return cls(
            version=2,
            sources=source_models,
            groups=group_models,
            playlist=track_models,
            canvas=canvas_model,
            theme=theme,
            language=language,
            settings=settings_model,
            content_library=content_models,
        )
