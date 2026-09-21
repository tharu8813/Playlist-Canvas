"""Inspector editor for SourceType.PARTICLE_OVERLAY, split out of
SourceInspector._update_legacy_source_specific_fields."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.inspector.editors.base import apply_shared_fields, finish, hide_type_specific_fields
from app.models.source import Source

if TYPE_CHECKING:
    from app.inspector.source_inspector import SourceInspector

_OWN_FIELD_KEYS = (
    "particle_style", "particle_density", "particle_speed", "particle_min_size",
    "particle_max_size", "particle_opacity", "particle_direction",
    "particle_drift", "particle_twinkle", "particle_glow",
    "particle_secondary_color", "particle_seed",
)


def edit(inspector: "SourceInspector", source: Source) -> None:
    hide_type_specific_fields(inspector)
    for key in _OWN_FIELD_KEYS:
        inspector._set_field_visible(key, True)
    apply_shared_fields(inspector, source)
    finish(inspector, source)
