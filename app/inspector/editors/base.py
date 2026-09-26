"""Shared field-visibility helpers for per-type Inspector editors.

SourceInspector._update_legacy_source_specific_fields sets one field's
visibility per line in a single flat pass gated by source_type -- unlike
the Canvas paint dispatch, it has no per-type branch to lift out directly.
A per-type editor here instead: hides every type-conditional field first
(hide_type_specific_fields), shows only the fields its own type owns,
applies the handful of fields every type applies the same way
(apply_shared_fields), then runs the same closing steps the legacy
function did (finish).

Every editor follows that same frame, so it is written once as the
``editing`` context manager; an editor only fills in the middle step::

    def edit(inspector, source):
        with editing(inspector, source):
            show_fields(inspector, ("shape",))
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, ClassVar

from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QPushButton, QWidget

from app.models.source import Source

if TYPE_CHECKING:
    from app.inspector.source_inspector import SourceInspector


class FieldSection:
    """One source type's own Inspector fields, keyed by the Source attribute each edits.

    Because a field key is also its model attribute, rows, signal wiring,
    multi-selection bindings and filling all follow from ``widgets`` and the
    widget kind: QComboBox (item data), QCheckBox, QPushButton (colour picker)
    or a spin box. SourceInspector calls each hook exactly where its phase used
    to handle these fields inline, so form order, tab order and tooltip
    precedence are unchanged. Subclasses build ``widgets`` and fill the tables.
    """

    FAMILY: ClassVar[tuple[str, str]]
    """(Korean, English) help-text family name for the section's key prefix."""
    ROWS: ClassVar[tuple[tuple[str, str | None], ...]]
    """(field key, collapsible sub-section or None), in form order."""
    LABELS: ClassVar[dict[str, tuple[str, str]]]
    SECTION_TITLES: ClassVar[dict[str, tuple[str, str]]]
    HELP: ClassVar[dict[str, tuple[str, str]]]
    """Help details keyed by the field key without the family prefix."""
    ATTRIBUTES: ClassVar[dict[str, str]] = {}
    """Model attribute for a field key that is not itself the attribute name."""

    widgets: dict[str, QWidget]

    def attribute(self, key: str) -> str:
        return self.ATTRIBUTES.get(key, key)

    @staticmethod
    def kind(widget: QWidget) -> str:
        if isinstance(widget, QComboBox):
            return "combo"
        if isinstance(widget, QCheckBox):
            return "check"
        if isinstance(widget, QPushButton):
            return "color"
        return "spin"

    def add_rows(
        self, add_row: Callable[..., None], form: QFormLayout, keys: Collection[str] | None = None,
    ) -> None:
        """Add the rows (in ROWS order); ``keys`` adds just those, for sections
        whose rows the form places in more than one spot."""
        for key, section in self.ROWS:
            if keys is None or key in keys:
                add_row(form, key, self.widgets[key], section=section)

    def connect(
        self, update: Callable[[str, object], None], *,
        choose_color: Callable[[str, QPushButton], None] | None = None,
        apply_mixed_checkbox: Callable[[str, bool], None] | None = None,
    ) -> None:
        for field, widget in self.widgets.items():
            key = self.attribute(field)
            kind = self.kind(widget)
            if kind == "combo":
                widget.currentIndexChanged.connect(
                    lambda _index, key=key, combo=widget: update(key, combo.currentData())
                )
            elif kind == "check":
                widget.toggled.connect(lambda value, key=key: update(key, value))
                # A click on a mixed multi-selection value must still apply it.
                widget.clicked.connect(
                    lambda checked=False, key=key: apply_mixed_checkbox(key, checked)
                )
            elif kind == "color":
                widget.clicked.connect(
                    lambda _checked=False, key=key, button=widget: choose_color(key, button)
                )
            else:
                widget.valueChanged.connect(lambda value, key=key: update(key, value))

    def bindings(self) -> dict[str, tuple[str, QWidget, str]]:
        """Multi-selection bindings: model path -> (path, control, kind)."""
        return {
            self.attribute(key): (self.attribute(key), widget, self.kind(widget))
            for key, widget in self.widgets.items()
        }

    def displayed_value(self, source: Source, key: str) -> object:
        """The value a field shows for ``source``; override to present legacy data."""
        return getattr(source, self.attribute(key))

    def fill(
        self, source: Source, *,
        set_color: Callable[[QPushButton, str], None] | None = None,
    ) -> None:
        for key, widget in self.widgets.items():
            value = self.displayed_value(source, key)
            kind = self.kind(widget)
            if kind == "combo":
                widget.setCurrentIndex(max(0, widget.findData(value)))
            elif kind == "check":
                widget.setChecked(value)
            elif kind == "color":
                set_color(widget, value)
            else:
                widget.setValue(value)

    def hidden_when_off(self, source: Source) -> dict[str, bool]:
        """Fields to hide while the toggle/value they depend on is off."""
        return {}

    def retranslate(self, korean: bool) -> None:
        """Localize texts inside the widgets (item texts, special values)."""

    def notes(self, korean: bool) -> dict[str, str]:
        """Field-specific guidance merged into each field's hover help."""
        return {}

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


def show_fields(inspector: "SourceInspector", keys: Iterable[str], visible: bool = True) -> None:
    """Set the same visibility on every field in ``keys``."""
    for key in keys:
        inspector._set_field_visible(key, visible)


def hide_type_specific_fields(inspector: "SourceInspector") -> None:
    """Hide every field a per-type editor might show, before it shows its own."""
    show_fields(inspector, TYPE_SPECIFIC_FIELD_KEYS, False)


def apply_shared_fields(inspector: "SourceInspector", source: Source) -> None:
    """Apply the fields every type applies the same way.

    text_color depends on a cross-type helper rather than one type owning
    it, and the shadow group is visible for any selected source.
    """
    inspector._set_field_visible("text_color", inspector._uses_primary_text_color(source))
    show_fields(inspector, _ALWAYS_VISIBLE_FIELD_KEYS)


def apply_image_backed_fields(inspector: "SourceInspector", source: Source, *, show_file: bool) -> None:
    """Apply the fields every image-backed type applies the same way
    (image_fit/blur/brightness/contrast), plus file (whose own visibility
    condition differs per type, e.g. BACKGROUND gates it on background_mode)."""
    inspector._set_field_visible("file", show_file)
    show_fields(inspector, ("image_fit", "blur", "brightness", "contrast"))


def finish(inspector: "SourceInspector", source: Source) -> None:
    """Run the same closing steps _update_legacy_source_specific_fields did."""
    inspector._hide_inactive_dependent_fields(source)
    inspector._refresh_property_tabs([source])


@contextmanager
def editing(inspector: "SourceInspector", source: Source) -> Iterator[None]:
    """Frame one per-type edit: hide every type-specific field on entry, then
    apply the shared fields and run ``finish`` on exit.

    The body only shows the fields its own type owns. Like the flat legacy
    pass it replaces, an exception in the body skips the closing steps.
    """
    hide_type_specific_fields(inspector)
    yield
    apply_shared_fields(inspector, source)
    finish(inspector, source)
