"""Shared field-visibility helpers for per-type Inspector editors.

SourceInspector._update_legacy_source_specific_fields sets one field's
visibility per line in a single flat pass gated by source_type -- unlike
the Canvas paint dispatch, it has no per-type branch to lift out directly.
A per-type editor here instead: hides every type-conditional field first
(hide_type_specific_fields), shows only the fields its own type owns,
applies the handful of fields every type applies the same way
(apply_shared_fields), then runs the same closing steps the legacy
function did (finish).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.models.source import Source

if TYPE_CHECKING:
    from app.inspector.source_inspector import SourceInspector

# Every field key _update_legacy_source_specific_fields toggles purely by
# source_type. Excludes shadow_* (every source shows it, see
# apply_shared_fields) and the toggle-dependent rows
# _hide_inactive_dependent_fields already owns (gradient_start/end,
# outline_color, animation_in/out_duration) -- those are unaffected by
# which type is selected.
TYPE_SPECIFIC_FIELD_KEYS: tuple[str, ...] = (
    "text", "font_size", "font_weight", "font_family",
    "text_stroke_color", "text_stroke_width", "text_alignment", "text_overflow",
    "file", "image_fit", "blur", "brightness", "contrast",
    "shape", "video_settings",
    "progress_style", "progress_value", "progress_track_color", "progress_mode",
    "visualizer_style", "visualizer_bars", "visualizer_line_width",
    "visualizer_sensitivity", "visualizer_reactivity", "visualizer_noise_gate",
    "visualizer_min_level", "visualizer_max_level", "visualizer_attack",
    "visualizer_release", "visualizer_smoothing", "visualizer_curve",
    "background_mode", "background_ambient", "background_track_transition",
    "background_track_transition_seconds",
    "album_frame",
    "track_list_count", "track_list_style", "track_list_window",
    "track_list_show_number", "track_list_show_artist", "track_list_show_album",
    "track_list_marker", "track_list_row_spacing", "track_list_item_padding",
    "track_list_current_color", "track_list_inactive_color",
    "track_list_current_background", "track_list_inactive_opacity",
    "track_list_current_scale", "track_list_show_dividers",
    "now_playing_style", "now_playing_duration", "now_playing_exit",
    "now_playing_exit_duration",
    "subtitle_animation", "subtitle_animation_duration", "subtitle_context_lines",
    "subtitle_next_lines", "subtitle_line_spacing", "subtitle_previous_opacity",
    "subtitle_previous_blur", "subtitle_timing_offset",
    "waveform_style",
    "level_meter_mode", "level_meter_style", "level_meter_orientation",
    "level_meter_sensitivity", "level_meter_attack", "level_meter_release",
    "level_meter_min_level", "level_meter_max_level", "level_meter_segments",
    "level_meter_gap", "level_meter_show_peak", "level_meter_peak_hold",
    "level_meter_peak_decay", "level_meter_track_color", "level_meter_low_color",
    "level_meter_mid_color", "level_meter_high_color",
    "particle_style", "particle_density", "particle_speed", "particle_min_size",
    "particle_max_size", "particle_opacity", "particle_direction",
    "particle_drift", "particle_twinkle", "particle_glow",
    "particle_secondary_color", "particle_seed",
)

# Fields visible whenever a source is selected at all, regardless of type.
_ALWAYS_VISIBLE_FIELD_KEYS: tuple[str, ...] = (
    "shadow", "shadow_color", "shadow_opacity", "shadow_blur", "shadow_x", "shadow_y",
)


def hide_type_specific_fields(inspector: "SourceInspector") -> None:
    """Hide every field a per-type editor might show, before it shows its own."""
    for key in TYPE_SPECIFIC_FIELD_KEYS:
        inspector._set_field_visible(key, False)


def apply_shared_fields(inspector: "SourceInspector", source: Source) -> None:
    """Apply the fields every type applies the same way.

    text_color depends on a cross-type helper rather than one type owning
    it, and the shadow group is visible for any selected source.
    """
    inspector._set_field_visible("text_color", inspector._uses_primary_text_color(source))
    for key in _ALWAYS_VISIBLE_FIELD_KEYS:
        inspector._set_field_visible(key, True)


def finish(inspector: "SourceInspector", source: Source) -> None:
    """Run the same closing steps _update_legacy_source_specific_fields did."""
    inspector._hide_inactive_dependent_fields(source)
    inspector._refresh_property_tabs([source])
