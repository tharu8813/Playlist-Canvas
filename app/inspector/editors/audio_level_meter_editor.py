"""Inspector editor for SourceType.AUDIO_LEVEL_METER, split out of
SourceInspector._update_legacy_source_specific_fields."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.inspector.editors.base import apply_shared_fields, finish, hide_type_specific_fields
from app.models.source import Source

if TYPE_CHECKING:
    from app.inspector.source_inspector import SourceInspector

_OWN_FIELD_KEYS = (
    "level_meter_mode", "level_meter_style", "level_meter_orientation",
    "level_meter_sensitivity", "level_meter_attack", "level_meter_release",
    "level_meter_min_level", "level_meter_max_level", "level_meter_segments",
    "level_meter_gap", "level_meter_show_peak", "level_meter_peak_hold",
    "level_meter_peak_decay", "level_meter_track_color", "level_meter_low_color",
    "level_meter_mid_color", "level_meter_high_color",
)


def edit(inspector: "SourceInspector", source: Source) -> None:
    hide_type_specific_fields(inspector)
    for key in _OWN_FIELD_KEYS:
        inspector._set_field_visible(key, True)
    apply_shared_fields(inspector, source)
    finish(inspector, source)
