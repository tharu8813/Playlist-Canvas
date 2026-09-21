"""Inspector editor for SourceType.AUDIO_VISUALIZER, split out of
SourceInspector._update_legacy_source_specific_fields."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.inspector.editors.base import apply_shared_fields, finish, hide_type_specific_fields
from app.models.source import Source

if TYPE_CHECKING:
    from app.inspector.source_inspector import SourceInspector

_OWN_FIELD_KEYS = (
    "visualizer_style", "visualizer_bars", "visualizer_line_width",
    "visualizer_sensitivity", "visualizer_reactivity", "visualizer_noise_gate",
    "visualizer_min_level", "visualizer_max_level", "visualizer_attack",
    "visualizer_release", "visualizer_smoothing", "visualizer_curve",
)


def edit(inspector: "SourceInspector", source: Source) -> None:
    hide_type_specific_fields(inspector)
    for key in _OWN_FIELD_KEYS:
        inspector._set_field_visible(key, True)
    apply_shared_fields(inspector, source)
    finish(inspector, source)
