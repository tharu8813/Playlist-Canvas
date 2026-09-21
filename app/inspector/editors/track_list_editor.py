"""Inspector editor for SourceType.TRACK_LIST, split out of
SourceInspector._update_legacy_source_specific_fields."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.inspector.editors.base import apply_shared_fields, finish, hide_type_specific_fields
from app.models.source import Source

if TYPE_CHECKING:
    from app.inspector.source_inspector import SourceInspector

_OWN_FIELD_KEYS = (
    "text", "font_size", "font_weight", "font_family",
    "text_stroke_color", "text_stroke_width", "text_alignment", "text_overflow",
    "track_list_count", "track_list_style", "track_list_window",
    "track_list_show_number", "track_list_show_artist", "track_list_show_album",
    "track_list_marker", "track_list_row_spacing", "track_list_item_padding",
    "track_list_current_color", "track_list_inactive_color",
    "track_list_current_background", "track_list_inactive_opacity",
    "track_list_current_scale", "track_list_show_dividers",
)


def edit(inspector: "SourceInspector", source: Source) -> None:
    hide_type_specific_fields(inspector)
    for key in _OWN_FIELD_KEYS:
        inspector._set_field_visible(key, True)
    apply_shared_fields(inspector, source)
    finish(inspector, source)
