"""Inspector editor for SourceType.BACKGROUND, split out of
SourceInspector._update_legacy_source_specific_fields."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.inspector.editors.base import (
    apply_image_backed_fields,
    apply_shared_fields,
    finish,
    hide_type_specific_fields,
)
from app.models.source import Source

if TYPE_CHECKING:
    from app.inspector.source_inspector import SourceInspector


def edit(inspector: "SourceInspector", source: Source) -> None:
    hide_type_specific_fields(inspector)
    inspector._set_field_visible("background_mode", True)
    apply_image_backed_fields(inspector, source, show_file=source.background_mode == "image")
    album_art_background = source.background_mode == "album_art"
    inspector._set_field_visible("background_ambient", album_art_background)
    inspector._set_field_visible("background_track_transition", album_art_background)
    inspector._set_field_visible(
        "background_track_transition_seconds",
        album_art_background and source.background_track_transition,
    )
    apply_shared_fields(inspector, source)
    finish(inspector, source)
