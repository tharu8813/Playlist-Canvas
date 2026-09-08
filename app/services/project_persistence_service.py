"""Small, UI-independent helpers for project persistence decisions."""

from __future__ import annotations

from pathlib import Path


def default_project_path(title: str, directory: Path | None = None) -> Path:
    """Build the default project filename without depending on Qt or widgets."""
    safe_title = "".join(
        character if character.isalnum() or character in " _-" else "_"
        for character in str(title)
    ).strip() or "playlist"
    return (directory or Path.cwd()) / f"{safe_title}.pvsproj"


def is_legacy_project_path(path: Path | None) -> bool:
    """Return whether a path uses the legacy JSON project format."""
    return path is not None and path.suffix.lower() == ".json"
