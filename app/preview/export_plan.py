"""Decide how a playlist export is split into Canvas capture streams."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from app.models.playlist import PlaylistTrack
from app.models.source import Source, SourceType
from app.preview.canvas_snapshot import CanvasSnapshot
from app.renderer.export_timeline import ExportFrameSample, ExportTimelinePlanner
from app.renderer.ffmpeg_renderer import RenderError, RenderSettings

LOGGER = logging.getLogger(__name__)

_DYNAMIC_SOURCE_TYPES = {
    SourceType.AUDIO_VISUALIZER,
    SourceType.AUDIO_WAVEFORM,
    SourceType.AUDIO_LEVEL_METER,
    SourceType.PARTICLE_OVERLAY,
    SourceType.VIDEO,
}


@dataclass(frozen=True, slots=True)
class ExportPlan:
    """The Canvas capture schedule for one export, before any pixels are drawn."""

    z_bands: list[tuple[float | None, float | None]]
    dynamic_visualizer_ids: set[str]
    direct_final_stream: bool
    use_streamed_visuals: bool
    stream_timeline_samples: dict[str, list[ExportFrameSample]]
    animation_fps: int
    playlist_duration: float
    canvas_render_scale: float = 1.0


def canvas_render_scale(scene: object, render_settings: RenderSettings) -> float:
    """Return how much larger than the artboard the Canvas must be rasterised.

    Export resolutions keep the project's aspect ratio, so a single factor
    (>= 1.0, we never downscale the authored canvas) makes text and shapes land
    at the final pixel grid instead of being upscaled by FFmpeg afterwards.
    """
    artboard_width = float(scene.artboard_rect.width())
    if artboard_width <= 0.0:
        return 1.0
    return max(1.0, round(render_settings.output_width) / artboard_width)


def build_export_plan(
    scene: object,
    active_tracks: Sequence[PlaylistTrack],
    sources: Sequence[Source],
    render_settings: RenderSettings,
    playlist_duration: float,
    visualizers: Sequence[object],
    video_clips: Sequence[object],
    renderer: object,
    animation_fps: int,
) -> ExportPlan:
    """Resolve Z bands, the lossless-streaming decision, and the sample schedule.

    ``renderer`` only needs ``ensure_encoder_available``; it is passed in so a
    mocked renderer in tests keeps controlling encoder availability. Disk-space
    checks and stream-file paths stay with the caller because they touch the UI
    and temporary directory.
    """
    dynamic_visualizer_ids = {
        source.id for source in sources
        if source.source_type in _DYNAMIC_SOURCE_TYPES and source.visible
    }
    broad_z_bands = CanvasSnapshot.z_bands(scene, dynamic_visualizer_ids)
    preserve_direct_stream = (
        len(broad_z_bands) == 1 and not visualizers and not video_clips
    )
    z_bands = (
        broad_z_bands
        if preserve_direct_stream else
        CanvasSnapshot.split_mixed_capture_bands(
            scene, dynamic_visualizer_ids, broad_z_bands, playlist_duration,
        )
    )
    LOGGER.info(
        "Canvas capture bands: broad=%d split=%d direct_preserved=%s",
        len(broad_z_bands), len(z_bands), preserve_direct_stream,
    )
    direct_final_stream = (
        len(z_bands) == 1 and not visualizers and not video_clips
    )
    use_streamed_visuals = True
    required_stream_encoders = [] if direct_final_stream else ["libx264rgb"]
    if len(z_bands) > 1:
        required_stream_encoders.append("ffv1")
    for stream_encoder_name in required_stream_encoders:
        try:
            renderer.ensure_encoder_available(stream_encoder_name)
        except RenderError as error:
            # Custom FFmpeg builds may omit the lossless RGB encoder. Preserve
            # the known-good PNG path instead of making those installations
            # unable to export otherwise supported videos.
            LOGGER.warning(
                "Lossless Canvas streaming unavailable; using PNG staging: %s",
                error,
            )
            use_streamed_visuals = False
            break

    stream_timeline_samples = ExportTimelinePlanner.build_by_z_band(
        active_tracks, sources, dynamic_visualizer_ids, z_bands, animation_fps,
    )
    if (
        not stream_timeline_samples
        or any(not samples for samples in stream_timeline_samples.values())
    ):
        raise RenderError(
            "The export timeline does not contain any renderable duration."
        )

    return ExportPlan(
        z_bands=z_bands,
        dynamic_visualizer_ids=dynamic_visualizer_ids,
        direct_final_stream=direct_final_stream,
        use_streamed_visuals=use_streamed_visuals,
        stream_timeline_samples=stream_timeline_samples,
        animation_fps=animation_fps,
        playlist_duration=playlist_duration,
        canvas_render_scale=canvas_render_scale(scene, render_settings),
    )
