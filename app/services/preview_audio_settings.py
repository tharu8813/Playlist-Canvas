"""Shared audio preferences used by every in-app preview player."""

from __future__ import annotations

from PySide6.QtCore import QSettings


PREVIEW_VOLUME_KEY = "preview_volume"
DEFAULT_PREVIEW_VOLUME = 80


def preview_volume() -> int:
    """Return the persisted preview volume as a safe 0-100 percentage."""
    value = QSettings().value(PREVIEW_VOLUME_KEY, DEFAULT_PREVIEW_VOLUME, int)
    return max(0, min(100, int(value)))


def save_preview_volume(value: int) -> int:
    """Persist one volume value shared by track and full-video previews."""
    normalized = max(0, min(100, int(value)))
    QSettings().setValue(PREVIEW_VOLUME_KEY, normalized)
    return normalized
