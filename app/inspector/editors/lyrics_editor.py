"""Inspector editor for SourceType.LYRICS, split out of
SourceInspector._update_legacy_source_specific_fields."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.inspector.editors.base import apply_shared_fields, finish, hide_type_specific_fields
from app.models.source import Source

if TYPE_CHECKING:
    from app.inspector.source_inspector import SourceInspector

_OWN_FIELD_KEYS = (
    "text", "font_size", "font_weight", "font_family",
    "text_stroke_color", "text_stroke_width", "text_alignment",
    "subtitle_animation", "subtitle_animation_duration", "subtitle_context_lines",
    "subtitle_next_lines", "subtitle_line_spacing", "subtitle_previous_opacity",
    "subtitle_previous_blur", "subtitle_timing_offset",
)


def edit(inspector: "SourceInspector", source: Source) -> None:
    hide_type_specific_fields(inspector)
    for key in _OWN_FIELD_KEYS:
        inspector._set_field_visible(key, True)
    apply_shared_fields(inspector, source)
    finish(inspector, source)
