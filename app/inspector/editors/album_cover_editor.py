"""Inspector editor for SourceType.ALBUM_COVER, split out of
SourceInspector._update_legacy_source_specific_fields."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.inspector.editors.base import apply_image_backed_fields, editing
from app.models.source import Source

if TYPE_CHECKING:
    from app.inspector.source_inspector import SourceInspector


def edit(inspector: "SourceInspector", source: Source) -> None:
    with editing(inspector, source):
        apply_image_backed_fields(inspector, source, show_file=True)
        inspector._set_field_visible("album_frame", True)
