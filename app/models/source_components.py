"""Typed component snapshots backed by the legacy Source fields.

Use from_source() to obtain current values and dataclasses.replace() to edit a
snapshot. SourceStore.update_component() validates and publishes the result.
No component state is stored in project JSON or duplicated inside Source.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import Field, dataclass, field, fields
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from app.models.source import Source


def _legacy(name: str) -> Field[Any]:
    """Keep the flat project key explicit when a component uses a shorter name."""
    return field(metadata={"source_field": name})


@dataclass(frozen=True, slots=True)
class SourceComponent:
    @classmethod
    def from_source(cls, source: Source) -> Self:
        return cls(**{
            entry.name: deepcopy(getattr(source, entry.metadata.get("source_field", entry.name)))
            for entry in fields(cls)
        })

    def to_source_changes(self) -> dict[str, object]:
        """Return detached legacy values, ready for validation before applying."""
        return {
            entry.metadata.get("source_field", entry.name): deepcopy(getattr(self, entry.name))
            for entry in fields(self)
        }


@dataclass(frozen=True, slots=True)
class ImageComponent(SourceComponent):
    path: str = _legacy("content_path")
    fit_mode: str = _legacy("image_fit_mode")


@dataclass(frozen=True, slots=True)
class VideoComponent(ImageComponent):
    paths: list[str] = _legacy("video_paths")
    timing_mode: str = _legacy("video_timing_mode")
    repeat_mode: str = _legacy("video_repeat_mode")
    cycle_count: int = _legacy("video_cycle_count")
    cycle_unlimited: bool = _legacy("video_cycle_unlimited")
    speed: float = _legacy("video_speed")
    muted: bool = _legacy("video_muted")
    saturation: float = _legacy("video_saturation")
    grayscale: bool = _legacy("video_grayscale")
    random_seed: int = _legacy("video_random_seed")


@dataclass(frozen=True, slots=True)
class AlbumCoverComponent(ImageComponent):
    frame_style: str = _legacy("album_frame_style")


@dataclass(frozen=True, slots=True)
class BackgroundComponent(ImageComponent):
    mode: str = _legacy("background_mode")
    ambient: bool = _legacy("background_ambient")
    track_transition: bool = _legacy("background_track_transition")
    track_transition_seconds: float = _legacy("background_track_transition_seconds")


@dataclass(frozen=True, slots=True)
class TextComponent(SourceComponent):
    text: str
    font_family: str
    font_path: str
    font_size: float
    font_weight: int
    alignment: str = _legacy("text_alignment")
    overflow: str = _legacy("text_overflow")
    stroke_color: str = _legacy("text_stroke_color")
    stroke_width: float = _legacy("text_stroke_width")


@dataclass(frozen=True, slots=True)
class TimeComponent(TextComponent):
    format: str = _legacy("time_format")


@dataclass(frozen=True, slots=True)
class LyricsComponent(TextComponent):
    fallback: str = _legacy("subtitle_fallback")
    animation: str = _legacy("subtitle_animation")
    animation_duration: float = _legacy("subtitle_animation_duration")
    context_lines: int = _legacy("subtitle_context_lines")
    next_lines: int = _legacy("subtitle_next_lines")
    line_spacing: float = _legacy("subtitle_line_spacing")
    previous_opacity: float = _legacy("subtitle_previous_opacity")
    previous_blur: float = _legacy("subtitle_previous_blur")
    current_line: int = _legacy("subtitle_current_line")
    current_line_count: int = _legacy("subtitle_current_line_count")
    scroll_offset: float = _legacy("subtitle_scroll_offset")
    timing_offset: float = _legacy("subtitle_timing_offset")


@dataclass(frozen=True, slots=True)
class TrackListComponent(TextComponent):
    count: int = _legacy("track_list_count")
    style: str = _legacy("track_list_style")
    window: str = _legacy("track_list_window")
    show_number: bool = _legacy("track_list_show_number")
    show_artist: bool = _legacy("track_list_show_artist")
    show_album: bool = _legacy("track_list_show_album")
    marker: str = _legacy("track_list_marker")
    row_spacing: float = _legacy("track_list_row_spacing")
    item_padding: float = _legacy("track_list_item_padding")
    current_color: str = _legacy("track_list_current_color")
    inactive_color: str = _legacy("track_list_inactive_color")
    current_background: str = _legacy("track_list_current_background")
    inactive_opacity: float = _legacy("track_list_inactive_opacity")
    current_scale: float = _legacy("track_list_current_scale")
    show_dividers: bool = _legacy("track_list_show_dividers")
    current_row: int = _legacy("track_list_current_row")


@dataclass(frozen=True, slots=True)
class NowPlayingComponent(TextComponent):
    style: str = _legacy("now_playing_style")
    duration: float = _legacy("now_playing_duration")
    exit_animation: str = _legacy("now_playing_exit_animation")
    exit_duration: float = _legacy("now_playing_exit_duration")


@dataclass(frozen=True, slots=True)
class ShapeComponent(SourceComponent):
    kind: str = _legacy("shape_kind")


@dataclass(frozen=True, slots=True)
class ProgressComponent(SourceComponent):
    style: str = _legacy("progress_style")
    value: float = _legacy("progress_value")
    track_color: str = _legacy("progress_track_color")
    mode: str = _legacy("progress_mode")


@dataclass(frozen=True, slots=True)
class VisualizerComponent(SourceComponent):
    style: str = _legacy("visualizer_style")
    bars: int = _legacy("visualizer_bars")
    line_width: float = _legacy("visualizer_line_width")
    sensitivity: float = _legacy("visualizer_sensitivity")
    reactivity: float = _legacy("visualizer_reactivity")
    noise_gate: float = _legacy("visualizer_noise_gate")
    min_level: float = _legacy("visualizer_min_level")
    max_level: float = _legacy("visualizer_max_level")
    attack: float = _legacy("visualizer_attack")
    release: float = _legacy("visualizer_release")
    smoothing: float = _legacy("visualizer_smoothing")
    curve: float = _legacy("visualizer_curve")


@dataclass(frozen=True, slots=True)
class WaveformComponent(SourceComponent):
    style: str = _legacy("waveform_style")
    bars: int = _legacy("visualizer_bars")
    line_width: float = _legacy("visualizer_line_width")


@dataclass(frozen=True, slots=True)
class LevelMeterComponent(SourceComponent):
    mode: str = _legacy("level_meter_mode")
    style: str = _legacy("level_meter_style")
    orientation: str = _legacy("level_meter_orientation")
    sensitivity: float = _legacy("level_meter_sensitivity")
    attack: float = _legacy("level_meter_attack")
    release: float = _legacy("level_meter_release")
    min_level: float = _legacy("level_meter_min_level")
    max_level: float = _legacy("level_meter_max_level")
    segments: int = _legacy("level_meter_segments")
    gap: float = _legacy("level_meter_gap")
    show_peak: bool = _legacy("level_meter_show_peak")
    peak_hold: float = _legacy("level_meter_peak_hold")
    peak_decay: float = _legacy("level_meter_peak_decay")
    track_color: str = _legacy("level_meter_track_color")
    low_color: str = _legacy("level_meter_low_color")
    mid_color: str = _legacy("level_meter_mid_color")
    high_color: str = _legacy("level_meter_high_color")


@dataclass(frozen=True, slots=True)
class ParticleComponent(SourceComponent):
    style: str = _legacy("particle_style")
    density: int = _legacy("particle_density")
    speed: float = _legacy("particle_speed")
    min_size: float = _legacy("particle_min_size")
    max_size: float = _legacy("particle_max_size")
    opacity: float = _legacy("particle_opacity")
    direction: float = _legacy("particle_direction")
    drift: float = _legacy("particle_drift")
    twinkle: float = _legacy("particle_twinkle")
    glow: float = _legacy("particle_glow")
    secondary_color: str = _legacy("particle_secondary_color")
    seed: int = _legacy("particle_seed")
