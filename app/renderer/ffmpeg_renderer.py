"""FFmpeg-based static playlist video rendering pipeline."""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import os
import logging
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from math import cos, radians, sin
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory, mkstemp

from PySide6.QtGui import QImage, QImageReader

from app.models.playlist import PlaylistTrack
from app.renderer.python_visualizer import PythonVisualizerError, PythonVisualizerRenderer
from app.utils.subprocess_utils import hidden_process_kwargs


LOGGER = logging.getLogger(__name__)

WORK_MODE_STABLE = "stable"
WORK_MODE_AUTO = "auto"
WORK_MODE_MAX_SPEED = "max_speed"
WORK_MODES = (WORK_MODE_STABLE, WORK_MODE_AUTO, WORK_MODE_MAX_SPEED)


class FFmpegNotFoundError(RuntimeError):
    """Raised when no runnable FFmpeg executable can be found."""


class RenderError(RuntimeError):
    """Raised when FFmpeg rejects an input or cannot produce the video."""


class EncoderUnavailableError(RenderError):
    """Raised when the requested video encoder cannot start on this computer."""


class RenderCancelledError(RenderError):
    """Raised after a requested render cancellation safely stops FFmpeg."""


@dataclass(frozen=True, slots=True)
class RenderSettings:
    """Video and audio settings used for the Phase 3B renderer."""

    fps: int = 30
    video_codec: str = "libx264"
    crf: int = 18
    preset: str = "medium"
    audio_bitrate: str = "192k"
    output_width: int = 1920
    output_height: int = 1080
    work_mode: str = WORK_MODE_AUTO


@dataclass(frozen=True, slots=True)
class RenderResult:
    """Summary of a successfully generated video."""

    output_path: Path
    track_count: int


@dataclass(frozen=True, slots=True)
class ExportMetadata:
    """Container tags and per-track chapters written into the output MP4."""

    title: str = ""
    artist: str = ""
    comment: str = ""
    include_chapters: bool = True


@dataclass(frozen=True, slots=True)
class RenderFrame:
    """One Canvas image and its exact on-screen duration."""

    image: QImage | Path
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class PreparedVideoInput:
    """A CFR Canvas stream prepared without a PNG image sequence."""

    path: Path
    duration_seconds: float
    width: int
    height: int
    fps: int
    ready_for_mux: bool = False
    encoded_codec: str = ""


@dataclass(frozen=True, slots=True)
class StaticOverlayLayer:
    """A transparent, time-synchronised Canvas Z band for compositing."""

    z_index: float
    frames: list[RenderFrame]
    x: int = 0
    y: int = 0


@dataclass(frozen=True, slots=True)
class PreparedStaticOverlayLayer:
    """A lossless static Z band prepared as colour and alpha video tracks."""

    z_index: float
    video: PreparedVideoInput
    x: int = 0
    y: int = 0


@dataclass(frozen=True, slots=True)
class VisualizerOverlay:
    """One Python-rendered dynamic overlay layer for export."""

    x: int
    y: int
    width: int
    height: int
    style: str
    color: str
    personal_colors: tuple[str, ...] = ()
    opacity: float = 1.0
    bar_count: int = 32
    line_width: float = 3.0
    sensitivity: float = 1.2
    reactivity: float = 0.22
    noise_gate: float = 0.003
    min_level: float = 0.0
    max_level: float = 0.96
    attack: float = 0.55
    release: float = 0.16
    smoothing: float = 0.18
    curve: float = 0.9
    kind: str = "visualizer"
    effect_style: str = "bars"
    density: int = 42
    speed: float = 1.0
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
    particle_min_size: float = 1.0
    particle_max_size: float = 4.0
    particle_opacity: float = 0.62
    particle_direction: float = -90.0
    particle_drift: float = 0.25
    particle_twinkle: float = 0.25
    particle_glow: float = 0.2
    particle_secondary_color: str = "#7DD3FC"
    particle_seed: int = 17
    rotation: float = 0.0
    z_index: float = 0.0
    timeline_start: float = 0.0
    timeline_duration: float = 0.0
    animation_in: str = "none"
    animation_out: str = "none"
    animation_in_duration: float = 0.45
    animation_out_duration: float = 0.45


@dataclass(frozen=True, slots=True)
class VideoClipOverlay:
    """One scheduled video-file interval composited as a Canvas source."""

    path: Path
    timeline_start: float
    duration_seconds: float
    media_start_seconds: float
    x: int
    y: int
    width: int
    height: int
    z_index: float
    rotation: float = 0.0
    opacity: float = 1.0
    fit_mode: str = "cover"
    fill_color: str = "#000000"
    border_radius: float = 0.0
    brightness: float = 0.0
    contrast: float = 0.0
    saturation: float = 1.0
    grayscale: bool = False
    blur: float = 0.0
    speed: float = 1.0
    loop_input: bool = False


@dataclass(frozen=True, slots=True)
class VideoFileInput:
    """One physical FFmpeg input shared by matching scheduled occurrences."""

    path: Path
    media_start_seconds: float
    duration_seconds: float
    loop_input: bool = False


class FFmpegRenderer:
    """Normalizes tracks, concatenates them with FFmpeg, then renders an MP4."""

    def __init__(self, executable: str | Path | None = None) -> None:
        self.executable = self.find_executable(executable)
        self._available_encoders: frozenset[str] | None = None

    @staticmethod
    def find_executable(configured_path: str | Path | None = None) -> Path:
        """Resolve a configured FFmpeg executable or one available on PATH."""
        if configured_path:
            candidate = Path(configured_path)
            if candidate.is_file():
                return candidate
        bundled_root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None
        candidates = [
            Path(__file__).resolve().parents[2] / "ffmpeg" / "bin" / "ffmpeg.exe",
        ]
        if bundled_root is not None:
            candidates.insert(0, bundled_root / "ffmpeg" / "bin" / "ffmpeg.exe")
        for project_ffmpeg in candidates:
            if project_ffmpeg.is_file():
                return project_ffmpeg
        found = shutil.which("ffmpeg")
        if found:
            return Path(found)
        raise FFmpegNotFoundError("FFmpeg executable was not found.")

    def render(self, image: QImage | list[QImage] | list[RenderFrame] | PreparedVideoInput,
               tracks: list[PlaylistTrack], output_path: str | Path,
               settings: RenderSettings | None = None,
               progress_callback: Callable[[str, float, str], None] | None = None,
               cancel_event: threading.Event | None = None,
               visualizers: list[VisualizerOverlay] | None = None,
               static_layers: list[StaticOverlayLayer | PreparedStaticOverlayLayer] | None = None,
               video_clips: list[VideoClipOverlay] | None = None,
               metadata: "ExportMetadata | None" = None) -> RenderResult:
        """Create a static Canvas video whose audio is the ordered enabled playlist."""
        cancel_event = cancel_event or threading.Event()
        if cancel_event.is_set():
            raise RenderCancelledError("Rendering was cancelled.")
        active_tracks = [track for track in tracks if track.enabled]
        if not active_tracks:
            raise RenderError("Select at least one playlist track before exporting.")
        invalid_track = next(
            (track for track in active_tracks if track.duration_seconds <= 0.0), None
        )
        if invalid_track is not None:
            raise RenderError(
                f"Audio duration could not be determined: {invalid_track.title}"
            )
        visualizers = visualizers or []
        video_clips = video_clips or []
        video_file_inputs, video_input_slots = self._video_input_plan(video_clips)
        static_layers = static_layers or []
        prepared_video = image if isinstance(image, PreparedVideoInput) else None
        supplied_frames = (
            [] if prepared_video is not None
            else list(image) if isinstance(image, list) else [image]
        )
        if prepared_video is None and not supplied_frames:
            raise RenderError("No Canvas frames were supplied for export.")
        explicit_frames = bool(
            supplied_frames and isinstance(supplied_frames[0], RenderFrame)
        )
        if explicit_frames and not all(
            isinstance(frame, RenderFrame) for frame in supplied_frames
        ):
            raise RenderError("Export frames must use one consistent frame format.")
        frames = (
            [frame.image for frame in supplied_frames]
            if explicit_frames else supplied_frames
        )
        durations = (
            [frame.duration_seconds for frame in supplied_frames]
            if explicit_frames else []
        )
        if explicit_frames and any(duration <= 0 for duration in durations):
            raise RenderError("Each Canvas frame duration must be greater than zero.")
        if (prepared_video is None and not explicit_frames
                and len(frames) not in {1, len(active_tracks)}):
            raise RenderError(
                "The number of Canvas frames does not match the enabled playlist tracks."
            )
        # Legacy callers may supply one plain QImage for every track. Explicit
        # RenderFrame input already carries its own exact duration and may
        # intentionally represent an entire multi-track playlist with one image.
        if (
            prepared_video is None
            and not explicit_frames
            and len(frames) == 1
            and len(active_tracks) > 1
        ):
            frames *= len(active_tracks)
        self._report(
            progress_callback, "Preparing export", 0.005,
            (
                "Validating lossless Canvas stream"
                if prepared_video is not None
                else f"Validating visual frames 0/{len(frames)}"
            ),
        )
        if prepared_video is not None:
            if (not prepared_video.path.is_file()
                    or prepared_video.duration_seconds <= 0.0
                    or prepared_video.width <= 0 or prepared_video.height <= 0):
                raise RenderError("The prepared Canvas video is missing or invalid.")
        else:
            self._validate_frames(
                frames,
                lambda completed, total: self._report(
                    progress_callback, "Preparing export",
                    0.005 + 0.015 * completed / max(1, total),
                    f"Validating visual frames {completed}/{total}",
                ),
            )
        missing = [track.file_path for track in active_tracks if not Path(track.file_path).is_file()]
        if missing:
            raise RenderError(f"Audio file is missing: {missing[0]}")
        selected_settings = settings or RenderSettings()
        self._validate_settings(selected_settings)
        if prepared_video is not None and prepared_video.fps != selected_settings.fps:
            raise RenderError(
                "The prepared Canvas video frame rate does not match export settings."
            )
        direct_mux = bool(
            prepared_video is not None
            and prepared_video.ready_for_mux
            and not visualizers
            and not video_clips
            and not static_layers
        )
        if prepared_video is not None and prepared_video.ready_for_mux:
            if not direct_mux:
                raise RenderError(
                    "A directly encoded Canvas stream cannot be used with additional visual layers."
                )
            if prepared_video.encoded_codec != selected_settings.video_codec:
                raise RenderError(
                    "The prepared Canvas video codec does not match the selected encoder."
                )
            if (
                prepared_video.width != selected_settings.output_width
                or prepared_video.height != selected_settings.output_height
            ):
                raise RenderError(
                    "The prepared Canvas video resolution does not match the export settings."
                )
        if not direct_mux:
            self.ensure_encoder_available(selected_settings.video_codec)
        if cancel_event.is_set():
            raise RenderCancelledError("Rendering was cancelled.")
        self._report(progress_callback, "Preparing export", 0.02, "Preparing temporary files")
        target = Path(output_path).expanduser().resolve()
        if target.suffix.lower() != ".mp4":
            target = target.with_suffix(".mp4")
        target.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="playlist-video-") as temporary_directory:
            temporary = Path(temporary_directory)
            metadata_path = self._write_export_ffmetadata(
                temporary, active_tracks, metadata or ExportMetadata(), target,
            )
            if prepared_video is not None:
                prepared_path = prepared_video.path.resolve()
                visual_sequence = [
                    (prepared_path, prepared_video.duration_seconds),
                ]
                base_input_arguments = ["-i", str(prepared_path)]
            else:
                frame_paths: list[Path] = []
                for index, frame in enumerate(frames):
                    if cancel_event.is_set():
                        raise RenderCancelledError("Rendering was cancelled.")
                    if isinstance(frame, Path):
                        frame_path = frame.resolve()
                        if not frame_path.is_file():
                            raise RenderError(
                                f"A staged export frame is missing: {frame_path}"
                            )
                    else:
                        frame_path = temporary / f"canvas_{index:04d}.png"
                        if not frame.save(str(frame_path), "PNG"):
                            raise RenderError(
                                "Could not create a temporary Canvas image."
                            )
                    frame_paths.append(frame_path)
                visual_sequence = (
                    list(zip(frame_paths, durations, strict=True))
                    if explicit_frames
                    else self._visual_sequence(active_tracks, frame_paths)
                )
                base_input_arguments = []
            total_duration = self._timeline_duration(active_tracks)
            self._validate_visual_timeline(
                visual_sequence, static_layers, total_duration, selected_settings.fps,
            )
            segments = self._normalize_audio(
                active_tracks, temporary, selected_settings, progress_callback, cancel_event
            )
            segment_durations = self._insert_silence_for_gaps(
                active_tracks, segments, temporary, selected_settings, progress_callback, cancel_event
            )
            concat_path = temporary / "playlist.ffconcat"
            self._write_concat_file(concat_path, segments, segment_durations)
            audio_path = temporary / "playlist_audio.m4a"
            self._report(
                progress_callback, "Combining audio", 0.56,
                f"Combining audio 0.0s / {total_duration:.1f}s · 0%",
            )

            def combining_audio_progress(line: str) -> None:
                seconds = self._parse_progress_seconds(line)
                if seconds is None or total_duration <= 0.0:
                    return
                bounded = min(total_duration, max(0.0, seconds))
                fraction = bounded / total_duration
                self._report(
                    progress_callback, "Combining audio", 0.56 + fraction * 0.08,
                    self._timed_progress_message(
                        "Combining audio", bounded, total_duration, fraction,
                    ),
                )

            self._run([
                "-f", "concat", "-safe", "0", "-i", str(concat_path),
                "-c:a", "aac", "-ar", "48000", "-ac", "2",
                "-b:a", selected_settings.audio_bitrate,
                "-movflags", "+faststart", "-progress", "pipe:1", "-nostats",
                "-y", str(audio_path),
            ], progress_parser=combining_audio_progress, cancel_event=cancel_event)
            self._report(
                progress_callback, "Combining audio", 0.64,
                self._timed_progress_message(
                    "Combining audio", total_duration, total_duration, 1.0,
                ),
            )
            visualizer_paths: list[Path] = []
            if visualizers:
                self._report(progress_callback, "Preparing visualizers", 0.64,
                             "Analyzing audio and rendering Python visualizer frames")

                def visualizer_progress(fraction: float, message: str) -> None:
                    self._report(progress_callback, "Preparing visualizers", 0.64 + fraction * 0.12,
                                 message)

                try:
                    visualizer_paths = PythonVisualizerRenderer(self.executable).render_layers(
                        audio_path, visualizers, selected_settings.fps, temporary, cancel_event,
                        visualizer_progress, self._track_windows(active_tracks),
                    )
                except PythonVisualizerError as error:
                    if cancel_event.is_set():
                        raise RenderCancelledError("Rendering was cancelled.") from error
                    raise RenderError(str(error)) from error
            encoding_start = 0.78 if visualizers else 0.66
            encoding_span = 0.21 if visualizers else 0.33
            static_inputs: list[tuple[float, Path, str, int, int]] = []
            static_stage_start = 0.76 if visualizers else 0.64
            static_stage_span = max(0.0, encoding_start - static_stage_start)
            static_frame_total = sum(
                len(layer.frames)
                for layer in static_layers
                if isinstance(layer, StaticOverlayLayer)
            )
            # Each static frame is first validated and then staged/registered.
            # Counting both passes prevents the UI from appearing frozen during
            # header validation on projects with thousands of PNG frames.
            static_work_total = static_frame_total * 2
            static_work_completed = 0
            if static_frame_total:
                self._report(
                    progress_callback, "Preparing visual layers", static_stage_start,
                    f"Preparing visual layers 0/{static_frame_total} frames",
                )
            for layer_index, layer in enumerate(static_layers):
                if cancel_event.is_set():
                    raise RenderCancelledError("Rendering was cancelled.")
                if isinstance(layer, PreparedStaticOverlayLayer):
                    prepared_layer = layer.video
                    if (not prepared_layer.path.is_file()
                            or prepared_layer.width <= 0 or prepared_layer.height <= 0
                            or prepared_layer.fps != selected_settings.fps
                            or layer.x < 0 or layer.y < 0
                            or (prepared_video is not None and (
                                layer.x + prepared_layer.width > prepared_video.width
                                or layer.y + prepared_layer.height > prepared_video.height
                            ))):
                        raise RenderError(
                            "A prepared static overlay video is missing or invalid."
                        )
                    static_inputs.append((
                        layer.z_index, prepared_layer.path.resolve(), "alpha_pair",
                        layer.x, layer.y,
                    ))
                    continue
                if not layer.frames:
                    continue
                layer_images = [frame.image for frame in layer.frames]
                validation_completed = 0

                def visual_validation_progress(
                    completed: int, total: int, *, current_layer: int = layer_index,
                ) -> None:
                    nonlocal validation_completed, static_work_completed
                    static_work_completed += max(0, completed - validation_completed)
                    validation_completed = completed
                    fraction = static_work_completed / max(1, static_work_total)
                    self._report(
                        progress_callback, "Preparing visual layers",
                        static_stage_start + static_stage_span * fraction,
                        (
                            f"Checking visual layer {current_layer + 1}/{len(static_layers)}"
                            f" · frame {completed}/{total}"
                            f" · {round(fraction * 100)}%"
                        ),
                    )

                self._validate_frames(layer_images, visual_validation_progress)
                layer_paths: list[Path] = []
                for frame_index, frame in enumerate(layer_images):
                    if cancel_event.is_set():
                        raise RenderCancelledError("Rendering was cancelled.")
                    if isinstance(frame, Path):
                        layer_path = frame.resolve()
                    else:
                        layer_path = temporary / f"layer_{layer_index:02d}_{frame_index:06d}.png"
                        if not frame.save(str(layer_path), "PNG"):
                            raise RenderError("Could not create a static overlay frame.")
                    if not layer_path.is_file():
                        raise RenderError(f"A static overlay frame is missing: {layer_path}")
                    layer_paths.append(layer_path)
                    static_work_completed += 1
                    if (static_work_completed == static_work_total
                            or static_work_completed % max(1, static_work_total // 100) == 0):
                        fraction = static_work_completed / static_work_total
                        self._report(
                            progress_callback, "Preparing visual layers",
                            static_stage_start + static_stage_span * fraction,
                            (
                                f"Preparing visual layer {layer_index + 1}/{len(static_layers)}"
                                f" · frame {frame_index + 1}/{len(layer_images)}"
                                f" · {round(fraction * 100)}%"
                            ),
                        )
                layer_manifest = temporary / f"layer_{layer_index:02d}.ffconcat"
                self._write_visual_concat(
                    layer_manifest,
                    list(zip(layer_paths, [frame.duration_seconds for frame in layer.frames], strict=True)),
                )
                static_inputs.append((
                    layer.z_index, layer_manifest, "concat", layer.x, layer.y,
                ))
            if prepared_video is None:
                video_concat_path = temporary / "video.ffconcat"
                self._write_visual_concat(video_concat_path, visual_sequence)
                base_input_arguments = [
                    "-f", "concat", "-safe", "0", "-i", str(video_concat_path),
                ]

            def encoding_progress(line: str) -> None:
                seconds = self._parse_progress_seconds(line)
                if seconds is not None and total_duration > 0:
                    bounded = min(total_duration, max(0.0, seconds))
                    fraction = bounded / total_duration
                    finishing = bounded >= max(
                        0.0,
                        total_duration - max(0.5, 2.0 / selected_settings.fps),
                    )
                    self._report(
                        progress_callback,
                        "Finalizing export" if direct_mux or finishing else "Encoding video",
                        encoding_start + fraction * encoding_span,
                        (
                            "Finishing the MP4 file"
                            if finishing else
                            f"Muxing {bounded:.1f}s / {total_duration:.1f}s"
                            if direct_mux else
                            f"Encoding {bounded:.1f}s / {total_duration:.1f}s"
                        ),
                    )

            self._report(
                progress_callback,
                "Finalizing export" if direct_mux else "Encoding video",
                encoding_start,
                (
                    "Muxing prepared video and audio"
                    if direct_mux else "Rendering the final video"
                ),
            )
            try:
                output_descriptor, output_staging_name = mkstemp(
                    prefix=f".{target.stem}-",
                    suffix=".rendering.mp4",
                    dir=target.parent,
                )
                os.close(output_descriptor)
            except OSError as error:
                raise RenderError(
                    "Could not create a temporary output video beside the selected "
                    "destination. Check the destination folder and try again."
                ) from error
            temporary_video = Path(output_staging_name)
            video_arguments = [
                # Let FFmpeg use all available CPU workers for PNG decoding,
                # filtering and software encoding. Hardware encoders ignore this
                # safely while their video encode stage runs on the GPU.
                # A single filter worker prevents QHD/60 FPS filter graphs from
                # retaining many full-resolution RGBA frames at once. Encoding
                # threads still use the available CPU cores (or the selected GPU).
                "-threads", "0",
                "-filter_threads", str(self._filter_worker_count(selected_settings)),
                "-filter_complex_threads", str(self._filter_worker_count(selected_settings)),
                *base_input_arguments,
                "-i", str(audio_path),
            ]
            for visualizer_path in visualizer_paths:
                video_arguments.extend(["-i", str(visualizer_path)])
            for video_input in video_file_inputs:
                if video_input.loop_input:
                    video_arguments.extend(["-stream_loop", "-1"])
                video_arguments.extend([
                    "-ss", f"{video_input.media_start_seconds:.6f}",
                    "-t", f"{video_input.duration_seconds:.6f}",
                    "-i", str(video_input.path),
                ])
            for _z_index, layer_path, input_kind, _x, _y in static_inputs:
                if input_kind == "concat":
                    video_arguments.extend([
                        "-f", "concat", "-safe", "0", "-i", str(layer_path),
                    ])
                else:
                    video_arguments.extend(["-i", str(layer_path)])
            metadata_arguments: list[str] = []
            if metadata_path is not None:
                metadata_input_index = (
                    2 + len(visualizer_paths) + len(video_file_inputs)
                    + len(static_inputs)
                )
                video_arguments.extend(
                    ["-f", "ffmetadata", "-i", str(metadata_path)]
                )
                metadata_arguments = [
                    "-map_metadata", str(metadata_input_index),
                    "-map_chapters", str(metadata_input_index),
                ]
            if direct_mux:
                video_arguments.extend([
                    "-map", "0:v:0", "-map", "1:a:0",
                    *metadata_arguments,
                ])
            elif visualizer_paths or video_clips or static_inputs:
                video_arguments.extend([
                    "-filter_complex", self._layered_filter_graph(
                        visualizers, static_inputs, selected_settings.fps,
                        selected_settings.output_width, selected_settings.output_height,
                        video_clips=video_clips,
                        video_input_slots=video_input_slots,
                    ),
                    "-map", "[vout]", "-map", "1:a",
                    *metadata_arguments,
                ])
            else:
                video_arguments.extend([
                    "-vf", self._output_scaling_filter(selected_settings.fps, selected_settings.output_width,
                                                        selected_settings.output_height),
                    *metadata_arguments,
                ])
            if direct_mux:
                video_arguments.extend([
                    # Both streams were already encoded once at their selected
                    # final settings. This pass only writes the MP4 container.
                    "-c:v", "copy", "-c:a", "copy",
                    # Do not use -shortest here. Sparse Canvas inputs and framesync
                    # filters can disagree by a fraction of a frame at EOF, causing
                    # FFmpeg to retain queued frames while the UI appears stuck at
                    # 99%. The validated project timeline is authoritative.
                    "-t", f"{total_duration:.6f}",
                    "-movflags", "+faststart",
                    "-progress", "pipe:1", "-nostats", "-y", str(temporary_video),
                ])
            else:
                video_arguments.extend([
                    # Keep presentation timestamps strictly CFR.  The Canvas concat input is
                    # intentionally variable-duration, while Python layers are FPS-based.
                    "-fps_mode", "cfr", "-r", str(selected_settings.fps),
                    "-c:v", selected_settings.video_codec,
                    *self._video_encoding_arguments(selected_settings),
                    # playlist_audio.m4a is already the final AAC stream. Copying it
                    # avoids a redundant lossy pass and preserves the prepared audio
                    # bytes and timing exactly.
                    "-c:a", "copy",
                    "-pix_fmt", "yuv420p",
                    # Bound the filter graph and muxer to the exact timeline.
                    # Without this output limit, a one-frame EOF mismatch between
                    # the base, alpha-pair and audio streams can make FFmpeg keep
                    # buffering after the last visible frame.
                    "-t", f"{total_duration:.6f}",
                    "-movflags", "+faststart",
                    "-progress", "pipe:1", "-nostats", "-y", str(temporary_video),
                ])
            LOGGER.info(
                "Final FFmpeg stage: codec=%s direct_mux=%s duration=%.3fs "
                "fps=%d resolution=%dx%d filter_workers=%d visualizers=%d "
                "video_clips=%d static_inputs=%d",
                "copy" if direct_mux else selected_settings.video_codec,
                direct_mux,
                total_duration,
                selected_settings.fps,
                selected_settings.output_width,
                selected_settings.output_height,
                self._filter_worker_count(selected_settings),
                len(visualizer_paths),
                len(video_clips),
                len(static_inputs),
            )
            try:
                self._run(
                    video_arguments,
                    progress_parser=encoding_progress,
                    cancel_event=cancel_event,
                )
                if cancel_event.is_set():
                    raise RenderCancelledError("Rendering was cancelled.")
                self._report(
                    progress_callback, "Finalizing export", 0.995,
                    "Moving the completed video to the selected location",
                )
                try:
                    temporary_video.replace(target)
                except OSError as error:
                    raise RenderError(
                        "Could not replace the output video. Close any program using "
                        f"'{target.name}', check the destination folder, and try again."
                    ) from error
            finally:
                try:
                    temporary_video.unlink(missing_ok=True)
                except OSError:
                    LOGGER.warning(
                        "Could not remove incomplete output staging file: %s",
                        temporary_video,
                    )
        self._report(progress_callback, "Complete", 1.0, "Export completed")
        return RenderResult(target, len(active_tracks))

    @staticmethod
    def _visual_sequence(tracks: list[PlaylistTrack], frame_paths: list[Path]) -> list[tuple[Path, float]]:
        """Pair per-track Canvas frames with durations, including manual silent gaps."""
        sequence: list[tuple[Path, float]] = []
        cursor = 0.0
        last_frame = frame_paths[0]
        for track, frame_path in zip(tracks, frame_paths, strict=True):
            requested = track.start_time_seconds if track.start_time_seconds is not None else cursor
            start = max(cursor, requested)
            gap = max(0.0, start - cursor)
            if gap > 0.001:
                sequence.append((last_frame, gap))
            sequence.append((frame_path, max(0.001, track.duration_seconds)))
            last_frame = frame_path
            cursor = start + track.duration_seconds
        return sequence

    @staticmethod
    def _python_visualizer_filter_graph(visualizers: list[VisualizerOverlay], fps: int,
                                         output_width: int, output_height: int) -> str:
        """Composite pre-rendered Python alpha layers; no FFmpeg analyzer filters are used."""
        # The Canvas uses a concat manifest of still images.  Convert that stream to
        # constant frame rate *before* overlay framesync, otherwise framesync outputs
        # only one frame per still image and the video visibly stutters.
        graph: list[str] = [
            f"[0:v]fps={fps}:start_time=0,settb=AVTB,setpts=N/({fps}*TB)[base]"
        ]
        current = "[base]"
        for index, overlay in enumerate(visualizers):
            output = "[composited]" if index == len(visualizers) - 1 else f"[layer{index}]"
            # Inputs 0 and 1 are Canvas and playlist audio; Python layer videos begin at 2.
            layer_input = f"[{index + 2}:v]"
            rotation = float(overlay.rotation) % 360.0
            x, y = overlay.x, overlay.y
            if rotation:
                radians_value = radians(rotation)
                rotated_width = abs(overlay.width * cos(radians_value)) + abs(overlay.height * sin(radians_value))
                rotated_height = abs(overlay.width * sin(radians_value)) + abs(overlay.height * cos(radians_value))
                x = round(overlay.x - (rotated_width - overlay.width) / 2.0)
                y = round(overlay.y - (rotated_height - overlay.height) / 2.0)
                rotated_label = f"[rotated{index}]"
                graph.append(
                    f"{layer_input}rotate={radians_value:.12f}:"
                    f"ow=rotw({radians_value:.12f}):"
                    f"oh=roth({radians_value:.12f}):fillcolor=none{rotated_label}"
                )
                layer_input = rotated_label
            graph.append(f"{current}{layer_input}overlay={x}:{y}:eof_action=pass{output}")
            current = output
        # Overlay coordinates are canvas coordinates.  Scale only after compositing
        # so a source at (100, 100) stays at that location on the artboard.
        graph.append(
            f"{current}{FFmpegRenderer._output_scaling_filter(fps, output_width, output_height, include_fps=False)}[vout]"
        )
        return ";".join(graph)

    @staticmethod
    def _layered_filter_graph(visualizers: list[VisualizerOverlay],
                              static_layers: list[
                                  tuple[float, Path]
                                  | tuple[float, Path, str]
                                  | tuple[float, Path, str, int, int]
                              ], fps: int,
                              output_width: int, output_height: int,
                              video_clips: list[VideoClipOverlay] | None = None,
                              video_input_slots: list[int] | None = None) -> str:
        """Interleave reactive video and transparent static Z bands in Canvas order."""
        video_clips = video_clips or []
        video_file_inputs, planned_slots = FFmpegRenderer._video_input_plan(video_clips)
        if video_input_slots is None:
            video_input_slots = planned_slots
        if len(video_input_slots) != len(video_clips):
            raise ValueError("Video input slots do not match scheduled clips.")
        graph: list[str] = [f"[0:v]fps={fps}:start_time=0,settb=AVTB,setpts=N/({fps}*TB)[base]"]
        entries: list[tuple[float, int, str]] = []
        # Inputs 0/1 are base canvas and audio. Dynamic video inputs precede
        # static concat inputs, preserving their independent source timing.
        entries.extend((overlay.z_index, index, "dynamic") for index, overlay in enumerate(visualizers))
        entries.extend((clip.z_index, index, "video") for index, clip in enumerate(video_clips))
        video_offset = 2 + len(visualizers)
        static_offset = video_offset + len(video_file_inputs)
        video_layer_inputs = [""] * len(video_clips)
        for slot in range(len(video_file_inputs)):
            consumers = [
                clip_index for clip_index, input_slot in enumerate(video_input_slots)
                if input_slot == slot
            ]
            input_label = f"[{video_offset + slot}:v]"
            if len(consumers) == 1:
                video_layer_inputs[consumers[0]] = input_label
                continue
            outputs = [f"[vsrc{slot}_{branch}]" for branch in range(len(consumers))]
            graph.append(f"{input_label}split={len(outputs)}{''.join(outputs)}")
            for clip_index, output in zip(consumers, outputs, strict=True):
                video_layer_inputs[clip_index] = output
        entries.extend(
            (layer[0], index, "static")
            for index, layer in enumerate(static_layers)
        )
        current = "[base]"
        # A transparent static band is tagged with the Z value of the dynamic
        # layer immediately below it.  Compare the layer kind before its input
        # index so that the dynamic layer is always drawn first at that shared
        # boundary.  Comparing input indices first made a later particle/noise
        # layer cover a static foreground whenever an earlier empty band had
        # been omitted.
        ordered_entries = sorted(
            entries,
            key=lambda entry: (
                entry[0], 0 if entry[2] in {"dynamic", "video"} else 1, entry[1],
            ),
        )
        for order, (_z_value, index, kind) in enumerate(ordered_entries):
            output = "[composited]" if order == len(entries) - 1 else f"[zlayer{order}]"
            if kind == "dynamic":
                overlay = visualizers[index]
                layer_input = f"[{index + 2}:v]"
                rotation = float(overlay.rotation) % 360.0
                x, y = overlay.x, overlay.y
                if rotation:
                    angle = radians(rotation)
                    rotated_width = abs(overlay.width * cos(angle)) + abs(overlay.height * sin(angle))
                    rotated_height = abs(overlay.width * sin(angle)) + abs(overlay.height * cos(angle))
                    x = round(x - (rotated_width - overlay.width) / 2.0)
                    y = round(y - (rotated_height - overlay.height) / 2.0)
                    rotated = f"[zrot{order}]"
                    graph.append(
                        f"{layer_input}rotate={angle:.12f}:"
                        f"ow=rotw({angle:.12f}):"
                        f"oh=roth({angle:.12f}):fillcolor=none{rotated}"
                    )
                    layer_input = rotated
                graph.append(f"{current}{layer_input}overlay={x}:{y}:eof_action=pass{output}")
            elif kind == "video":
                clip = video_clips[index]
                source_input = video_layer_inputs[index]
                layer_input = f"[vclip{order}]"
                if clip.fit_mode == "stretch":
                    geometry = f"scale={clip.width}:{clip.height}"
                elif clip.fit_mode == "contain":
                    fill_color = FFmpegRenderer._filter_color(clip.fill_color)
                    geometry = (
                        f"scale={clip.width}:{clip.height}:force_original_aspect_ratio=decrease,"
                        f"pad={clip.width}:{clip.height}:(ow-iw)/2:(oh-ih)/2:"
                        f"color={fill_color}"
                    )
                else:
                    geometry = (
                        f"scale={clip.width}:{clip.height}:force_original_aspect_ratio=increase,"
                        f"crop={clip.width}:{clip.height}"
                    )
                filters = [
                    f"{source_input}trim=duration="
                    f"{clip.duration_seconds * max(0.05, clip.speed):.8f},"
                    f"settb=AVTB,setpts=(PTS-STARTPTS)/{max(0.05, clip.speed):.8f}",
                    geometry,
                    # Canvas stores both controls as percentages. FFmpeg's eq
                    # filter expects brightness in -1..1 and a contrast factor.
                    f"eq=brightness={max(-1.0, min(1.0, clip.brightness / 100.0)):.4f}:"
                    f"contrast={max(0.0, min(2.0, 1.0 + clip.contrast / 100.0)):.4f}:"
                    f"saturation={max(0.0, min(3.0, clip.saturation)):.4f}",
                ]
                if clip.grayscale:
                    filters.append("hue=s=0")
                if clip.blur > 0.01:
                    filters.append(f"boxblur={min(40.0, clip.blur):.3f}:1")
                radius = max(
                    0.0,
                    min(float(clip.border_radius), clip.width / 2.0, clip.height / 2.0),
                )
                if radius >= 0.5:
                    # Preserve the Canvas rounded-rectangle clip. This runs only
                    # when a radius is configured, avoiding a per-pixel export
                    # filter for the default square video element.
                    dx = (
                        f"max(max({radius:.4f}-X,X-(W-1-{radius:.4f})),0)"
                    )
                    dy = (
                        f"max(max({radius:.4f}-Y,Y-(H-1-{radius:.4f})),0)"
                    )
                    filters.append(
                        "format=rgba,"
                        "geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
                        f"a='if(lte(pow({dx},2)+pow({dy},2),"
                        f"pow({radius:.4f},2)),alpha(X,Y),0)'"
                    )
                filters.extend((
                    "format=rgba",
                    f"colorchannelmixer=aa={max(0.0, min(1.0, clip.opacity)):.4f}",
                    f"setpts=PTS+{max(0.0, clip.timeline_start):.8f}/TB{layer_input}",
                ))
                graph.append(",".join(filters))
                rotation = float(clip.rotation) % 360.0
                x, y = clip.x, clip.y
                if rotation:
                    angle = radians(rotation)
                    rotated_width = (
                        abs(clip.width * cos(angle))
                        + abs(clip.height * sin(angle))
                    )
                    rotated_height = (
                        abs(clip.width * sin(angle))
                        + abs(clip.height * cos(angle))
                    )
                    x = round(x - (rotated_width - clip.width) / 2.0)
                    y = round(y - (rotated_height - clip.height) / 2.0)
                    rotated = f"[vrot{order}]"
                    graph.append(
                        f"{layer_input}rotate={angle:.12f}:"
                        f"ow=rotw({angle:.12f}):"
                        f"oh=roth({angle:.12f}):fillcolor=none{rotated}"
                    )
                    layer_input = rotated
                graph.append(
                    f"{current}{layer_input}overlay={x}:{y}:eof_action=pass:shortest=0{output}"
                )
            else:
                static_layer = static_layers[index]
                input_kind = static_layer[2] if len(static_layer) == 3 else "concat"
                if len(static_layer) >= 5:
                    input_kind = static_layer[2]
                    layer_x, layer_y = int(static_layer[3]), int(static_layer[4])
                else:
                    layer_x, layer_y = 0, 0
                input_index = static_offset + index
                if input_kind == "alpha_pair":
                    layer_input = f"[staticrgba{order}]"
                    graph.append(
                        f"[{input_index}:v:0][{input_index}:v:1]"
                        f"alphamerge{layer_input}"
                    )
                else:
                    layer_input = f"[{input_index}:v]"
                graph.append(
                    f"{current}{layer_input}overlay={layer_x}:{layer_y}:"
                    f"eof_action=pass{output}"
                )
            current = output
        graph.append(f"{current}{FFmpegRenderer._output_scaling_filter(fps, output_width, output_height, include_fps=False)}[vout]")
        return ";".join(graph)

    @staticmethod
    def _video_input_plan(
        video_clips: list[VideoClipOverlay],
    ) -> tuple[list[VideoFileInput], list[int]]:
        """Deduplicate matching files while retaining per-occurrence filter branches."""
        groups: list[dict[str, object]] = []
        group_slots: dict[tuple[str, float], int] = {}
        clip_slots: list[int] = []
        for clip in video_clips:
            resolved = clip.path.resolve()
            key = (
                os.path.normcase(str(resolved)),
                round(max(0.0, clip.media_start_seconds), 6),
            )
            slot = group_slots.get(key)
            raw_duration = max(
                0.000001,
                clip.duration_seconds * max(0.05, clip.speed),
            )
            if slot is None:
                slot = len(groups)
                group_slots[key] = slot
                groups.append({
                    "path": resolved,
                    "media_start": key[1],
                    "duration": raw_duration,
                    "loop": bool(clip.loop_input),
                })
            else:
                group = groups[slot]
                group["duration"] = max(float(group["duration"]), raw_duration)
                group["loop"] = bool(group["loop"]) or bool(clip.loop_input)
            clip_slots.append(slot)
        return [
            VideoFileInput(
                path=group["path"],  # type: ignore[arg-type]
                media_start_seconds=float(group["media_start"]),
                duration_seconds=float(group["duration"]),
                loop_input=bool(group["loop"]),
            )
            for group in groups
        ], clip_slots

    @staticmethod
    def _filter_color(value: str) -> str:
        """Return an FFmpeg-safe opaque RGB literal for Canvas fill colors."""
        color = value.strip().removeprefix("#")
        if len(color) in {6, 8} and all(character in "0123456789abcdefABCDEF" for character in color):
            return f"0x{color[:6]}"
        return "0x000000"

    @staticmethod
    def _output_scaling_filter(fps: int, output_width: int, output_height: int,
                               include_fps: bool = True) -> str:
        """Scale without stretching the authored canvas and pad any aspect mismatch."""
        prefix = f"fps={fps}," if include_fps else ""
        return (
            f"{prefix}scale={output_width}:{output_height}:"
            "force_original_aspect_ratio=decrease,"
            f"pad={output_width}:{output_height}:(ow-iw)/2:(oh-ih)/2:color=black"
        )

    @staticmethod
    def _validate_frames(
        frames: list[QImage | Path],
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> None:
        """Reject inconsistent or empty snapshots before FFmpeg can shift a layout."""
        if not frames:
            raise RenderError("The export canvas frame is empty.")
        path_size_cache: dict[Path, object] = {}

        def frame_size(frame: QImage | Path) -> object:
            if isinstance(frame, Path):
                normalized = frame.resolve()
                cached = path_size_cache.get(normalized)
                if cached is not None:
                    return cached
                if not normalized.is_file():
                    raise RenderError(f"A staged export frame is missing: {frame}")
                reader = QImageReader(str(normalized))
                size = reader.size()
                if not size.isValid():
                    raise RenderError(
                        f"Could not read the staged export frame header: {frame}"
                    )
                path_size_cache[normalized] = size
                return size
            else:
                image = frame
            if image.isNull():
                raise RenderError("The export canvas frame is empty.")
            size = image.size()
            if isinstance(frame, Path):
                path_size_cache[normalized] = size
            return size

        reference_size = frame_size(frames[0])
        if progress_callback:
            progress_callback(1, len(frames))
        for index, frame in enumerate(frames[1:], start=2):
            if frame_size(frame) != reference_size:
                raise RenderError(
                    "All export frames must use one canvas size. "
                    f"Frame {index} does not match the first canvas frame."
                )
            if (progress_callback and (
                    index == len(frames)
                    or index % max(1, len(frames) // 100) == 0)):
                progress_callback(index, len(frames))

    @staticmethod
    def _validate_settings(settings: RenderSettings) -> None:
        """Reject invalid encoder geometry before it can create a corrupt output."""
        if settings.fps <= 0 or settings.fps > 240:
            raise RenderError("The export frame rate must be between 1 and 240 FPS.")
        if settings.output_width <= 0 or settings.output_height <= 0:
            raise RenderError("The export resolution must be greater than zero.")
        if settings.output_width % 2 or settings.output_height % 2:
            raise RenderError(
                "The export width and height must be even numbers for YUV video."
            )
        if not settings.video_codec.strip():
            raise RenderError("Select a video encoder before exporting.")

    @staticmethod
    def _validate_visual_timeline(
        visual_sequence: list[tuple[Path, float]],
        static_layers: list[StaticOverlayLayer | PreparedStaticOverlayLayer],
        expected_duration: float,
        fps: int,
    ) -> None:
        """Require every composited stream to cover the same playlist timeline."""
        tolerance = max(0.002, 1.0 / max(1, fps) + 0.001)

        def validate(label: str, durations: list[float]) -> None:
            if not durations or any(duration <= 0.0 for duration in durations):
                raise RenderError(f"{label} contains an invalid frame duration.")
            actual = sum(durations)
            if abs(actual - expected_duration) > tolerance:
                raise RenderError(
                    f"{label} is {actual:.3f}s long, but the playlist is "
                    f"{expected_duration:.3f}s. Export was stopped to prevent "
                    "misaligned video and audio."
                )

        validate("The prepared Canvas video", [duration for _path, duration in visual_sequence])
        for index, layer in enumerate(static_layers, start=1):
            if isinstance(layer, PreparedStaticOverlayLayer):
                validate(
                    f"Static overlay layer {index}",
                    [layer.video.duration_seconds],
                )
                continue
            if not layer.frames:
                continue
            validate(
                f"Static overlay layer {index}",
                [frame.duration_seconds for frame in layer.frames],
            )

    def ensure_encoder_available(self, encoder: str) -> None:
        """Fail early when the active FFmpeg build lacks the selected encoder."""
        if self._available_encoders is None:
            try:
                completed = subprocess.run(
                    [str(self.executable), "-hide_banner", "-encoders"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                    **hidden_process_kwargs(),
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise EncoderUnavailableError(
                    "Could not inspect the available FFmpeg encoders."
                ) from error
            if completed.returncode != 0:
                raise EncoderUnavailableError(
                    "Could not inspect the available FFmpeg encoders."
                )
            self._available_encoders = frozenset(completed.stdout.split())
        if encoder not in self._available_encoders:
            raise EncoderUnavailableError(
                f"The selected video encoder '{encoder}' is unavailable in this FFmpeg build. "
                "Choose a supported CPU/GPU encoder in Settings."
            )

    def preflight_export(
        self,
        tracks: list[PlaylistTrack],
        output_path: str | Path,
        settings: RenderSettings,
    ) -> None:
        """Validate inputs, destination, and real encoder startup before capture."""
        active_tracks = [track for track in tracks if track.enabled]
        if not active_tracks:
            raise RenderError("Select at least one playlist track before exporting.")
        invalid_track = next(
            (track for track in active_tracks if track.duration_seconds <= 0.0), None
        )
        if invalid_track is not None:
            raise RenderError(
                f"Audio duration could not be determined: {invalid_track.title}"
            )
        missing = next(
            (Path(track.file_path) for track in active_tracks
             if not Path(track.file_path).is_file()),
            None,
        )
        if missing is not None:
            raise RenderError(f"Audio file is missing: {missing}")

        self._validate_settings(settings)
        target = Path(output_path).expanduser().resolve()
        if target.suffix.lower() != ".mp4":
            target = target.with_suffix(".mp4")
        if target.exists() and not target.is_file():
            raise RenderError("The selected output path is not a video file.")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor, probe_name = mkstemp(
                prefix=".playlist-canvas-write-test-",
                suffix=".tmp",
                dir=target.parent,
            )
            os.close(descriptor)
            Path(probe_name).unlink()
        except OSError as error:
            raise RenderError(
                "The selected output folder is not writable. Choose another "
                "folder and try again."
            ) from error

        self.ensure_encoder_available(settings.video_codec)
        self.ensure_encoder_usable(settings)

    def ensure_encoder_usable(self, settings: RenderSettings) -> None:
        """Encode at the selected format so hardware startup checks are realistic."""
        encoder = settings.video_codec
        pixel_format = (
            "nv12"
            if encoder in {
                "h264_nvenc", "hevc_nvenc", "h264_qsv", "hevc_qsv",
                "h264_amf", "hevc_amf",
            }
            else "yuv420p"
        )
        probe_source = (
            f"color=c=black:s={settings.output_width}x{settings.output_height}"
            f":r={settings.fps}"
        )
        try:
            completed = subprocess.run(
                [
                    str(self.executable), "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", probe_source,
                    "-frames:v", "1", "-an", "-c:v", encoder,
                    *self._video_encoding_arguments(settings),
                    "-pix_fmt", pixel_format, "-f", "null", "-",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                **hidden_process_kwargs(),
            )
        except subprocess.TimeoutExpired as error:
            raise EncoderUnavailableError(
                f"The selected video encoder '{encoder}' did not respond during "
                "the selected-format startup check."
            ) from error
        except OSError as error:
            raise EncoderUnavailableError(
                "Could not start FFmpeg for the video encoder check."
            ) from error
        if completed.returncode == 0:
            return
        details = completed.stderr.strip()
        if len(details) > 1600:
            details = details[-1600:]
        suffix = f"\n\nFFmpeg: {details}" if details else ""
        raise EncoderUnavailableError(
            f"The selected video encoder '{encoder}' is installed but could not "
            f"start on this computer. Check the GPU driver or choose CPU H.264."
            f"{suffix}"
        )

    def direct_encoding_profile(
        self, settings: RenderSettings,
    ) -> "DirectVideoEncodingProfile":
        """Describe how to encode an opaque Canvas stream straight to the final codec.

        Callers that stream Canvas frames use this instead of reaching into the
        renderer's private encoding arguments.
        """
        from app.renderer.static_video_stream import DirectVideoEncodingProfile

        return DirectVideoEncodingProfile(
            settings.output_width,
            settings.output_height,
            settings.video_codec,
            tuple(self._video_encoding_arguments(settings)),
        )

    @staticmethod
    def _video_encoding_arguments(settings: RenderSettings) -> list[str]:
        """Return quality controls compatible with CPU and common GPU encoders."""
        encoder = settings.video_codec
        if encoder in {"libx264", "libx265"}:
            # The Canvas can now contain animated overlays; avoid the still-image tuning.
            return ["-crf", str(settings.crf), "-preset", settings.preset]
        if encoder in {"h264_nvenc", "hevc_nvenc"}:
            # NVENC uses p1 (fastest) through p7 (best compression).  The app's
            # familiar preset names now have a real effect for GPU exports.
            nvenc_presets = {
                "ultrafast": "p1", "superfast": "p1", "veryfast": "p2",
                "faster": "p2", "fast": "p3", "medium": "p4",
                "slow": "p5", "slower": "p6", "veryslow": "p7",
            }
            return [
                "-preset", nvenc_presets.get(settings.preset, "p3"),
                "-rc", "vbr", "-cq", str(settings.crf), "-b:v", "0",
            ]
        if encoder in {"h264_qsv", "hevc_qsv"}:
            qsv_presets = {
                "ultrafast": "veryfast", "superfast": "veryfast",
                "veryfast": "veryfast", "faster": "faster", "fast": "fast",
                "medium": "medium", "slow": "slow", "slower": "slower",
                "veryslow": "veryslow",
            }
            return [
                "-preset", qsv_presets.get(settings.preset, "fast"),
                "-global_quality", str(settings.crf),
            ]
        if encoder in {"h264_amf", "hevc_amf"}:
            quality = (
                "speed" if settings.preset in {"ultrafast", "superfast", "veryfast", "faster"}
                else "quality" if settings.preset in {"slow", "slower", "veryslow"}
                else "balanced"
            )
            return [
                "-quality", quality, "-rc", "cqp", "-qp_i", str(settings.crf),
                "-qp_p", str(settings.crf),
            ]
        return ["-crf", str(settings.crf), "-preset", settings.preset]

    @staticmethod
    def _filter_worker_count(settings: RenderSettings) -> int:
        """Use modest filter parallelism without multiplying 4K frame memory."""
        pixels_per_second = (
            settings.output_width * settings.output_height * settings.fps
        )
        if settings.work_mode == WORK_MODE_STABLE:
            return 1
        if settings.work_mode == WORK_MODE_MAX_SPEED:
            if pixels_per_second <= 1920 * 1080 * 60:
                return 4
            if pixels_per_second <= 2560 * 1440 * 60:
                return 2
            return 1
        # Two filter workers improve the common 720p/1080p path. Higher-rate 4K
        # work remains single-worker to avoid retaining several large RGBA frames.
        return 2 if pixels_per_second <= 1920 * 1080 * 60 else 1

    @staticmethod
    def _audio_worker_count(settings: RenderSettings, track_count: int) -> int:
        """Choose bounded independent-track concurrency for the work mode."""
        if track_count <= 0:
            return 0
        if settings.work_mode == WORK_MODE_STABLE:
            return 1
        cpu_count = os.cpu_count() or 2
        if settings.work_mode == WORK_MODE_MAX_SPEED:
            cap = max(2, min(6, cpu_count - 1))
        else:
            cap = max(1, min(4, cpu_count // 2))
        return min(track_count, cap)

    def _normalize_audio(self, tracks: list[PlaylistTrack], directory: Path,
                         settings: RenderSettings,
                         progress_callback: Callable[[str, float, str], None] | None,
                         cancel_event: threading.Event) -> list[Path]:
        """Normalize every track to an exact-duration lossless segment."""
        segments: list[Path | None] = [None] * len(tracks)
        completed = 0
        progress_lock = threading.Lock()
        track_seconds = [0.0] * len(tracks)
        total_seconds = max(0.001, sum(track.duration_seconds for track in tracks))
        self._report(
            progress_callback, "Preparing audio", 0.05,
            f"Normalizing audio 0/{len(tracks)} complete · 0% total",
        )

        def normalize(index: int, track: PlaylistTrack) -> tuple[int, Path]:
            output = directory / f"track_{index:04d}.nut"
            duration = f"{track.duration_seconds:.6f}"

            def normalization_progress(line: str) -> None:
                seconds = self._parse_progress_seconds(line)
                if seconds is None:
                    return
                with progress_lock:
                    track_seconds[index] = max(
                        track_seconds[index],
                        min(track.duration_seconds, max(0.0, seconds)),
                    )
                    fraction = min(1.0, sum(track_seconds) / total_seconds)
                    self._report(
                        progress_callback, "Preparing audio",
                        0.05 + 0.48 * fraction,
                        (
                            f"Normalizing audio {completed}/{len(tracks)} complete"
                            f" · {track.filename}"
                            f" · {track_seconds[index]:.1f}s / {track.duration_seconds:.1f}s"
                            f" · {round(fraction * 100)}% total"
                        ),
                    )

            self._run([
                # Independent tracks are normalized concurrently, so each small
                # audio decode gets one FFmpeg worker instead of oversubscribing
                # every CPU core. PCM output and ordering remain unchanged.
                "-threads", "1", "-i", track.file_path, "-vn", "-map", "0:a:0",
                "-af", f"apad=whole_dur={duration}", "-t", duration,
                "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", "-f", "nut",
                "-progress", "pipe:1", "-nostats", "-y", str(output),
            ], progress_parser=normalization_progress, cancel_event=cancel_event)
            return index, output

        worker_count = self._audio_worker_count(settings, len(tracks))
        self._report(
            progress_callback, "Preparing audio", 0.05,
            f"Normalizing {len(tracks)} independent track(s) with "
            f"{worker_count} parallel worker(s)",
        )
        with ThreadPoolExecutor(
            max_workers=worker_count, thread_name_prefix="audio-normalize",
        ) as executor:
            futures = [
                executor.submit(normalize, index, track)
                for index, track in enumerate(tracks)
            ]
            for future in as_completed(futures):
                index, output = future.result()
                segments[index] = output
                with progress_lock:
                    track_seconds[index] = tracks[index].duration_seconds
                    completed += 1
                    timeline_fraction = min(1.0, sum(track_seconds) / total_seconds)
                    self._report(
                        progress_callback, "Preparing audio",
                        0.05 + 0.48 * timeline_fraction,
                        f"Normalized {completed}/{len(tracks)} tracks",
                    )
        self._report(progress_callback, "Preparing audio", 0.53, "Audio normalization complete")
        return [segment for segment in segments if segment is not None]

    def _insert_silence_for_gaps(self, tracks: list[PlaylistTrack], segments: list[Path],
                                 directory: Path, settings: RenderSettings,
                                 progress_callback: Callable[[str, float, str], None] | None,
                                 cancel_event: threading.Event) -> list[float]:
        """Insert lossless silence segments for user-defined timeline gaps."""
        if not any(track.start_time_seconds is not None for track in tracks):
            return [track.duration_seconds for track in tracks]
        combined: list[Path] = []
        combined_durations: list[float] = []
        total_gap_seconds = 0.0
        gap_count = 0
        planning_cursor = 0.0
        for track in tracks:
            requested = (
                track.start_time_seconds
                if track.start_time_seconds is not None else planning_cursor
            )
            planned_start = max(planning_cursor, requested)
            planned_gap = max(0.0, planned_start - planning_cursor)
            total_gap_seconds += planned_gap
            if planned_gap > 0.001:
                gap_count += 1
            planning_cursor = planned_start + track.duration_seconds
        created_gap_seconds = 0.0
        created_gap_count = 0
        cursor = 0.0
        for index, (track, segment) in enumerate(zip(tracks, segments, strict=True)):
            requested = track.start_time_seconds if track.start_time_seconds is not None else cursor
            start = max(cursor, requested)
            gap = max(0.0, start - cursor)
            if gap > 0.001:
                silence = directory / f"silence_{index:04d}.nut"

                def silence_progress(line: str, gap_seconds: float = gap) -> None:
                    seconds = self._parse_progress_seconds(line)
                    if seconds is None or total_gap_seconds <= 0.0:
                        return
                    current = min(gap_seconds, max(0.0, seconds))
                    fraction = min(
                        1.0, (created_gap_seconds + current) / total_gap_seconds,
                    )
                    self._report(
                        progress_callback, "Preparing audio", 0.53 + 0.03 * fraction,
                        (
                            f"Creating silence {created_gap_count + 1}/{max(1, gap_count)}"
                            f" · {current:.1f}s / {gap_seconds:.1f}s"
                            f" · {round(fraction * 100)}% total"
                        ),
                    )

                self._run([
                    "-f", "lavfi", "-t", f"{gap:.6f}", "-i", "anullsrc=r=48000:cl=stereo",
                    "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", "-f", "nut",
                    "-progress", "pipe:1", "-nostats", "-y", str(silence),
                ], progress_parser=silence_progress, cancel_event=cancel_event)
                combined.append(silence)
                combined_durations.append(gap)
                created_gap_seconds += gap
                created_gap_count += 1
                fraction = min(1.0, created_gap_seconds / max(0.001, total_gap_seconds))
                self._report(
                    progress_callback, "Preparing audio", 0.53 + 0.03 * fraction,
                    f"Inserted {gap:.1f}s of silence",
                )
            combined.append(segment)
            combined_durations.append(track.duration_seconds)
            cursor = start + track.duration_seconds
        segments[:] = combined
        return combined_durations

    @staticmethod
    def _write_concat_file(
        path: Path, segments: list[Path], durations: list[float] | None = None,
    ) -> None:
        """Write an FFmpeg concat-demuxer manifest with safely quoted file paths."""
        def quote(segment: Path) -> str:
            return segment.resolve().as_posix().replace("'", "'\\''")

        lines = ["ffconcat version 1.0"]
        if durations is not None and len(durations) != len(segments):
            raise RenderError("Audio segment durations do not match the prepared files.")
        for index, segment in enumerate(segments):
            lines.append(f"file '{quote(segment)}'")
            if durations is not None:
                duration = durations[index]
                if duration <= 0.0:
                    raise RenderError("Audio segment duration must be greater than zero.")
                lines.append(f"duration {duration:.6f}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _write_visual_concat(path: Path, frames: list[tuple[Path, float]]) -> None:
        """Write an image concat manifest with exact still-frame durations."""
        def quote(frame: Path) -> str:
            return frame.resolve().as_posix().replace("'", "'\\''")

        lines = ["ffconcat version 1.0"]
        for frame, duration in frames:
            lines.append(f"file '{quote(frame)}'")
            lines.append(f"duration {duration:.6f}")
        if frames:
            lines.append(f"file '{quote(frames[-1][0])}'")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _run(self, arguments: list[str], progress_parser: Callable[[str], None] | None = None,
             cancel_event: threading.Event | None = None) -> None:
        """Run FFmpeg, forward machine progress, and terminate safely on cancellation."""
        command = [str(self.executable), "-hide_banner", "-loglevel", "error", *arguments]
        try:
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding="utf-8", errors="replace",
                **hidden_process_kwargs(),
            )
        except OSError as error:
            raise RenderError(f"Could not start FFmpeg: {error}") from error
        # FFmpeg can print several progress lines per encoded frame.  Keeping the
        # entire stream made long QHD/60 exports grow memory for hours, although
        # only the newest diagnostics are useful after a failure.
        stdout_lines: deque[str] = deque(maxlen=80)
        stderr_lines: deque[str] = deque(maxlen=400)

        def read_stream(stream: object, sink: deque[str]) -> None:
            for line in iter(stream.readline, ""):  # type: ignore[union-attr]
                sink.append(line)
                if sink is stdout_lines and progress_parser:
                    progress_parser(line.strip())

        stdout_thread = threading.Thread(target=read_stream, args=(process.stdout, stdout_lines), daemon=True)
        stderr_thread = threading.Thread(target=read_stream, args=(process.stderr, stderr_lines), daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        cancelled = False
        while process.poll() is None:
            if cancel_event and cancel_event.is_set():
                cancelled = True
                try:
                    process.terminate()
                except OSError:
                    pass
                try:
                    process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                    except OSError:
                        pass
                    try:
                        process.wait(timeout=3.0)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                break
            try:
                process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass
        stdout_thread.join(timeout=1.0)
        stderr_thread.join(timeout=1.0)
        for stream in (process.stdout, process.stderr):
            if stream is None or stream.closed:
                continue
            try:
                stream.close()
            except OSError:
                pass
        if stdout_thread.is_alive():
            stdout_thread.join(timeout=1.0)
        if stderr_thread.is_alive():
            stderr_thread.join(timeout=1.0)
        if cancelled:
            raise RenderCancelledError("Rendering was cancelled.")
        if process.returncode != 0:
            message = "".join(stderr_lines).strip() or "FFmpeg returned an unknown error."
            raise RenderError(message)

    @staticmethod
    def _report(callback: Callable[[str, float, str], None] | None, stage: str,
                fraction: float, message: str) -> None:
        if callback:
            callback(stage, min(1.0, max(0.0, fraction)), message)

    @staticmethod
    def _timeline_duration(tracks: list[PlaylistTrack]) -> float:
        cursor = 0.0
        for track in tracks:
            requested = track.start_time_seconds if track.start_time_seconds is not None else cursor
            start = max(cursor, requested)
            cursor = start + track.duration_seconds
        return cursor

    @staticmethod
    def _ffmetadata_escape(value: str) -> str:
        for character in ("\\", "=", ";", "#", "\n"):
            value = value.replace(character, "\\" + character)
        return value

    def _write_export_ffmetadata(
        self,
        temporary: Path,
        tracks: list[PlaylistTrack],
        metadata: "ExportMetadata",
        target: Path,
    ) -> Path | None:
        """Write an FFmetadata file with container tags and per-track chapters.

        Returns ``None`` when there is nothing worth embedding so the caller can
        skip the extra FFmpeg input entirely.
        """
        lines = [";FFMETADATA1"]
        title = metadata.title.strip() or target.stem
        if title:
            lines.append(f"title={self._ffmetadata_escape(title)}")
        if metadata.artist.strip():
            lines.append(f"artist={self._ffmetadata_escape(metadata.artist.strip())}")
        comment = metadata.comment.strip() or "Playlist Canvas"
        lines.append(f"comment={self._ffmetadata_escape(comment)}")

        chapters = metadata.include_chapters and len(tracks) > 1
        if chapters:
            windows = self._track_windows(tracks)
            total = self._timeline_duration(tracks)
            for index, (track, (start, _duration)) in enumerate(
                zip(tracks, windows)
            ):
                end = windows[index + 1][0] if index + 1 < len(windows) else total
                start_ms = max(0, round(start * 1000))
                end_ms = max(start_ms + 1, round(end * 1000))
                name = (track.title or track.filename or f"Track {index + 1}").strip()
                lines.extend([
                    "",
                    "[CHAPTER]",
                    "TIMEBASE=1/1000",
                    f"START={start_ms}",
                    f"END={end_ms}",
                    f"title={self._ffmetadata_escape(name)}",
                ])
        path = temporary / "metadata.ffmeta"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    @staticmethod
    def _track_windows(tracks: list[PlaylistTrack]) -> list[tuple[float, float]]:
        """Return sequenced global start/duration pairs for enabled tracks."""
        windows: list[tuple[float, float]] = []
        cursor = 0.0
        for track in tracks:
            requested = track.start_time_seconds if track.start_time_seconds is not None else cursor
            start = max(cursor, requested)
            windows.append((start, track.duration_seconds))
            cursor = start + track.duration_seconds
        return windows

    @staticmethod
    def _parse_progress_seconds(line: str) -> float | None:
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                return float(line.split("=", 1)[1]) / 1_000_000
            except ValueError:
                return None
        if line.startswith("out_time="):
            try:
                hours, minutes, seconds = line.split("=", 1)[1].split(":")
                return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
            except ValueError:
                return None
        return None

    @staticmethod
    def _timed_progress_message(
        action: str, seconds: float, total_seconds: float, fraction: float,
    ) -> str:
        """Build one consistent live FFmpeg progress detail."""
        return (
            f"{action} {seconds:.1f}s / {total_seconds:.1f}s"
            f" · {round(min(1.0, max(0.0, fraction)) * 100)}%"
        )
