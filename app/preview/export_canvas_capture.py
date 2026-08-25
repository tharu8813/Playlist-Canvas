"""Capture a prepared export timeline through the existing Qt Canvas renderer."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtGui import QImage

from app.canvas.live_canvas import CanvasScene
from app.models.playlist import PlaylistTrack
from app.preview.canvas_snapshot import CanvasSnapshot
from app.renderer.export_timeline import ExportFrameSample
from app.renderer.ffmpeg_renderer import RenderFrame, StaticOverlayLayer


class ExportCanvasCapturer:
    """Turn planned Canvas states into the existing disk-backed frame streams."""

    def __init__(
        self,
        scene: CanvasScene,
        tracks: Sequence[PlaylistTrack],
        playlist_duration: float,
        dynamic_source_ids: set[str],
        z_bands: Sequence[tuple[float | None, float | None]],
        stage_frame: Callable[[QImage, float, str], RenderFrame],
        ensure_not_cancelled: Callable[[], None],
        after_capture: Callable[[int, str], None],
        *,
        retain_static_frames: bool = True,
    ) -> None:
        self.scene = scene
        self.tracks = list(tracks)
        self.playlist_duration = playlist_duration
        self.dynamic_source_ids = set(dynamic_source_ids)
        self.z_bands = list(z_bands)
        self.stage_frame = stage_frame
        self.ensure_not_cancelled = ensure_not_cancelled
        self.after_capture = after_capture
        self.retain_static_frames = retain_static_frames
        self._static_band_frames: list[list[RenderFrame]] = [
            [] for _band in self.z_bands[1:]
        ]

    def capture(self, sample: ExportFrameSample) -> RenderFrame:
        """Stage one opaque base frame and its transparent foreground bands."""
        base = self.capture_stream(sample, "base")
        for index, _band in enumerate(self.z_bands[1:]):
            self.capture_stream(sample, f"layer:{index}")
        return base

    def capture_stream(
        self, sample: ExportFrameSample, stream_key: str,
    ) -> RenderFrame:
        """Capture one band so callers can encode streams sequentially."""
        self.ensure_not_cancelled()
        common: dict[str, object] = {
            "elapsed_seconds": sample.elapsed_seconds,
            "hide_visualizers": self.dynamic_source_ids,
            "playlist_duration_seconds": self.playlist_duration,
            "playlist_tracks": self.tracks,
            "timeline_seconds": sample.timeline_seconds,
        }
        if sample.animation_phase is not None:
            common.update({
                "animation_phase": sample.animation_phase,
                "animation_progress": sample.animation_progress,
                "animation_phase_duration": sample.animation_phase_duration,
            })
        if stream_key == "base":
            z_min = None
            z_max = self.z_bands[0][1]
            transparent = False
            retained_layer_index = None
        elif stream_key.startswith("layer:"):
            try:
                retained_layer_index = int(stream_key.removeprefix("layer:"))
                if retained_layer_index < 0:
                    raise IndexError
                z_min, z_max = self.z_bands[retained_layer_index + 1]
            except (ValueError, IndexError) as error:
                raise ValueError(f"Unknown Canvas stream: {stream_key}") from error
            transparent = True
        else:
            raise ValueError(f"Unknown Canvas stream: {stream_key}")

        rendered = self.stage_frame(
            CanvasSnapshot.capture_track(
                self.scene,
                sample.track,
                sample.track_number,
                len(self.tracks),
                sample.track_start_seconds,
                z_min=z_min,
                z_max=z_max,
                transparent=transparent,
                **common,
            ),
            sample.duration_seconds,
            stream_key,
        )
        if retained_layer_index is not None and self.retain_static_frames:
            self._static_band_frames[retained_layer_index].append(rendered)
        self.after_capture(sample.track_number, stream_key)
        return rendered

    def static_layers(self) -> list[StaticOverlayLayer]:
        """Return transparent bands in the same Z and timeline order as capture."""
        return [
            StaticOverlayLayer(
                z_min if z_min is not None else -10_000.0,
                frames,
            )
            for (z_min, _z_max), frames in zip(
                self.z_bands[1:], self._static_band_frames, strict=True,
            )
            if frames
        ]
