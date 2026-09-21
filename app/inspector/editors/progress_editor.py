"""Inspector editor for SourceType.PROGRESS_BAR, split out of
SourceInspector._update_legacy_source_specific_fields."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.inspector.editors.base import apply_shared_fields, finish, hide_type_specific_fields
from app.models.source import Source

if TYPE_CHECKING:
    from app.inspector.source_inspector import SourceInspector

_OWN_FIELD_KEYS = (
    "progress_style", "progress_value", "progress_track_color", "progress_mode",
)


def edit(inspector: "SourceInspector", source: Source) -> None:
    hide_type_specific_fields(inspector)
    for key in _OWN_FIELD_KEYS:
        inspector._set_field_visible(key, True)
    apply_shared_fields(inspector, source)
    finish(inspector, source)
