"""Capture a prepared export timeline through the existing Qt Canvas renderer."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace

from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage

from app.canvas.live_canvas import CanvasScene
from app.canvas.source_item import SourceItem
from app.models.playlist import PlaylistTrack
from app.models.source import Source, SourceType
from app.preview.canvas_snapshot import CanvasSnapshot
from app.preview.text_template import expand_track_template
from app.renderer.export_timeline import ExportFrameSample
from app.renderer.ffmpeg_renderer import RenderFrame, StaticOverlayLayer
from app.services.lyrics_service import LyricsService


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
        self.invariant_stream_keys = CanvasSnapshot.invariant_stream_keys(
            self.scene,
            self.dynamic_source_ids,
            self.z_bands,
            self.playlist_duration,
        )
        self._invariant_images: dict[str, QImage] = {}
        visible_items = [
            item for item in self.scene.items()
            if isinstance(item, SourceItem)
            and item.isVisible()
            and item.source.visible
        ]
        self._stream_source_items: dict[str, list[SourceItem]] = {}
        for index, (z_min, z_max) in enumerate(self.z_bands):
            stream_key = "base" if index == 0 else f"layer:{index - 1}"
            self._stream_source_items[stream_key] = [
                item for item in visible_items
                if (z_min is None or item.source.z_index >= z_min)
                and (z_max is None or item.source.z_index <= z_max)
            ]
        self._stream_capture_rects: dict[str, QRectF] = {}
        for index, (z_min, z_max) in enumerate(self.z_bands[1:]):
            capture_rect = CanvasSnapshot.band_capture_envelope(
                self.scene, z_min, z_max,
            )
            if capture_rect is not None:
                self._stream_capture_rects[f"layer:{index}"] = capture_rect
        self.partial_render_capture_count = 0
        self.scene_render_source_pixels = 0
        self.full_frame_source_pixels = 0

    def coalesce_samples(
        self,
        samples: Sequence[ExportFrameSample],
        stream_key: str,
    ) -> list[ExportFrameSample]:
        """Merge adjacent samples whose complete Canvas state is identical.

        Only consecutive samples are combined.  A ``None`` state key is a
        conservative opt-out: an element whose state cannot be proven equal
        continues through the established one-capture-per-sample path.
        """
        result: list[ExportFrameSample] = []
        previous_key: tuple[object, ...] | None = None
        for sample in samples:
            state_key = self._stream_state_key(sample, stream_key)
            if (
                result
                and state_key is not None
                and previous_key is not None
                and state_key == previous_key
            ):
                previous = result[-1]
                result[-1] = replace(
                    previous,
                    duration_seconds=(
                        previous.duration_seconds + sample.duration_seconds
                    ),
                )
            else:
                result.append(sample)
            previous_key = state_key
        return result

    def _stream_state_key(
        self, sample: ExportFrameSample, stream_key: str,
    ) -> tuple[object, ...] | None:
        """Return the resolved visual state of every element in one Z stream."""
        _z_min, _z_max, _transparent, _retained = self._stream_parameters(stream_key)
        source_keys: list[tuple[object, ...]] = []
        for item in self._stream_source_items.get(stream_key, []):
            source = item.source
            if (
                not item.isVisible()
                or not source.visible
                or source.id in self.dynamic_source_ids
            ):
                continue
            source_key = self._source_state_key(source, sample)
            if source_key is None:
                return None
            source_keys.append(source_key)
        return tuple(source_keys)

    def _source_state_key(
        self, source: Source, sample: ExportFrameSample,
    ) -> tuple[object, ...] | None:
        """Resolve the sample-dependent pixels of one Canvas source."""
        global_seconds = max(0.0, sample.timeline_seconds)
        timing_end = source.timeline_start + source.timeline_duration
        visible = not (
            global_seconds < source.timeline_start
            or (
                source.timeline_duration > 0.0
                and global_seconds >= timing_end
            )
        )
        if not visible:
            return (source.id, "hidden")

        content_state: tuple[object, ...]
        if source.source_type in {SourceType.TEXT, SourceType.TIME}:
            template = (
                source.text
                if "%" in source.text
                else "%current_time%"
                if source.source_type is SourceType.TIME
                else source.text
            )
            content_state = (
                "text",
                expand_track_template(
                    template,
                    sample.track,
                    sample.track_number,
                    len(self.tracks),
                    global_seconds - sample.elapsed_seconds,
                    sample.elapsed_seconds,
                    self.playlist_duration,
                ),
            )
        elif source.source_type is SourceType.LYRICS:
            effective_offset = (
                sample.track.lyrics_timing_offset_seconds
                + source.subtitle_timing_offset
            )
            lyric_elapsed = max(0.0, sample.elapsed_seconds + effective_offset)
            active_index = LyricsService.current_cue_index(
                sample.track.lyrics, lyric_elapsed,
            )
            cue_index = LyricsService.display_cue_index(
                sample.track.lyrics, lyric_elapsed,
            )
            transition: float | None = None
            if (
                cue_index is not None
                and active_index == cue_index
                and source.subtitle_animation != "none"
            ):
                cue = sample.track.lyrics[cue_index]
                cue_start = float(cue.get("start", lyric_elapsed)) - effective_offset
                transition = max(0.0, min(
                    1.0,
                    (sample.elapsed_seconds - cue_start)
                    / max(0.05, source.subtitle_animation_duration),
                ))
            content_state = (
                "lyrics", id(sample.track), cue_index, active_index, transition,
            )
        elif source.source_type is SourceType.TRACK_LIST:
            content_state = ("track_list", sample.track_number)
        elif source.source_type is SourceType.NOW_PLAYING:
            card_visible = sample.elapsed_seconds <= source.now_playing_duration
            if not card_visible:
                content_state = ("now_playing", "hidden")
            else:
                exit_duration = min(
                    source.now_playing_exit_duration,
                    source.now_playing_duration,
                )
                exit_start = source.now_playing_duration - exit_duration
                exit_progress: float | None = None
                if sample.elapsed_seconds >= exit_start and exit_duration > 0.0:
                    exit_progress = max(0.0, min(
                        1.0,
                        (sample.elapsed_seconds - exit_start) / exit_duration,
                    ))
                content_state = (
                    "now_playing",
                    sample.track.file_path,
                    sample.track.title,
                    sample.track.artist,
                    sample.track.album,
                    exit_progress,
                )
        elif source.source_type is SourceType.PROGRESS_BAR:
            progress = (
                global_seconds / max(0.01, self.playlist_duration)
                if source.progress_mode == "video"
                else sample.elapsed_seconds
                / max(0.01, sample.track.duration_seconds)
            )
            content_state = ("progress", max(0.0, min(1.0, progress)))
        elif source.source_type is SourceType.ALBUM_COVER:
            content_state = (
                "cover",
                source.content_path
                or (sample.track.file_path, sample.track.cover_path),
            )
        elif source.source_type is SourceType.BACKGROUND:
            content_state = (
                "background",
                (
                    sample.track.file_path,
                    sample.track.cover_path,
                    source.background_ambient,
                )
                if source.background_mode == "album_art"
                else source.background_mode,
            )
        elif source.source_type in {
            SourceType.IMAGE,
            SourceType.SHAPE,
            SourceType.LOGO,
            SourceType.WATERMARK,
        }:
            content_state = ("static",)
        elif source.source_type in {
            SourceType.VIDEO,
            SourceType.AUDIO_VISUALIZER,
            SourceType.AUDIO_WAVEFORM,
            SourceType.AUDIO_LEVEL_METER,
            SourceType.PARTICLE_OVERLAY,
        }:
            # These should normally be hidden and rendered by FFmpeg.  Opt out
            # if an unexpected caller includes one in a Canvas stream.
            return None
        else:
            return None

        animation_state: tuple[object, ...] = ("stable",)
        if sample.animation_phase is not None:
            style = (
                source.animation_in
                if sample.animation_phase == "in"
                else source.animation_out
            )
            if style != "none":
                configured_duration = (
                    source.animation_in_duration
                    if sample.animation_phase == "in"
                    else source.animation_out_duration
                )
                effective_duration = max(0.001, min(
                    configured_duration,
                    sample.animation_phase_duration,
                ))
                if sample.animation_phase == "in":
                    raw_progress = sample.elapsed_seconds / effective_duration
                else:
                    exit_start = max(
                        0.0, sample.track.duration_seconds - effective_duration,
                    )
                    raw_progress = (
                        sample.elapsed_seconds - exit_start
                    ) / effective_duration
                animation_state = (
                    sample.animation_phase,
                    style,
                    max(0.0, min(1.0, raw_progress)),
                )
        return (source.id, content_state, animation_state)

    def _stream_parameters(
        self, stream_key: str,
    ) -> tuple[float | None, float | None, bool, int | None]:
        """Resolve a public stream key into its capture band parameters."""
        if stream_key == "base":
            return None, self.z_bands[0][1], False, None
        if stream_key.startswith("layer:"):
            try:
                retained_layer_index = int(stream_key.removeprefix("layer:"))
                if retained_layer_index < 0:
                    raise IndexError
                z_min, z_max = self.z_bands[retained_layer_index + 1]
            except (ValueError, IndexError) as error:
                raise ValueError(f"Unknown Canvas stream: {stream_key}") from error
            return z_min, z_max, True, retained_layer_index
        raise ValueError(f"Unknown Canvas stream: {stream_key}")

    def capture(self, sample: ExportFrameSample) -> RenderFrame:
        """Stage one opaque base frame and its transparent foreground bands."""
        base = self.capture_stream(sample, "base")
        for index, _band in enumerate(self.z_bands[1:]):
            self.capture_stream(sample, f"layer:{index}")
        return base

    def capture_stream(
        self, sample: ExportFrameSample, stream_key: str,
    ) -> RenderFrame:
        """Capture one band, reusing only conservatively proven static pixels."""
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
        z_min, z_max, transparent, retained_layer_index = (
            self._stream_parameters(stream_key)
        )

        image = self._invariant_images.get(stream_key)
        if image is None:
            render_metrics: dict[str, object] = {}
            fixed_capture_rect = self._stream_capture_rects.get(stream_key)
            image = CanvasSnapshot.capture_track(
                self.scene,
                sample.track,
                sample.track_number,
                len(self.tracks),
                sample.track_start_seconds,
                z_min=z_min,
                z_max=z_max,
                transparent=transparent,
                partial_render=(
                    transparent
                    and fixed_capture_rect is None
                    and stream_key not in self.invariant_stream_keys
                ),
                render_metrics=render_metrics,
                capture_rect=fixed_capture_rect,
                band_source_items=self._stream_source_items.get(stream_key),
                **common,
            )
            artboard = self.scene.artboard_rect
            full_frame_pixels = max(
                1, round(artboard.width()) * round(artboard.height()),
            )
            self.full_frame_source_pixels += full_frame_pixels
            used_partial_render = (
                fixed_capture_rect is not None
                or bool(render_metrics.get("partial_render"))
            )
            if used_partial_render:
                capture_rect = fixed_capture_rect or render_metrics.get("capture_rect")
                if hasattr(capture_rect, "width") and hasattr(capture_rect, "height"):
                    self.partial_render_capture_count += 1
                    self.scene_render_source_pixels += max(
                        1,
                        round(float(capture_rect.width()))
                        * round(float(capture_rect.height())),
                    )
                else:
                    self.scene_render_source_pixels += full_frame_pixels
            else:
                self.scene_render_source_pixels += full_frame_pixels
            if stream_key in self.invariant_stream_keys:
                # QImage is implicitly shared and safe for the background
                # encoder to read.  Keep a detached copy so later scene paints
                # cannot alter the cached pixels through a reused buffer.
                image = image.copy()
                self._invariant_images[stream_key] = image
        rendered = self.stage_frame(
            image,
            sample.duration_seconds,
            stream_key,
        )
        if retained_layer_index is not None and self.retain_static_frames:
            self._static_band_frames[retained_layer_index].append(rendered)
        self.after_capture(sample.track_number, stream_key)
        return rendered

    def capture_invariant_stream(
        self,
        sample: ExportFrameSample,
        stream_key: str,
        duration_seconds: float,
    ) -> RenderFrame:
        """Capture a safe stream once and stage it for the complete timeline."""
        if stream_key not in self.invariant_stream_keys:
            raise ValueError(f"Canvas stream is not capture-invariant: {stream_key}")
        reference = ExportFrameSample(
            track=sample.track,
            track_number=sample.track_number,
            track_start_seconds=sample.track_start_seconds,
            duration_seconds=duration_seconds,
            elapsed_seconds=sample.elapsed_seconds,
            timeline_seconds=sample.timeline_seconds,
            animation_phase=sample.animation_phase,
            animation_progress=sample.animation_progress,
            animation_phase_duration=sample.animation_phase_duration,
        )
        return self.capture_stream(reference, stream_key)

    def static_layers(self) -> list[StaticOverlayLayer]:
        """Return transparent bands in the same Z and timeline order as capture."""
        return [
            StaticOverlayLayer(
                z_min if z_min is not None else -10_000.0,
                frames,
                *self.stream_origin(f"layer:{index}"),
            )
            for index, ((z_min, _z_max), frames) in enumerate(zip(
                self.z_bands[1:], self._static_band_frames, strict=True,
            ))
            if frames
        ]

    def stream_origin(self, stream_key: str) -> tuple[int, int]:
        """Return the cropped stream's top-left position on the artboard."""
        capture_rect = self._stream_capture_rects.get(stream_key)
        if capture_rect is None:
            return 0, 0
        artboard = self.scene.artboard_rect
        return (
            round(capture_rect.left() - artboard.left()),
            round(capture_rect.top() - artboard.top()),
        )
