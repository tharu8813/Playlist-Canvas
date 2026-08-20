"""Python-rendered, audio-reactive visualizer overlay generation."""

from __future__ import annotations

import math
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections.abc import Callable, Sequence
from pathlib import Path
from time import monotonic

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

from app.animation.curves import (
    ease_in_quint, ease_out_quint, hidden_scale_factor, slide_distance,
)
from app.utils.level_meter_painter import paint_level_meter
from app.utils.particle_painter import paint_particles
from app.utils.subprocess_utils import hidden_process_kwargs


class PythonVisualizerError(RuntimeError):
    """Raised when Python visualizer analysis or layer encoding fails."""


class PythonVisualizerRenderer:
    """Analyze the playlist audio in Python and encode transparent overlay movies."""

    sample_rate = 8_000
    fft_size = 512

    def __init__(self, ffmpeg_executable: Path) -> None:
        self.ffmpeg_executable = ffmpeg_executable

    def render_layers(
        self,
        audio_path: Path,
        overlays: Sequence[object],
        fps: int,
        directory: Path,
        cancel_event: threading.Event,
        progress_callback: Callable[[float, str], None] | None = None,
        track_windows: Sequence[tuple[float, float]] = (),
    ) -> list[Path]:
        """Create one alpha-preserving video layer for every configured source."""
        if not overlays:
            return []
        self._report(progress_callback, 0.0, "Decoding audio for visualizers")
        needs_stereo = any(getattr(item, "kind", "") == "level_meter" for item in overlays)
        stereo = self._decode_stereo_audio(audio_path, cancel_event) if needs_stereo else None
        samples = (
            np.mean(stereo, axis=1, dtype=np.float32)
            if stereo is not None else self._decode_mono_audio(audio_path, cancel_event)
        )
        self._report(progress_callback, 0.04, "Analyzing visualizer frequency levels")
        levels = self._analyze_levels(samples, fps, max(
            max(4, min(96, int(getattr(item, "bar_count", 32))))
            for item in overlays
        ), cancel_event)
        waveform_levels = None
        if any(getattr(item, "kind", "") == "waveform" for item in overlays):
            self._report(progress_callback, 0.08, "Analyzing waveform samples")
            waveform_levels = self._analyze_waveform(
                samples, fps, max(32, max(int(getattr(item, "bar_count", 32)) for item in overlays)), cancel_event
            )
        stereo_levels: tuple[np.ndarray, np.ndarray] | None = None
        if stereo is not None:
            self._report(progress_callback, 0.11, "Analyzing stereo level meter channels")
            stereo_levels = (
                self._analyze_rms(stereo[:, 0], fps, cancel_event),
                self._analyze_rms(stereo[:, 1], fps, cancel_event),
            )
        paths: list[Path | None] = [None] * len(overlays)
        encoding_started_at = monotonic()
        layer_progress = [0.0] * len(overlays)
        progress_lock = threading.Lock()

        def encode_layer(index: int, overlay: object) -> tuple[int, Path]:
            if cancel_event.is_set():
                raise PythonVisualizerError("Rendering was cancelled.")
            path = directory / f"python_visualizer_{index:02d}.mov"
            source_levels = waveform_levels if getattr(overlay, "kind", "") == "waveform" else levels
            if source_levels is None:
                source_levels = levels

            def report_layer(fraction: float, message: str) -> None:
                # _encode_layer reports its historical global fraction. Convert
                # that value back to one layer's completion and aggregate all
                # concurrent layers into a monotonic overall progress value.
                local = ((fraction - 0.15) / 0.85 * len(overlays)) - index
                with progress_lock:
                    layer_progress[index] = max(
                        layer_progress[index], min(1.0, max(0.0, local)),
                    )
                    aggregate = sum(layer_progress) / len(layer_progress)
                    self._report(
                        progress_callback, 0.15 + 0.85 * aggregate, message,
                    )

            self._encode_layer(
                path, overlay, source_levels, fps, cancel_event, report_layer,
                index, len(overlays), stereo_levels, encoding_started_at,
                track_windows,
            )
            return index, path

        worker_count = min(
            len(overlays), max(1, min(2, (os.cpu_count() or 2) // 2)),
        )
        self._report(
            progress_callback, 0.15,
            f"Preparing {len(overlays)} visualizer layer(s)",
        )
        with ThreadPoolExecutor(
            max_workers=worker_count, thread_name_prefix="visualizer-layer",
        ) as executor:
            futures = [
                executor.submit(encode_layer, index, overlay)
                for index, overlay in enumerate(overlays)
            ]
            for future in as_completed(futures):
                index, path = future.result()
                paths[index] = path
        self._report(progress_callback, 1.0, "Visualizer frames complete")
        return [path for path in paths if path is not None]

    @staticmethod
    def _report(
        callback: Callable[[float, str], None] | None, fraction: float, message: str,
    ) -> None:
        if callback is not None:
            callback(max(0.0, min(1.0, fraction)), message)

    def _decode_mono_audio(self, audio_path: Path, cancel_event: threading.Event) -> np.ndarray:
        """Decode compact PCM for analysis; FFmpeg does no visualizer rendering here."""
        values = self._decode_pcm(audio_path, 1, cancel_event)
        return values if len(values) else np.zeros(1, dtype=np.float32)

    def _decode_stereo_audio(self, audio_path: Path, cancel_event: threading.Event) -> np.ndarray:
        """Decode true left/right PCM through the same cancellable streaming path."""
        values = self._decode_pcm(audio_path, 2, cancel_event)
        if len(values) < 2:
            return np.zeros((1, 2), dtype=np.float32)
        return values[:len(values) // 2 * 2].reshape(-1, 2)

    def _decode_pcm(self, audio_path: Path, channels: int,
                    cancel_event: threading.Event) -> np.ndarray:
        """Stream compact PCM without an uncancellable full-output subprocess call."""
        command = [
            str(self.ffmpeg_executable), "-hide_banner", "-loglevel", "error", "-i", str(audio_path),
            "-vn", "-ac", str(channels), "-ar", str(self.sample_rate), "-f", "s16le", "-",
        ]
        try:
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                **hidden_process_kwargs(),
            )
        except OSError as error:
            raise PythonVisualizerError(f"Could not start FFmpeg audio decoding: {error}") from error
        raw = bytearray()
        assert process.stdout is not None
        while True:
            if cancel_event.is_set():
                self._stop_process(process)
                raise PythonVisualizerError("Rendering was cancelled.")
            # ``read`` may wait for the full requested byte count on Windows.
            # ``read1`` returns currently available pipe data, keeping preview
            # cancellation responsive even for long mono tracks.
            block = process.stdout.read1(1024 * 16)
            if not block:
                break
            raw.extend(block)
        stderr = process.stderr.read().decode("utf-8", "replace") if process.stderr else ""
        if process.wait() != 0:
            raise PythonVisualizerError(stderr.strip() or "Could not decode audio for the visualizer.")
        if not raw:
            return np.zeros(0, dtype=np.float32)
        return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0

    @staticmethod
    def _stop_process(process: subprocess.Popen[bytes]) -> None:
        """Terminate and reap a helper process, escalating only when necessary."""
        if process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

    def _analyze_rms(self, samples: np.ndarray, fps: int,
                     cancel_event: threading.Event) -> np.ndarray:
        """Return one actual channel RMS value per rendered frame."""
        frame_count = max(1, math.ceil(len(samples) * fps / self.sample_rate))
        result = np.zeros(frame_count, dtype=np.float32)
        for index in range(frame_count):
            if cancel_event.is_set():
                raise PythonVisualizerError("Rendering was cancelled.")
            start = round(index * self.sample_rate / fps)
            end = min(len(samples), round((index + 1) * self.sample_rate / fps))
            window = samples[start:end]
            result[index] = float(np.sqrt(np.mean(window * window))) if len(window) else 0.0
        return result

    def _analyze_waveform(self, samples: np.ndarray, fps: int, points: int,
                          cancel_event: threading.Event) -> np.ndarray:
        """Sample signed PCM windows for a genuine time-domain waveform."""
        frame_count = max(1, math.ceil(len(samples) * fps / self.sample_rate))
        result = np.zeros((frame_count, points), dtype=np.float32)
        for index in range(frame_count):
            if cancel_event.is_set():
                raise PythonVisualizerError("Rendering was cancelled.")
            start = round(index * self.sample_rate / fps)
            end = min(len(samples), round((index + 1) * self.sample_rate / fps))
            window = samples[start:end]
            if len(window) > 1:
                result[index] = np.interp(
                    np.linspace(0, len(window) - 1, points), np.arange(len(window)), window
                )
        return result

    def preview_levels(self, audio_path: Path, seconds: float, bands: int) -> np.ndarray:
        """Analyze a short real-audio window for an export-preview frame."""
        command = [
            str(self.ffmpeg_executable), "-hide_banner", "-loglevel", "error",
            "-ss", f"{max(0.0, seconds):.3f}", "-t", "0.18", "-i", str(audio_path),
            "-vn", "-ac", "1", "-ar", str(self.sample_rate), "-f", "s16le", "-",
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, check=False, timeout=10,
                **hidden_process_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired):
            # Full Preview must remain recoverable when a damaged/network audio
            # file or a broken FFmpeg process stops responding. The idle level
            # is deliberately nonzero so the source remains visible for repair.
            return np.full(max(4, bands), 0.08, dtype=np.float32)
        if completed.returncode != 0 or not completed.stdout:
            return np.full(max(4, bands), 0.08, dtype=np.float32)
        samples = np.frombuffer(completed.stdout, dtype="<i2").astype(np.float32) / 32768.0
        window = np.hanning(self.fft_size).astype(np.float32)
        segment = np.zeros(self.fft_size, dtype=np.float32)
        length = min(len(samples), self.fft_size)
        segment[:length] = samples[:length]
        frequencies = np.fft.rfftfreq(self.fft_size, 1 / self.sample_rate)
        edges = np.geomspace(35.0, self.sample_rate / 2, max(4, bands) + 1)
        spectrum = np.abs(np.fft.rfft(segment * window)) / (self.fft_size / 2)
        return np.array([
            float(np.max(spectrum[(frequencies >= edges[index]) & (frequencies < edges[index + 1])]))
            if np.any((frequencies >= edges[index]) & (frequencies < edges[index + 1])) else 0.0
            for index in range(len(edges) - 1)
        ], dtype=np.float32)

    @staticmethod
    def preview_image(width: int, height: int, overlay: object, levels: np.ndarray,
                      frame_index: int, processed: bool = False,
                      frame_rate: int = 60,
                      channel_values: tuple[float, ...] | None = None,
                      peak_values: tuple[float, ...] | None = None) -> QImage:
        """Create one overlay image with the same drawing rules used for export."""
        count = max(4, min(96, int(getattr(overlay, "bar_count", 32))))
        if processed:
            values = PythonVisualizerRenderer._resample_levels(levels, count)
        else:
            values = PythonVisualizerRenderer.process_level_sequence(
                np.asarray(levels, dtype=np.float32).reshape(1, -1), overlay, count,
            )[0]
        return PythonVisualizerRenderer._draw_frame(
            width, height, overlay, values, frame_index,
            channel_values=channel_values, frame_rate=frame_rate,
            peak_values=peak_values,
        )

    def _analyze_levels(self, samples: np.ndarray, fps: int, bands: int,
                        cancel_event: threading.Event) -> np.ndarray:
        """Return smooth logarithmic-frequency levels for every output frame."""
        frame_count = max(1, math.ceil(len(samples) * fps / self.sample_rate))
        window = np.hanning(self.fft_size).astype(np.float32)
        frequencies = np.fft.rfftfreq(self.fft_size, 1 / self.sample_rate)
        edges = np.geomspace(35.0, self.sample_rate / 2, bands + 1)
        bins = [np.flatnonzero((frequencies >= edges[index]) & (frequencies < edges[index + 1]))
                for index in range(bands)]
        result = np.zeros((frame_count, bands), dtype=np.float32)
        for frame_index in range(frame_count):
            if cancel_event.is_set():
                raise PythonVisualizerError("Rendering was cancelled.")
            center = int(frame_index * self.sample_rate / fps)
            start = center - self.fft_size // 2
            end = start + self.fft_size
            segment = np.zeros(self.fft_size, dtype=np.float32)
            source_start = max(0, start)
            source_end = min(len(samples), end)
            if source_end > source_start:
                target_start = source_start - start
                segment[target_start:target_start + source_end - source_start] = samples[source_start:source_end]
            # Normalize FFT magnitude by its window size.  Raw FFT values are much
            # larger than audible amplitude and previously saturated almost every
            # bar, making quiet passages look like a solid rectangle.
            spectrum = np.abs(np.fft.rfft(segment * window)) / (self.fft_size / 2)
            values = np.array([
                # Preserve a clear musical peak within each logarithmic band while
                # retaining the normalized amplitude ceiling below.
                float(np.max(spectrum[band])) if len(band) else 0.0 for band in bins
            ], dtype=np.float32)
            result[frame_index] = np.clip(values, 0.0, 2.0)
        return result

    def _encode_layer(
        self,
        path: Path,
        overlay: object,
        all_levels: np.ndarray,
        fps: int,
        cancel_event: threading.Event,
        progress_callback: Callable[[float, str], None] | None,
        overlay_index: int,
        overlay_count: int,
        stereo_levels: tuple[np.ndarray, np.ndarray] | None = None,
        encoding_started_at: float | None = None,
        track_windows: Sequence[tuple[float, float]] = (),
    ) -> None:
        """Stream local RGBA frames into an alpha-capable MOV file."""
        width = max(8, int(getattr(overlay, "width")))
        height = max(8, int(getattr(overlay, "height")))
        command = [
            str(self.ffmpeg_executable), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgba", "-video_size", f"{width}x{height}",
            "-framerate", str(fps), "-i", "-", "-an", "-c:v", "qtrle", "-pix_fmt", "argb", str(path),
        ]
        try:
            process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                **hidden_process_kwargs(),
            )
        except OSError as error:
            raise PythonVisualizerError(f"Could not start Python visualizer encoding: {error}") from error
        assert process.stdin is not None
        bar_count = max(4, min(96, int(getattr(overlay, "bar_count", 32))))
        processed_levels = (
            np.zeros((len(all_levels), 1), dtype=np.float32)
            if getattr(overlay, "kind", "") in {"particles", "level_meter"}
            else self.process_level_sequence(all_levels, overlay, bar_count)
        )
        meter_levels: np.ndarray | None = None
        meter_peaks: np.ndarray | None = None
        if getattr(overlay, "kind", "") == "level_meter":
            if stereo_levels is not None:
                raw_meter = np.column_stack(stereo_levels)
            else:
                raw_meter = np.mean(np.maximum(all_levels, 0.0), axis=1, keepdims=True)
            meter_levels, meter_peaks = self.process_meter_sequence(
                raw_meter, overlay, fps,
            )
        frame_buffer = QImage()
        progress_started_at = (
            encoding_started_at if encoding_started_at is not None else monotonic()
        )
        for frame_index, selected in enumerate(processed_levels):
            if cancel_event.is_set():
                self._stop_process(process)
                raise PythonVisualizerError("Rendering was cancelled.")
            channel_values = None
            peak_values = None
            if meter_levels is not None and meter_peaks is not None:
                meter_index = min(frame_index, len(meter_levels) - 1)
                channel_values = tuple(float(value) for value in meter_levels[meter_index])
                peak_values = tuple(float(value) for value in meter_peaks[meter_index])
            timeline_start = max(0.0, float(getattr(overlay, "timeline_start", 0.0)))
            timeline_duration = max(0.0, float(getattr(overlay, "timeline_duration", 0.0)))
            frame_seconds = frame_index / max(1, fps)
            visible = not (
                frame_seconds < timeline_start
                or (timeline_duration > 0.0
                    and frame_seconds >= timeline_start + timeline_duration)
            )
            if visible:
                frame_buffer = self._draw_frame(
                    width, height, overlay, selected, frame_index,
                    channel_values=channel_values, image_buffer=frame_buffer,
                    frame_rate=fps, peak_values=peak_values,
                )
                animation = self._animation_state(
                    frame_seconds, track_windows, overlay,
                )
                if animation is not None:
                    style, progress, entering = animation
                    frame_buffer = self._apply_animation(
                        frame_buffer, style, progress, entering,
                        float(getattr(overlay, "width", width)),
                        float(getattr(overlay, "height", height)),
                    )
            else:
                image_format = QImage.Format.Format_RGBA8888
                if (frame_buffer.width() != width or frame_buffer.height() != height
                        or frame_buffer.format() != image_format):
                    frame_buffer = QImage(width, height, image_format)
                frame_buffer.fill(0)
            try:
                process.stdin.write(frame_buffer.bits())
            except (BrokenPipeError, OSError) as error:
                self._stop_process(process)
                stderr = process.stderr.read().decode("utf-8", "replace") if process.stderr else ""
                raise PythonVisualizerError(
                    stderr.strip() or f"Visualizer encoder stopped unexpectedly: {error}"
                ) from error
            completed_in_layer = frame_index + 1
            if progress_callback and (
                completed_in_layer == 1
                or completed_in_layer % 12 == 0
                or completed_in_layer == len(processed_levels)
            ):
                raw_fraction = (
                    overlay_index + completed_in_layer / len(processed_levels)
                ) / overlay_count
                fraction = 0.15 + 0.85 * raw_fraction
                total_frames = len(processed_levels) * overlay_count
                completed_frames = overlay_index * len(processed_levels) + completed_in_layer
                elapsed = max(0.0, monotonic() - progress_started_at)
                progress_callback(
                    fraction,
                    self._frame_progress_message(
                        overlay_index + 1, overlay_count, completed_in_layer,
                        len(processed_levels), completed_frames, total_frames, elapsed,
                    ),
                )
        process.stdin.close()
        stderr = process.stderr.read().decode("utf-8", "replace") if process.stderr else ""
        if process.wait() != 0:
            raise PythonVisualizerError(stderr.strip() or "Could not encode the Python visualizer layer.")

    @staticmethod
    def _animation_state(
        seconds: float,
        track_windows: Sequence[tuple[float, float]],
        overlay: object,
    ) -> tuple[str, float, bool] | None:
        """Resolve one reactive source's per-track entrance or exit state."""
        if not track_windows:
            return None
        animation_in = str(getattr(overlay, "animation_in", "none"))
        animation_out = str(getattr(overlay, "animation_out", "none"))
        in_duration = max(0.001, float(getattr(overlay, "animation_in_duration", 0.45)))
        out_duration = max(0.001, float(getattr(overlay, "animation_out_duration", 0.45)))
        for index, (start, duration) in enumerate(track_windows):
            end = start + duration
            if seconds < start:
                if index == 0 and animation_in != "none":
                    return animation_in, 0.0, True
                if index > 0 and animation_out != "none":
                    return animation_out, 1.0, False
                return None
            if seconds < end:
                if animation_in != "none" and seconds < start + in_duration:
                    return animation_in, min(1.0, (seconds - start) / in_duration), True
                if animation_out != "none" and seconds >= end - out_duration:
                    return animation_out, min(1.0, (seconds - (end - out_duration)) / out_duration), False
                return None
        if animation_out != "none":
            return animation_out, 1.0, False
        return None

    @staticmethod
    def _apply_animation(
        image: QImage, style: str, raw_progress: float, entering: bool,
        source_width: float | None = None, source_height: float | None = None,
    ) -> QImage:
        """Apply Canvas-compatible opacity, slide, and zoom to a reactive layer."""
        raw_progress = max(0.0, min(1.0, raw_progress))
        if entering:
            visible_progress = ease_out_quint(raw_progress)
        else:
            visible_progress = 1.0 - ease_in_quint(raw_progress)
        if visible_progress <= 0.0:
            transparent = QImage(image.size(), QImage.Format.Format_RGBA8888)
            transparent.fill(0)
            return transparent
        result = QImage(image.size(), QImage.Format.Format_RGBA8888)
        result.fill(0)
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setOpacity(visible_progress)
        width = float(image.width())
        height = float(image.height())
        if style == "zoom":
            hidden_scale = hidden_scale_factor(style)
            scale = hidden_scale + (1.0 - hidden_scale) * visible_progress
            target_width = width * scale
            target_height = height * scale
            target = QRectF(
                (width - target_width) / 2.0,
                (height - target_height) / 2.0,
                target_width,
                target_height,
            )
        else:
            distance = slide_distance(
                source_width if source_width is not None else width,
                source_height if source_height is not None else height,
            )
            remaining = distance * (1.0 - visible_progress)
            dx, dy = {
                "slide_left": (-remaining, 0.0),
                "slide_right": (remaining, 0.0),
                "slide_up": (0.0, -remaining),
                "slide_down": (0.0, remaining),
            }.get(style, (0.0, 0.0))
            target = QRectF(dx, dy, width, height)
        painter.drawImage(target, image)
        painter.end()
        return result

    @staticmethod
    def _frame_progress_message(
        layer_number: int, layer_count: int, frame_number: int, layer_frames: int,
        completed_frames: int, total_frames: int, elapsed_seconds: float,
    ) -> str:
        """Build a detailed and stable progress description for the export dialog."""
        percent = 100.0 * completed_frames / max(1, total_frames)
        message = (
            f"Visualizer {layer_number}/{layer_count} · frame "
            f"{frame_number:,}/{layer_frames:,} · {percent:.1f}%"
        )
        if completed_frames >= 12 and elapsed_seconds > 0.2:
            rate = completed_frames / elapsed_seconds
            remaining = max(0.0, total_frames - completed_frames) / max(0.001, rate)
            message += f" · about {PythonVisualizerRenderer._format_duration(remaining)} remaining"
        return message

    @staticmethod
    def _format_duration(seconds: float) -> str:
        total = max(0, round(seconds))
        minutes, seconds_part = divmod(total, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:d}:{minutes:02d}:{seconds_part:02d}"
        return f"{minutes:02d}:{seconds_part:02d}"

    @staticmethod
    def _resample_levels(values: np.ndarray, count: int) -> np.ndarray:
        positions = np.linspace(0, len(values) - 1, count)
        return np.interp(positions, np.arange(len(values)), values)

    @staticmethod
    def process_level_sequence(all_levels: np.ndarray, overlay: object,
                               count: int) -> np.ndarray:
        """Apply gain, gate, dynamics, smoothing, and temporal response controls."""
        levels = np.asarray(all_levels, dtype=np.float32)
        if levels.ndim == 1:
            levels = levels.reshape(1, -1)
        count = max(4, min(96, int(count)))
        kind = str(getattr(overlay, "kind", "visualizer"))
        waveform = kind == "waveform"
        sensitivity = max(0.25, min(3.0, float(getattr(overlay, "sensitivity", 1.2))))
        gate = max(0.0, min(0.1, float(getattr(overlay, "noise_gate", 0.003))))
        minimum = max(0.0, min(0.5, float(getattr(overlay, "min_level", 0.0))))
        maximum = max(minimum, min(1.0, float(getattr(overlay, "max_level", 0.96))))
        curve = max(0.25, min(3.0, float(getattr(overlay, "curve", 0.9))))
        spatial = max(0.0, min(1.0, float(getattr(overlay, "smoothing", 0.18))))

        # Overall response remains a useful master control.  Attack and release
        # then independently shape upward and downward motion around its legacy
        # default of 0.22.
        reactivity = max(0.05, min(0.8, float(getattr(overlay, "reactivity", 0.22))))
        response_scale = reactivity / 0.22
        attack = max(0.01, min(1.0, float(getattr(overlay, "attack", 0.55))))
        release = max(0.01, min(1.0, float(getattr(overlay, "release", 0.16))))
        attack_alpha = 1.0 - (1.0 - attack) ** response_scale
        release_alpha = 1.0 - (1.0 - release) ** response_scale

        result = np.zeros((len(levels), count), dtype=np.float32)
        previous = np.zeros(count, dtype=np.float32)
        if not waveform:
            previous.fill(minimum)
        for frame_index, raw_values in enumerate(levels):
            values = PythonVisualizerRenderer._resample_levels(raw_values, count).astype(
                np.float32, copy=False,
            )
            values *= sensitivity
            values[np.abs(values) < gate] = 0.0
            if waveform:
                values = np.sign(values) * np.power(
                    np.clip(np.abs(values), 0.0, 1.0), curve,
                ) * maximum
            else:
                values = np.log1p(np.maximum(values, 0.0) * 30.0) / math.log(31.0)
                values = np.power(np.clip(values, 0.0, 1.0), curve)
                values = minimum + values * (maximum - minimum)
            if spatial > 0.0 and count > 2:
                padded = np.pad(values, (1, 1), mode="edge")
                neighbours = (padded[:-2] + 2.0 * padded[1:-1] + padded[2:]) / 4.0
                values = values * (1.0 - spatial) + neighbours * spatial
            rising = np.abs(values) > np.abs(previous) if waveform else values > previous
            alpha = np.where(rising, attack_alpha, release_alpha).astype(np.float32)
            previous = previous + (values - previous) * alpha
            result[frame_index] = previous
        return result

    @staticmethod
    def process_meter_sequence(raw_levels: np.ndarray, overlay: object,
                               fps: int) -> tuple[np.ndarray, np.ndarray]:
        """Apply true RMS gain, ballistics, and peak hold to meter channels."""
        raw = np.asarray(raw_levels, dtype=np.float32)
        if raw.ndim == 1:
            raw = raw.reshape(-1, 1)
        mode = str(getattr(overlay, "level_meter_mode", "stereo"))
        if mode == "mono" and raw.shape[1] > 1:
            raw = np.mean(raw, axis=1, keepdims=True)
        elif mode != "mono" and raw.shape[1] == 1:
            raw = np.repeat(raw, 2, axis=1)
        sensitivity = max(
            0.25, min(4.0, float(getattr(overlay, "level_meter_sensitivity", 1.2)))
        )
        minimum = max(
            0.0, min(0.5, float(getattr(overlay, "level_meter_min_level", 0.0)))
        )
        maximum = max(
            minimum, min(1.0, float(getattr(overlay, "level_meter_max_level", 1.0)))
        )
        target = np.sqrt(np.clip(raw * sensitivity * 1.8, 0.0, 1.0))
        target = minimum + target * (maximum - minimum)
        attack = max(0.01, min(1.0, float(getattr(overlay, "level_meter_attack", 0.65))))
        release = max(0.01, min(1.0, float(getattr(overlay, "level_meter_release", 0.18))))
        hold_frames = max(
            0, round(float(getattr(overlay, "level_meter_peak_hold", 0.35)) * max(1, fps))
        )
        peak_decay = max(
            0.05, min(3.0, float(getattr(overlay, "level_meter_peak_decay", 0.7)))
        ) / max(1, fps)
        levels = np.zeros_like(target, dtype=np.float32)
        peaks = np.zeros_like(target, dtype=np.float32)
        previous = np.full(target.shape[1], minimum, dtype=np.float32)
        peak = previous.copy()
        hold = np.zeros(target.shape[1], dtype=np.int32)
        for frame_index, values in enumerate(target):
            alpha = np.where(values > previous, attack, release).astype(np.float32)
            previous = previous + (values - previous) * alpha
            levels[frame_index] = previous
            raised = previous >= peak
            peak = np.where(raised, previous, peak)
            hold = np.where(raised, hold_frames, np.maximum(0, hold - 1))
            peak = np.where(hold > 0, peak, np.maximum(previous, peak - peak_decay))
            peaks[frame_index] = peak
        return levels, peaks

    @staticmethod
    def _draw_frame(width: int, height: int, overlay: object, levels: np.ndarray,
                    frame_index: int = 0, channel_values: tuple[float, ...] | None = None,
                    image_buffer: QImage | None = None, frame_rate: int = 60,
                    peak_values: tuple[float, ...] | None = None) -> QImage:
        image_format = QImage.Format.Format_RGBA8888
        if (image_buffer is not None and image_buffer.width() == width
                and image_buffer.height() == height and image_buffer.format() == image_format):
            image = image_buffer
        else:
            image = QImage(width, height, image_format)
        image.fill(0)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 0, width, height)
        color = QColor(str(getattr(overlay, "color", "#FFFFFF")))
        color.setAlphaF(max(0.0, min(1.0, float(getattr(overlay, "opacity", 1.0)))))
        style = str(getattr(overlay, "style", "bars"))
        kind = str(getattr(overlay, "kind", "visualizer"))
        effect_style = str(getattr(overlay, "effect_style", style))
        line_width = max(1.0, float(getattr(overlay, "line_width", 3.0)))
        count = len(levels)
        gap = max(1.0, rect.width() * 0.012 / count)
        bar_width = max(1.0, (rect.width() - gap * (count - 1)) / count)
        painter.setPen(QPen(color, line_width))
        if kind == "waveform":
            path = QPainterPath(QPointF(rect.left(), rect.center().y()))
            for index, level in enumerate(levels):
                x = rect.left() + index * rect.width() / max(1, count - 1)
                amplitude = float(level) * rect.height() * 0.45
                if effect_style == "filled":
                    y = rect.center().y() - amplitude
                else:
                    y = rect.center().y() - amplitude
                path.lineTo(x, y)
            if effect_style == "filled":
                path.lineTo(rect.right(), rect.bottom())
                path.lineTo(rect.left(), rect.bottom())
                path.closeSubpath()
                fill = QColor(color)
                fill.setAlpha(max(32, color.alpha() // 2))
                painter.fillPath(path, fill)
            painter.drawPath(path)
        elif kind == "level_meter":
            meter_values = channel_values or (
                (float(np.percentile(levels, 80)),)
                if str(getattr(overlay, "level_meter_mode", "stereo")) == "mono" else
                (float(np.percentile(levels[::2], 80)), float(np.percentile(levels[1::2], 80)))
            )
            painter.save()
            painter.setOpacity(max(
                0.0, min(1.0, float(getattr(overlay, "opacity", 1.0)))
            ))
            paint_level_meter(
                painter,
                rect,
                meter_values,
                peak_values,
                style=str(getattr(overlay, "level_meter_style", effect_style)),
                orientation=str(getattr(overlay, "level_meter_orientation", "vertical")),
                segments=int(getattr(overlay, "level_meter_segments", 16)),
                gap=float(getattr(overlay, "level_meter_gap", 4.0)),
                track_color=str(getattr(overlay, "level_meter_track_color", "#263244")),
                low_color=str(getattr(overlay, "level_meter_low_color", "#22C55E")),
                mid_color=str(getattr(overlay, "level_meter_mid_color", "#FACC15")),
                high_color=str(getattr(overlay, "level_meter_high_color", "#EF4444")),
                show_peak=bool(getattr(overlay, "level_meter_show_peak", True)),
            )
            painter.restore()
        elif kind == "particles":
            paint_particles(
                painter,
                rect,
                style=effect_style,
                color=str(getattr(overlay, "color", "#FFFFFF")),
                secondary_color=str(getattr(overlay, "particle_secondary_color", "#7DD3FC")),
                density=int(getattr(overlay, "density", 42)),
                speed=float(getattr(overlay, "speed", 1.0)),
                minimum_size=float(getattr(overlay, "particle_min_size", 1.0)),
                maximum_size=float(getattr(overlay, "particle_max_size", 4.0)),
                particle_opacity=(
                    float(getattr(overlay, "particle_opacity", 0.62))
                    * float(getattr(overlay, "opacity", 1.0))
                ),
                direction=float(getattr(overlay, "particle_direction", -90.0)),
                drift=float(getattr(overlay, "particle_drift", 0.25)),
                twinkle=float(getattr(overlay, "particle_twinkle", 0.25)),
                glow=float(getattr(overlay, "particle_glow", 0.2)),
                seed=int(getattr(overlay, "particle_seed", 17)),
                time_seconds=frame_index / max(1, frame_rate),
            )
        elif style in {"line", "wave"}:
            path = QPainterPath(QPointF(rect.left(), rect.center().y()))
            for index, level in enumerate(levels):
                x = rect.left() + index * rect.width() / max(1, count - 1)
                y = rect.center().y() - (float(level) - 0.5) * rect.height() * 0.82
                if style == "wave":
                    y = rect.center().y() - math.sin(index * 0.42) * float(level) * rect.height() * 0.36
                path.lineTo(x, y)
            painter.drawPath(path)
        elif style == "arc":
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for index, level in enumerate(levels):
                inset = index * min(rect.width(), rect.height()) / max(1, count * 3.2)
                arc_rect = rect.adjusted(inset, inset, -inset, -inset)
                arc_color = QColor(color)
                arc_color.setAlpha(max(45, int(color.alpha() * float(level))))
                painter.setPen(QPen(arc_color, max(1.0, line_width * 0.75)))
                painter.drawArc(arc_rect, 210 * 16, int(120 * 16 * float(level)))
        else:
            painter.setPen(QPen(color, 0))
            for index, level in enumerate(levels):
                value = float(level)
                x = rect.left() + index * (bar_width + gap)
                draw_color = QColor.fromHsv(int(300 * index / max(1, count - 1)), 210, 245) if style == "spectrum" else color
                painter.setBrush(draw_color)
                if style == "led":
                    segments = 8
                    segment_gap = max(1.0, rect.height() * 0.025)
                    segment_height = (rect.height() - segment_gap * (segments - 1)) / segments
                    active = max(1, round(value * segments))
                    for segment in range(active):
                        y = rect.bottom() - (segment + 1) * segment_height - segment * segment_gap
                        led_color = QColor(color)
                        led_color.setAlpha(max(80, color.alpha() - segment * 9))
                        painter.setBrush(led_color)
                        painter.drawRoundedRect(QRectF(x, y, bar_width, segment_height), 2, 2)
                elif style == "center":
                    bar_height = rect.height() * value
                    painter.drawRoundedRect(QRectF(x, rect.bottom() - bar_height, bar_width, bar_height), bar_width / 2, bar_width / 2)
                elif style == "mirror":
                    bar_height = rect.height() * value * 0.48
                    painter.drawRoundedRect(QRectF(x, rect.center().y() - bar_height, bar_width, bar_height), bar_width / 2, bar_width / 2)
                    painter.drawRoundedRect(QRectF(x, rect.center().y(), bar_width, bar_height), bar_width / 2, bar_width / 2)
                elif style == "dots":
                    dot_size = max(3.0, min(bar_width * 1.35, rect.height() * 0.16))
                    for dot_index in range(max(1, int(value * 7))):
                        y = rect.bottom() - dot_size - dot_index * (dot_size + 3)
                        painter.drawEllipse(QRectF(x, y, dot_size, dot_size))
                else:
                    bar_height = rect.height() * value
                    draw_width = bar_width * 0.62 if style == "capsule" else bar_width
                    radius = draw_width / 2
                    painter.drawRoundedRect(QRectF(x + (bar_width - draw_width) / 2, rect.center().y() - bar_height / 2, draw_width, bar_height), radius, radius)
        painter.end()
        return image
