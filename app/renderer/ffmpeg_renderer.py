"""FFmpeg-based static playlist video rendering pipeline."""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from collections import deque
from math import cos, radians, sin
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtGui import QImage

from app.models.playlist import PlaylistTrack
from app.renderer.python_visualizer import PythonVisualizerError, PythonVisualizerRenderer
from app.utils.subprocess_utils import hidden_process_kwargs


class FFmpegNotFoundError(RuntimeError):
    """Raised when no runnable FFmpeg executable can be found."""


class RenderError(RuntimeError):
    """Raised when FFmpeg rejects an input or cannot produce the video."""


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


@dataclass(frozen=True, slots=True)
class RenderResult:
    """Summary of a successfully generated video."""

    output_path: Path
    track_count: int


@dataclass(frozen=True, slots=True)
class RenderFrame:
    """One Canvas image and its exact on-screen duration."""

    image: QImage | Path
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class StaticOverlayLayer:
    """A transparent, time-synchronised Canvas Z band for compositing."""

    z_index: float
    frames: list[RenderFrame]


@dataclass(frozen=True, slots=True)
class VisualizerOverlay:
    """One Python-rendered dynamic overlay layer for export."""

    x: int
    y: int
    width: int
    height: int
    style: str
    color: str
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


class FFmpegRenderer:
    """Normalizes tracks, concatenates them with FFmpeg, then renders an MP4."""

    def __init__(self, executable: str | Path | None = None) -> None:
        self.executable = self.find_executable(executable)

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

    def render(self, image: QImage | list[QImage] | list[RenderFrame], tracks: list[PlaylistTrack], output_path: str | Path,
               settings: RenderSettings | None = None,
               progress_callback: Callable[[str, float, str], None] | None = None,
               cancel_event: threading.Event | None = None,
               visualizers: list[VisualizerOverlay] | None = None,
               static_layers: list[StaticOverlayLayer] | None = None) -> RenderResult:
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
        static_layers = static_layers or []
        supplied_frames = list(image) if isinstance(image, list) else [image]
        if not supplied_frames:
            raise RenderError("No Canvas frames were supplied for export.")
        explicit_frames = bool(supplied_frames and isinstance(supplied_frames[0], RenderFrame))
        if explicit_frames and not all(isinstance(frame, RenderFrame) for frame in supplied_frames):
            raise RenderError("Export frames must use one consistent frame format.")
        frames = [frame.image for frame in supplied_frames] if explicit_frames else supplied_frames
        durations = [frame.duration_seconds for frame in supplied_frames] if explicit_frames else []
        if explicit_frames and any(duration <= 0 for duration in durations):
            raise RenderError("Each Canvas frame duration must be greater than zero.")
        if not explicit_frames and len(frames) not in {1, len(active_tracks)}:
            raise RenderError("The number of Canvas frames does not match the enabled playlist tracks.")
        if len(frames) == 1 and len(active_tracks) > 1:
            frames *= len(active_tracks)
        self._validate_frames(frames)
        missing = [track.file_path for track in active_tracks if not Path(track.file_path).is_file()]
        if missing:
            raise RenderError(f"Audio file is missing: {missing[0]}")
        selected_settings = settings or RenderSettings()
        self._validate_settings(selected_settings)
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
            frame_paths: list[Path] = []
            for index, frame in enumerate(frames):
                if cancel_event.is_set():
                    raise RenderCancelledError("Rendering was cancelled.")
                if isinstance(frame, Path):
                    frame_path = frame.resolve()
                    if not frame_path.is_file():
                        raise RenderError(f"A staged export frame is missing: {frame_path}")
                else:
                    frame_path = temporary / f"canvas_{index:04d}.png"
                    if not frame.save(str(frame_path), "PNG"):
                        raise RenderError("Could not create a temporary Canvas image.")
                frame_paths.append(frame_path)
            visual_sequence = (
                list(zip(frame_paths, durations, strict=True))
                if explicit_frames else self._visual_sequence(active_tracks, frame_paths)
            )
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
            self._report(progress_callback, "Combining audio", 0.62, "Concatenating normalized tracks")
            self._run([
                "-f", "concat", "-safe", "0", "-i", str(concat_path),
                "-c:a", "aac", "-ar", "48000", "-ac", "2",
                "-b:a", selected_settings.audio_bitrate,
                "-movflags", "+faststart", str(audio_path),
            ], cancel_event=cancel_event)
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
            static_concat_paths: list[tuple[float, Path]] = []
            for layer_index, layer in enumerate(static_layers):
                if cancel_event.is_set():
                    raise RenderCancelledError("Rendering was cancelled.")
                if not layer.frames:
                    continue
                layer_images = [frame.image for frame in layer.frames]
                self._validate_frames(layer_images)
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
                layer_manifest = temporary / f"layer_{layer_index:02d}.ffconcat"
                self._write_visual_concat(
                    layer_manifest,
                    list(zip(layer_paths, [frame.duration_seconds for frame in layer.frames], strict=True)),
                )
                static_concat_paths.append((layer.z_index, layer_manifest))
            video_concat_path = temporary / "video.ffconcat"
            self._write_visual_concat(video_concat_path, visual_sequence)

            def encoding_progress(line: str) -> None:
                seconds = self._parse_progress_seconds(line)
                if seconds is not None and total_duration > 0:
                    fraction = min(1.0, seconds / total_duration)
                    self._report(
                        progress_callback, "Encoding video",
                        encoding_start + fraction * encoding_span,
                        f"Encoding {seconds:.1f}s / {total_duration:.1f}s",
                    )

            self._report(
                progress_callback, "Encoding video", encoding_start,
                "Rendering the final video",
            )
            temporary_video = temporary / "rendered_video.mp4"
            video_arguments = [
                # Let FFmpeg use all available CPU workers for PNG decoding,
                # filtering and software encoding. Hardware encoders ignore this
                # safely while their video encode stage runs on the GPU.
                # A single filter worker prevents QHD/60 FPS filter graphs from
                # retaining many full-resolution RGBA frames at once. Encoding
                # threads still use the available CPU cores (or the selected GPU).
                "-threads", "0", "-filter_threads", "1", "-filter_complex_threads", "1",
                "-f", "concat", "-safe", "0", "-i", str(video_concat_path),
                "-i", str(audio_path),
            ]
            for visualizer_path in visualizer_paths:
                video_arguments.extend(["-i", str(visualizer_path)])
            for _z_index, layer_manifest in static_concat_paths:
                video_arguments.extend(["-f", "concat", "-safe", "0", "-i", str(layer_manifest)])
            if visualizer_paths or static_concat_paths:
                video_arguments.extend([
                    "-filter_complex", self._layered_filter_graph(
                        visualizers, static_concat_paths, selected_settings.fps,
                        selected_settings.output_width, selected_settings.output_height,
                    ),
                    "-map", "[vout]", "-map", "1:a",
                ])
            else:
                video_arguments.extend([
                    "-vf", self._output_scaling_filter(selected_settings.fps, selected_settings.output_width,
                                                        selected_settings.output_height),
                ])
            video_arguments.extend([
                # Keep presentation timestamps strictly CFR.  The Canvas concat input is
                # intentionally variable-duration, while Python layers are FPS-based.
                "-fps_mode", "cfr", "-r", str(selected_settings.fps),
                "-c:v", selected_settings.video_codec,
                *self._video_encoding_arguments(selected_settings),
                "-c:a", "aac", "-b:a", selected_settings.audio_bitrate,
                "-pix_fmt", "yuv420p", "-shortest", "-movflags", "+faststart",
                "-progress", "pipe:1", "-nostats", "-y", str(temporary_video),
            ])
            self._run(video_arguments, progress_parser=encoding_progress, cancel_event=cancel_event)
            if cancel_event.is_set():
                raise RenderCancelledError("Rendering was cancelled.")
            try:
                temporary_video.replace(target)
            except OSError as error:
                raise RenderError(
                    "Could not replace the output video. Close any program using "
                    f"'{target.name}', check the destination folder, and try again."
                ) from error
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
                    f"{layer_input}rotate={radians_value:.12f}:ow=rotw(iw):oh=roth(ih){rotated_label}"
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
                              static_layers: list[tuple[float, Path]], fps: int,
                              output_width: int, output_height: int) -> str:
        """Interleave reactive video and transparent static Z bands in Canvas order."""
        graph: list[str] = [f"[0:v]fps={fps}:start_time=0,settb=AVTB,setpts=N/({fps}*TB)[base]"]
        entries: list[tuple[float, int, str]] = []
        # Inputs 0/1 are base canvas and audio. Dynamic video inputs precede
        # static concat inputs, preserving their independent source timing.
        entries.extend((overlay.z_index, index, "dynamic") for index, overlay in enumerate(visualizers))
        static_offset = 2 + len(visualizers)
        entries.extend((z_index, index, "static") for index, (z_index, _path) in enumerate(static_layers))
        current = "[base]"
        for order, (_z_value, index, kind) in enumerate(sorted(entries, key=lambda entry: (entry[0], entry[1], entry[2]))):
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
                    graph.append(f"{layer_input}rotate={angle:.12f}:ow=rotw(iw):oh=roth(ih){rotated}")
                    layer_input = rotated
                graph.append(f"{current}{layer_input}overlay={x}:{y}:eof_action=pass{output}")
            else:
                layer_input = f"[{static_offset + index}:v]"
                graph.append(f"{current}{layer_input}overlay=0:0:eof_action=pass{output}")
            current = output
        graph.append(f"{current}{FFmpegRenderer._output_scaling_filter(fps, output_width, output_height, include_fps=False)}[vout]")
        return ";".join(graph)

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
    def _validate_frames(frames: list[QImage | Path]) -> None:
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
                image = QImage(str(normalized))
            else:
                image = frame
            if image.isNull():
                raise RenderError("The export canvas frame is empty.")
            size = image.size()
            if isinstance(frame, Path):
                path_size_cache[normalized] = size
            return size

        reference_size = frame_size(frames[0])
        for index, frame in enumerate(frames[1:], start=2):
            if frame_size(frame) != reference_size:
                raise RenderError(
                    "All export frames must use one canvas size. "
                    f"Frame {index} does not match the first canvas frame."
                )

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
        static_layers: list[StaticOverlayLayer],
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
            if not layer.frames:
                continue
            validate(
                f"Static overlay layer {index}",
                [frame.duration_seconds for frame in layer.frames],
            )

    def ensure_encoder_available(self, encoder: str) -> None:
        """Fail early when the active FFmpeg build lacks the selected encoder."""
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
            raise RenderError("Could not inspect the available FFmpeg encoders.") from error
        if completed.returncode != 0 or encoder not in completed.stdout.split():
            raise RenderError(
                f"The selected video encoder '{encoder}' is unavailable in this FFmpeg build. "
                "Choose a supported CPU/GPU encoder in Settings."
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

    def _normalize_audio(self, tracks: list[PlaylistTrack], directory: Path,
                         settings: RenderSettings,
                         progress_callback: Callable[[str, float, str], None] | None,
                         cancel_event: threading.Event) -> list[Path]:
        """Normalize every track to an exact-duration lossless segment."""
        segments: list[Path] = []
        for index, track in enumerate(tracks):
            self._report(
                progress_callback, "Preparing audio", 0.05 + 0.48 * index / len(tracks),
                f"Normalizing {track.filename}",
            )
            output = directory / f"track_{index:04d}.nut"
            duration = f"{track.duration_seconds:.6f}"
            self._run([
                "-threads", "0", "-i", track.file_path, "-vn", "-map", "0:a:0",
                "-af", f"apad=whole_dur={duration}", "-t", duration,
                "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", "-f", "nut",
                "-y", str(output),
            ], cancel_event=cancel_event)
            segments.append(output)
        self._report(progress_callback, "Preparing audio", 0.53, "Audio normalization complete")
        return segments

    def _insert_silence_for_gaps(self, tracks: list[PlaylistTrack], segments: list[Path],
                                 directory: Path, settings: RenderSettings,
                                 progress_callback: Callable[[str, float, str], None] | None,
                                 cancel_event: threading.Event) -> list[float]:
        """Insert lossless silence segments for user-defined timeline gaps."""
        if not any(track.start_time_seconds is not None for track in tracks):
            return [track.duration_seconds for track in tracks]
        combined: list[Path] = []
        combined_durations: list[float] = []
        cursor = 0.0
        for index, (track, segment) in enumerate(zip(tracks, segments, strict=True)):
            requested = track.start_time_seconds if track.start_time_seconds is not None else cursor
            start = max(cursor, requested)
            gap = max(0.0, start - cursor)
            if gap > 0.001:
                silence = directory / f"silence_{index:04d}.nut"
                self._run([
                    "-f", "lavfi", "-t", f"{gap:.6f}", "-i", "anullsrc=r=48000:cl=stereo",
                    "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", "-f", "nut",
                    "-y", str(silence),
                ], cancel_event=cancel_event)
                combined.append(silence)
                combined_durations.append(gap)
                self._report(progress_callback, "Preparing audio", 0.56,
                             f"Inserted {gap:.1f}s of silence")
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
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
                break
            try:
                process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)
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
