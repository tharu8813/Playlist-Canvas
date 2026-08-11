"""Data models for visual sources on the video canvas."""

from __future__ import annotations

from dataclasses import MISSING, asdict, dataclass, field, fields
from enum import Enum
from math import isfinite
from typing import Any
from uuid import uuid4


class SourceType(str, Enum):
    """Kinds of visual source supported by the editor."""

    IMAGE = "image"
    TEXT = "text"
    SHAPE = "shape"
    PROGRESS_BAR = "progress_bar"
    TIME = "time"
    ALBUM_COVER = "album_cover"
    LOGO = "logo"
    WATERMARK = "watermark"
    BACKGROUND = "background"
    AUDIO_VISUALIZER = "audio_visualizer"
    LYRICS = "lyrics"
    TRACK_LIST = "track_list"
    NOW_PLAYING = "now_playing"
    AUDIO_WAVEFORM = "audio_waveform"
    AUDIO_LEVEL_METER = "audio_level_meter"
    PARTICLE_OVERLAY = "particle_overlay"


@dataclass(slots=True)
class Shadow:
    """Drop-shadow appearance settings."""

    enabled: bool = False
    color: str = "#000000"
    blur_radius: float = 12.0
    offset_x: float = 3.0
    offset_y: float = 3.0
    opacity: float = 0.35


@dataclass(slots=True)
class Gradient:
    """Simple two-stop linear gradient definition."""

    enabled: bool = False
    start_color: str = "#7C3AED"
    end_color: str = "#06B6D4"
    angle: float = 0.0


@dataclass(slots=True)
class Source:
    """Serializable source state shared by the canvas and inspector."""

    source_type: SourceType
    name: str
    x: float = 0.0
    y: float = 0.0
    width: float = 240.0
    height: float = 100.0
    rotation: float = 0.0
    scale: float = 1.0
    opacity: float = 1.0
    border_radius: float = 0.0
    shadow: Shadow = field(default_factory=Shadow)
    blur: float = 0.0
    outline_color: str = "#FFFFFF"
    outline_width: float = 0.0
    gradient: Gradient = field(default_factory=Gradient)
    z_index: int = 0
    visible: bool = True
    locked: bool = False
    fill_color: str = "#7C3AED"
    text: str = "Text"
    shape_kind: str = "rectangle"
    progress_style: str = "rounded"
    time_format: str = "HH:mm"
    font_family: str = "Segoe UI"
    font_path: str = ""
    font_size: float = 24.0
    font_weight: int = 600
    text_alignment: str = "center"
    text_overflow: str = "wrap"
    content_path: str = ""
    image_fit_mode: str = "cover"
    background_mode: str = "color"
    background_ambient: bool = False
    brightness: float = 0.0
    contrast: float = 0.0
    progress_value: float = 0.62
    progress_track_color: str = "#303842"
    progress_mode: str = "track"
    group_id: str | None = None
    visualizer_style: str = "bars"
    visualizer_bars: int = 32
    visualizer_line_width: float = 3.0
    visualizer_sensitivity: float = 1.2
    visualizer_reactivity: float = 0.22
    visualizer_noise_gate: float = 0.003
    visualizer_min_level: float = 0.0
    visualizer_max_level: float = 0.96
    visualizer_attack: float = 0.55
    visualizer_release: float = 0.16
    visualizer_smoothing: float = 0.18
    visualizer_curve: float = 0.9
    subtitle_fallback: str = "Lyrics are not available for this track."
    subtitle_style: str = "karaoke"
    subtitle_animation: str = "apple_music"
    subtitle_animation_duration: float = 0.36
    subtitle_context_lines: int = 1
    subtitle_next_lines: int = 1
    subtitle_line_spacing: float = 14.0
    subtitle_previous_opacity: float = 0.34
    subtitle_previous_blur: float = 1.5
    subtitle_current_line: int = -1
    subtitle_scroll_offset: float = 0.0
    subtitle_timing_offset: float = 0.0
    track_list_count: int = 5
    track_list_style: str = "compact"
    track_list_window: str = "centered"
    track_list_show_number: bool = True
    track_list_show_artist: bool = True
    track_list_show_album: bool = False
    track_list_marker: str = "play"
    track_list_row_spacing: float = 6.0
    track_list_item_padding: float = 10.0
    track_list_current_color: str = "#FFFFFF"
    track_list_inactive_color: str = "#94A3B8"
    track_list_current_background: str = "#1685D1"
    track_list_inactive_opacity: float = 0.62
    track_list_current_scale: float = 1.05
    track_list_show_dividers: bool = False
    track_list_current_row: int = -1
    now_playing_style: str = "card"
    now_playing_duration: float = 3.0
    now_playing_exit_animation: str = "fade"
    now_playing_exit_duration: float = 0.35
    album_frame_style: str = "rounded"
    waveform_style: str = "line"
    level_meter_mode: str = "stereo"
    level_meter_style: str = "gradient"
    level_meter_orientation: str = "vertical"
    level_meter_sensitivity: float = 1.2
    level_meter_attack: float = 0.65
    level_meter_release: float = 0.18
    level_meter_min_level: float = 0.0
    level_meter_max_level: float = 1.0
    level_meter_segments: int = 16
    level_meter_gap: float = 4.0
    level_meter_show_peak: bool = True
    level_meter_peak_hold: float = 0.35
    level_meter_peak_decay: float = 0.7
    level_meter_track_color: str = "#263244"
    level_meter_low_color: str = "#22C55E"
    level_meter_mid_color: str = "#FACC15"
    level_meter_high_color: str = "#EF4444"
    particle_style: str = "dust"
    particle_density: int = 42
    particle_speed: float = 1.0
    particle_min_size: float = 1.0
    particle_max_size: float = 4.0
    particle_opacity: float = 0.62
    particle_direction: float = -90.0
    particle_drift: float = 0.25
    particle_twinkle: float = 0.25
    particle_glow: float = 0.2
    particle_secondary_color: str = "#7DD3FC"
    particle_seed: int = 17
    animation_in: str = "none"
    animation_out: str = "none"
    animation_duration: float = 0.45
    timeline_start: float = 0.0
    timeline_duration: float = 0.0
    id: str = field(default_factory=lambda: str(uuid4()))

    def to_dict(self) -> dict[str, Any]:
        """Convert the model to JSON-compatible data."""
        result = asdict(self)
        result["source_type"] = self.source_type.value
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Source":
        """Restore a source from JSON-compatible data."""
        if not isinstance(data, dict):
            raise ValueError("Project sources must be objects.")
        source_data = data.copy()
        source_data["source_type"] = SourceType(source_data["source_type"])
        source_data["shadow"] = Shadow(**source_data.get("shadow", {}))
        source_data["gradient"] = Gradient(**source_data.get("gradient", {}))
        source = cls(**source_data)
        if not isinstance(source.id, str) or not source.id.strip():
            raise ValueError("Every project source must have a non-empty string ID.")
        if not isinstance(source.name, str) or not source.name.strip():
            raise ValueError("Every project source must have a non-empty name.")
        for model_field in fields(cls):
            default = model_field.default
            if default is MISSING or isinstance(default, bool):
                continue
            value = getattr(source, model_field.name)
            if isinstance(default, (int, float)):
                if (not isinstance(value, (int, float)) or isinstance(value, bool)
                        or not isfinite(float(value))):
                    raise ValueError(
                        f"Source '{source.name}' has an invalid numeric value for "
                        f"'{model_field.name}'."
                    )
        if not 0.0 <= source.opacity <= 1.0:
            raise ValueError(f"Source '{source.name}' opacity must be between 0 and 1.")
        if source.width <= 0 or source.height <= 0 or source.scale <= 0:
            raise ValueError(f"Source '{source.name}' dimensions and scale must be positive.")
        if source.width > 100_000 or source.height > 100_000 or source.scale > 100:
            raise ValueError(f"Source '{source.name}' dimensions are unexpectedly large.")
        if source.timeline_start < 0 or source.timeline_duration < 0:
            raise ValueError(f"Source '{source.name}' timeline values cannot be negative.")
        bounded_integers = {
            "visualizer_bars": (4, 96),
            "subtitle_context_lines": (0, 6),
            "subtitle_next_lines": (0, 6),
            "track_list_count": (1, 15),
            "level_meter_segments": (3, 64),
            "particle_density": (4, 500),
            "particle_seed": (0, 999_999),
        }
        for name, (minimum, maximum) in bounded_integers.items():
            value = getattr(source, name)
            if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
                raise ValueError(
                    f"Source '{source.name}' value for '{name}' must be between "
                    f"{minimum} and {maximum}."
                )
        if source.group_id is not None and not isinstance(source.group_id, str):
            raise ValueError(f"Source '{source.name}' has an invalid group ID.")
        return source
